"""Conflict check: does a candidate entry contradict the baseline facts?

Stand-in until M7 (cpy): every entry is ``unrelated``. The real check compares the entry with the
top-k related baseline facts through ``llm``, caches by content and ``baseline.version``, and
returns ``error`` instead of raising.
"""

from __future__ import annotations

from memguard.baseline import Baseline
from memguard.judge import LLM
from memguard.types import ConflictResult


def check(content: str, baseline: Baseline, llm: LLM) -> ConflictResult:
    return ConflictResult("unrelated", (), "", baseline.version)
