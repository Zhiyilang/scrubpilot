"""Agent wiring: tools + verifier + observe + done -> scrub().

The verifier is the heart of the safety story. It is a plain deterministic
function — no model involved — and it can reject any proposal:

  1. fix must be on the whitelist
  2. the fix must actually change something (no-ops rejected)
  3. the fix may not destroy mass amounts of data (>=60% cell loss rejected)
  4. issues must not increase (regressions rejected)
  5. drop_all_empty_column requires an all-empty column to exist
"""

from __future__ import annotations

import csv
import io
from typing import Optional

from .brains import Brain, get_brain
from .harness import Action, Harness
from .tools_csv import (
    FIX_WHITELIST,
    apply_fix,
    cell_count,
    profile_csv,
    verify_csv,
)


def _cell_count(text: str) -> int:
    return cell_count(text)


def _sample(text: str, max_rows: int = 6) -> str:
    lines = [ln for ln in text.splitlines() if ln.strip()][:max_rows]
    return "\n".join(ln[:200] for ln in lines)


def make_verifier():
    def verify(action: Action, before: dict, after: dict) -> tuple:
        fix = action.args.get("fix", "")
        if fix not in FIX_WHITELIST:
            return False, f"fix not on the whitelist: {fix}"
        if before["text"] == after["text"]:
            return False, "no-op: the fix changed nothing"
        cells_before = _cell_count(before["text"])
        cells_after = _cell_count(after["text"])
        if cells_after < cells_before * 0.4:
            return False, (
                f"mass-destruction: {cells_before} cells -> {cells_after} "
                f"(>60% loss)"
            )
        issues_before = len(profile_csv(before)["issues"])
        issues_after = len(profile_csv(after)["issues"])
        if issues_after > issues_before:
            return False, (
                f"regression: issues {issues_before} -> {issues_after}"
            )
        if fix == "drop_all_empty_column":
            if not any(
                i.startswith("all_empty_column")
                for i in profile_csv(before)["issues"]
            ):
                return False, "no all-empty column exists"
        return True, (
            f"ok: issues {issues_before} -> {issues_after}, "
            f"cells {cells_before} -> {cells_after}"
        )
    return verify


def make_observe():
    def observe(state: dict) -> dict:
        profile = profile_csv(state)
        return {
            "profile": profile,
            "ok": profile["ok"],
            "summary": profile["summary"],
            "text": state.get("text", ""),
            "sample": _sample(state.get("text", "")),
        }
    return observe


def _done(observation: dict) -> bool:
    return bool(observation.get("ok"))


def make_agent(brain: Optional[Brain] = None) -> Harness:
    brain = brain or get_brain()
    tools = {
        "profile_csv": lambda state, **kw: {**state, "profile": profile_csv(state)},
        "apply_fix": apply_fix,
        "verify_csv": lambda state, **kw: {**state, "profile": verify_csv(state)},
    }
    return Harness(
        brain=brain,
        tools=tools,
        verifier=make_verifier(),
        observe=make_observe(),
        done=_done,
        max_steps=14,
    )


def scrub(text: str, brain: Optional[Brain] = None) -> dict:
    """Clean a CSV. Returns {"receipt": ..., "cleaned": ..., "profile_before": ...}."""
    agent = make_agent(brain)
    receipt = agent.run("scrub this csv", {"text": text})
    profile_before = profile_csv({"text": text})
    profile_after = profile_csv({"text": receipt.final_state.get("text", text)})
    return {
        "receipt": receipt.to_dict(),
        "cleaned": receipt.final_state.get("text", text),
        "profile_before": profile_before,
        "profile_after": profile_after,
        "outcome": receipt.outcome,
        "rejected_actions": receipt.rejected_actions,
    }
