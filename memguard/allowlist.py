"""Allowlist check: URLs and accounts in a candidate entry that the baseline doesn't list.

Stand-in until M8 (cpy): nothing is reported. The real check is plain code, never an LLM, so an
injected entry cannot talk its way past it.
"""

from __future__ import annotations

from memguard.baseline import Baseline
from memguard.types import AllowlistResult


def check(content: str, baseline: Baseline) -> AllowlistResult:
    return AllowlistResult(())
