"""Policy: turns the judge verdict, stamp, conflict and allowlist results into commit / stage / block.

A pure function with no I/O, so it can be tested without an agent. Verdicts are cached by
content; the policy runs again on every write because the stamp differs per write.

Stand-in until M6 (cpy): the verdict alone decides, as before the policy step existed
(allow -> commit, flag -> stage, block -> block). A judge error is already a flag (fail-closed).
"""

from __future__ import annotations

from typing import Optional

from memguard.judge import Verdict
from memguard.types import AllowlistResult, ConflictResult, PolicyDecision, Stamp

_BY_VERDICT = {"allow": ("commit", "R-allow"), "flag": ("stage", "R-flag"), "block": ("block", "R-block")}


def decide(verdict: Verdict, stamp: Optional[Stamp], conflict: ConflictResult, allow: AllowlistResult,
           *, store: str, action: str) -> PolicyDecision:
    outcome, rule = _BY_VERDICT[verdict.verdict]
    return PolicyDecision(outcome, rule, verdict.reason)
