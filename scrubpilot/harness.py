"""The harness: PERCEIVE -> PLAN -> ACT -> OBSERVE -> VERIFY -> RECEIPT.

Deliberately small. If you can read this file you understand the whole agent.

Contract with brains:
    perceive(task, context) -> observation dict
    plan(observation)       -> Action | None (None means "done")

The observation dict may carry `last_rejection` (the reason the previous
action was rejected) so a brain can adapt — or ignore it and stay
deterministic, like the offline brain does.

Contract with the verifier (a plain function):
    verify(action, before_state, after_state) -> (ok: bool, reason: str)

Contract with tools:
    tools[name](state, **args) -> new_state
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple


@dataclass
class Action:
    """A proposed step. `reason` is the brain's justification — always recorded."""
    tool: str
    args: dict
    reason: str = ""
    proposed_by: str = "unknown"


@dataclass
class Step:
    stage: str                       # PERCEIVE / PLAN / ACT / OBSERVE / VERIFY / RECEIPT
    detail: str
    ms: float = 0.0
    action: Optional[Action] = None
    accepted: Optional[bool] = None  # VERIFY stages only
    rejection_reason: str = ""


@dataclass
class Receipt:
    task: str
    steps: List[Step] = field(default_factory=list)
    final_state: dict = field(default_factory=dict)
    brain: str = ""
    outcome: str = ""                # "clean" | "stopped" | "budget-exhausted"
    rejected_actions: int = 0

    def to_dict(self) -> dict:
        out = []
        for s in self.steps:
            entry = {"stage": s.stage, "detail": s.detail, "ms": round(s.ms, 1)}
            if s.action is not None:
                entry["action"] = {
                    "tool": s.action.tool,
                    "args": s.action.args,
                    "reason": s.action.reason,
                    "proposed_by": s.action.proposed_by,
                }
            if s.accepted is not None:
                entry["accepted"] = s.accepted
            if s.rejection_reason:
                entry["rejection_reason"] = s.rejection_reason
            out.append(entry)
        return {
            "task": self.task,
            "brain": self.brain,
            "outcome": self.outcome,
            "rejected_actions": self.rejected_actions,
            "steps": out,
            "final_state": self.final_state,
        }


class Harness:
    """Runs the loop. Never raises because of a bad brain proposal — a bad
    proposal is data (a rejected step), not a crash."""

    def __init__(
        self,
        brain,
        tools: Dict[str, Callable],
        verifier: Callable[[Action, dict, dict], Tuple[bool, str]],
        observe: Callable[[dict], dict],
        done: Callable[[dict], bool],
        max_steps: int = 12,
    ):
        self.brain = brain
        self.tools = tools
        self.verifier = verifier
        self.observe = observe
        self.done = done
        self.max_steps = max_steps

    def run(self, task: str, state: dict) -> Receipt:
        receipt = Receipt(task=task, brain=getattr(self.brain, "name", "unknown"))

        t0 = time.perf_counter()
        observation = self.brain.perceive(task, state)
        receipt.steps.append(Step(
            "PERCEIVE",
            f"brain {receipt.brain}: {observation.get('summary', '')}",
            (time.perf_counter() - t0) * 1000,
        ))

        exhausted = True
        for _ in range(self.max_steps):
            # PLAN --------------------------------------------------------
            t0 = time.perf_counter()
            action = self.brain.plan(observation)
            plan_ms = (time.perf_counter() - t0) * 1000
            if action is None:
                receipt.steps.append(Step(
                    "PLAN", "brain declared the task done", plan_ms
                ))
                receipt.outcome = "clean" if observation.get("ok") else "stopped"
                exhausted = False
                break
            receipt.steps.append(Step(
                "PLAN",
                f"propose {action.tool}({action.args}) — {action.reason}",
                plan_ms,
                action=action,
            ))

            # ACT ---------------------------------------------------------
            t0 = time.perf_counter()
            before = dict(state)
            if action.tool not in self.tools:
                ok, reason = False, f"unknown tool: {action.tool}"
                receipt.steps.append(Step(
                    "ACT", f"tool {action.tool!r} does not exist",
                    (time.perf_counter() - t0) * 1000,
                ))
            else:
                try:
                    state = self.tools[action.tool](state, **action.args)
                    ok, reason = self.verifier(action, before, state)
                    receipt.steps.append(Step(
                        "ACT", f"{action.tool} applied (proposed by {action.proposed_by})",
                        (time.perf_counter() - t0) * 1000,
                    ))
                except Exception as exc:  # a tool that raises is a rejected action
                    state = before
                    ok, reason = False, f"tool raised {type(exc).__name__}: {exc}"
                    receipt.steps.append(Step(
                        "ACT", f"{action.tool} raised {type(exc).__name__}",
                        (time.perf_counter() - t0) * 1000,
                    ))

            # VERIFY ------------------------------------------------------
            t0 = time.perf_counter()
            if ok:
                receipt.steps.append(Step(
                    "VERIFY", reason or "accepted", (time.perf_counter() - t0) * 1000,
                    accepted=True,
                ))
            else:
                receipt.rejected_actions += 1
                receipt.steps.append(Step(
                    "VERIFY", f"rejected: {reason}", (time.perf_counter() - t0) * 1000,
                    accepted=False, rejection_reason=reason,
                ))

            # OBSERVE -----------------------------------------------------
            t0 = time.perf_counter()
            observation = self.observe(state)
            if not ok:
                observation["last_rejection"] = reason
            receipt.steps.append(Step(
                "OBSERVE", observation.get("summary", ""), (time.perf_counter() - t0) * 1000
            ))

            if self.done(observation):
                receipt.outcome = "clean"
                exhausted = False
                break

        if exhausted:
            receipt.outcome = "budget-exhausted"
        receipt.final_state = observation
        return receipt
