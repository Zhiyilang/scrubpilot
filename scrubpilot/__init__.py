"""ScrubPilot — a local-first agent harness you can read in one sitting.

The whole point: an agent framework where nothing is hidden. The loop is six
stages, the brains are two small classes, the tools are plain functions, and
every decision the agent makes is recorded in a receipt with a reason.

The safety property that matters: **the brain can only propose.** Every
proposed action passes a deterministic verifier before it is applied. A brain
that hallucinates a tool, invents a destructive fix, or proposes a no-op gets
rejected — and the rejection is a first-class event in the receipt.

Stages:
    PERCEIVE -> PLAN -> ACT -> OBSERVE -> VERIFY -> RECEIPT
"""

from .harness import Action, Harness, Receipt, Step
from .brains import Brain, OfflineBrain, OpenAIBrain, get_brain
from .tools_csv import (
    FIX_WHITELIST,
    apply_fix,
    profile_csv,
    verify_csv,
)
from .agent import scrub

__all__ = [
    "Action", "Harness", "Receipt", "Step",
    "Brain", "OfflineBrain", "OpenAIBrain", "get_brain",
    "FIX_WHITELIST", "apply_fix", "profile_csv", "verify_csv",
    "scrub",
]

__version__ = "1.0.0"
