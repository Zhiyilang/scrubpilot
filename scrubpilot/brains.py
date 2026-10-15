"""Brains: the pluggable decision layer.

Two implementations, one interface:

- OfflineBrain   — deterministic policy. Reads the profile, proposes the next
                   whitelisted fix whose target issue actually exists. No
                   network, no key, converges in a fixed number of steps.
- OpenAIBrain    — asks any OpenAI-compatible endpoint to propose the next fix.
                   The endpoint can only *suggest*: the harness verifier has
                   the final word. Any failure (unreachable, malformed JSON,
                   out-of-whitelist proposal) degrades to the offline policy
                   and is recorded as proposed_by="offline-fallback".

A brain is ~40 lines. That is the point of this project: you can read the
entire decision layer of your agent in one sitting, and swap it.
"""

from __future__ import annotations

import json
import os
import urllib.request
from typing import List, Optional

from .harness import Action
from .tools_csv import FIX_WHITELIST

# fix -> the profile issue that justifies proposing it
_FIX_TRIGGERS = {
    "trim_whitespace": {"header_whitespace", "cell_whitespace"},
    "drop_empty_rows": {"empty_rows"},
    "drop_duplicate_rows": {"duplicate_rows"},
    "normalize_dates": {"mixed_date_formats"},
    "normalize_amounts": {"messy_amounts"},
    "snake_case_headers": {"header_not_snake_case"},
    "drop_all_empty_column": {"all_empty_column"},
}

# deterministic preference order (cheap structural fixes first)
_FIX_ORDER = [
    "trim_whitespace",
    "drop_empty_rows",
    "drop_duplicate_rows",
    "snake_case_headers",
    "normalize_dates",
    "normalize_amounts",
    "drop_all_empty_column",
]


class Brain:
    name = "abstract"

    def perceive(self, task: str, context: dict) -> dict:
        raise NotImplementedError

    def plan(self, observation: dict) -> Optional[Action]:
        raise NotImplementedError


def _fix_justified(fix: str, issues: List[str]) -> bool:
    """True when the profile still shows the issue this fix targets.

    Both brains use this: it stops a brain from wasting a proposal (and
    collecting a rejection) on a fix whose work is already done.
    """
    triggers = _FIX_TRIGGERS[fix]
    if any(i in triggers for i in issues):
        return True
    if fix == "drop_all_empty_column":
        return any(i.startswith("all_empty_column") for i in issues)
    return False


class OfflineBrain(Brain):
    """Deterministic policy over the profile. ~30 lines, fully testable."""

    name = "offline-deterministic"

    def perceive(self, task: str, context: dict) -> dict:
        from .tools_csv import profile_csv
        profile = profile_csv(context)
        return {
            "profile": profile,
            "ok": profile["ok"],
            "summary": profile["summary"],
            "text": context.get("text", ""),
        }

    def plan(self, observation: dict) -> Optional[Action]:
        issues = list(observation.get("profile", {}).get("issues", []))
        for fix in _FIX_ORDER:
            if _fix_justified(fix, issues):
                return Action(
                    tool="apply_fix",
                    args={"fix": fix},
                    reason=f"profile shows {sorted(i for i in issues if i in _FIX_TRIGGERS[fix])}",
                    proposed_by=self.name,
                )
        return None  # nothing justified left to do


_LLM_SYSTEM = (
    "You are ScrubPilot's planner. You are shown a CSV profile and a sample of "
    "the file. Propose exactly ONE next cleaning step.\n"
    "Respond with JSON only: {\"fix\": \"<fix name>\", \"reason\": \"<max 12 words>\"} "
    "or {\"fix\": null} when the profile reports no issues.\n"
    f"Allowed fix names: {', '.join(FIX_WHITELIST)}.\n"
    "Anything else will be rejected by the harness verifier."
)


class OpenAIBrain(Brain):
    """Proposes fixes via any OpenAI-compatible chat endpoint.

    Degrades to the offline policy on any failure; the degradation is visible
    in the receipt via proposed_by.
    """

    name = "openai-compatible"

    def __init__(self, base_url: str, api_key: str, model: str, timeout: int = 20):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.fallback = OfflineBrain()
        self.degraded = False

    def perceive(self, task: str, context: dict) -> dict:
        return self.fallback.perceive(task, context)

    def plan(self, observation: dict) -> Optional[Action]:
        profile = observation.get("profile", {})
        issues = list(profile.get("issues", []))
        if profile.get("ok"):
            return None
        sample = _sample(observation.get("text", ""))
        rejection = observation.get("last_rejection", "")
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": _LLM_SYSTEM},
                {"role": "user", "content":
                    f"Profile: {json.dumps(profile, ensure_ascii=False)}\n"
                    f"Sample rows:\n{sample}"
                    + (f"\nPrevious proposal was rejected: {rejection}" if rejection else "")},
            ],
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            content = data["choices"][0]["message"]["content"]
            proposal = json.loads(content)
            fix = proposal.get("fix")
            if fix is None:
                return None
            if fix not in FIX_WHITELIST:
                raise ValueError(f"out-of-whitelist proposal: {fix}")
            if not _fix_justified(fix, issues):
                # the model proposed work that is already done — do not waste
                # a proposal on a guaranteed no-op rejection
                action = self.fallback.plan(observation)
                if action is not None:
                    action.proposed_by = "offline-fallback"
                    action.reason = (
                        f"llm proposed {fix} but that issue is already resolved"
                    )
                return action
            self.degraded = False
            return Action(
                tool="apply_fix",
                args={"fix": fix},
                reason=str(proposal.get("reason", ""))[:120] or "llm proposal",
                proposed_by=self.name,
            )
        except Exception:
            # degrade, visibly
            self.degraded = True
            action = self.fallback.plan(observation)
            if action is not None:
                action.proposed_by = "offline-fallback"
                action.reason = f"{action.reason} (llm unavailable)"
            return action


def _sample(text: str, max_rows: int = 6) -> str:
    lines = [ln for ln in text.splitlines() if ln.strip()][:max_rows]
    return "\n".join(ln[:200] for ln in lines)


def get_brain():
    """OpenAI-compatible brain if fully configured, else the offline brain."""
    base = os.environ.get("SCRUBPILOT_LLM_BASE_URL", "").strip()
    key = os.environ.get("SCRUBPILOT_LLM_API_KEY", "").strip()
    model = os.environ.get("SCRUBPILOT_LLM_MODEL", "").strip()
    if base and key and model:
        return OpenAIBrain(base, key, model)
    return OfflineBrain()
