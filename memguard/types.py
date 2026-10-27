"""Data passed between the write gate's steps: stamp -> judge -> conflict -> allowlist -> policy.

Shared by the gate (austin) and the policy, conflict, allowlist and baseline modules (cpy); change
a field only after both agree (research/ASI06_防禦內部介面與分工.html). Agent-agnostic: an adapter
fills ``Stamp`` from its framework, the gate never does.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

OUTCOMES = ("commit", "stage", "block")


@dataclass(frozen=True)
class Stamp:
    """Where a write comes from. The policy reads it on every write; verdicts never depend on it."""
    principal: str                  # profile the write lands in (Identity: principal -> profile)
    session_id: str
    turn_id: Optional[str]
    origin: str                     # "user_turn" | "background_review" | "subagent" | "cron"
    delegated_from: Optional[str]   # parent session when a subagent wrote it
    tainted: bool                   # this turn read outside content (web, file, email, subagent output)
    taint_sources: Tuple[str, ...]  # the tools that made it tainted, e.g. ("web_extract",)
    user_confirmed: bool            # the user explicitly confirmed this write


@dataclass(frozen=True)
class ConflictResult:
    status: str                     # "consistent" | "contradicts" | "unrelated" | "error"
    fact_ids: Tuple[str, ...]       # baseline facts it was compared with
    reason: str
    baseline_version: str


@dataclass(frozen=True)
class AllowlistResult:
    unknown: Tuple[str, ...]        # URLs and accounts in the entry that the baseline doesn't list


@dataclass(frozen=True)
class PolicyDecision:
    outcome: str                    # one of OUTCOMES
    rule: str                       # id of the rule that fired, e.g. "R-conflict"
    reason: str
