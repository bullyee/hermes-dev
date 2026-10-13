"""memguard: an agent-agnostic defense layer against ASI06 memory and context poisoning.

The layer never imports an agent framework. Agents plug in through small adapters
that supply an ``llm(system, user) -> str`` callable and call the gate at their
memory write and recall points.
"""

from memguard.judge import CATEGORIES, VERDICTS, Verdict, judge_entry

__all__ = ["CATEGORIES", "VERDICTS", "Verdict", "judge_entry"]
