"""Write gate: turns a judge verdict into commit / ask a human / refuse, and logs every decision.

``allow`` commits, ``flag`` goes to a human (the adapter asks inline or stages the write for
later review), ``block`` refuses it. Every decision (allow included) goes to ``decisions.jsonl``,
and the human's answer to a flag is appended there as a review record, so a run can be scored
afterwards (including how often a flag was a false alarm). A flag nobody could review is also
kept in ``quarantine.jsonl``.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

from memguard.judge import LLM, Verdict, judge_entry


@dataclass(frozen=True)
class Decision:
    id: str
    store: str
    action: str
    content: str
    verdict: Verdict
    seconds: float

    @property
    def commits(self) -> bool:
        return self.verdict.verdict == "allow"


class MemGuard:
    def __init__(self, llm: LLM, state_dir: Path):
        self._llm = llm
        self._dir = Path(state_dir)
        self._lock = threading.Lock()

    def check_write(self, content: str, *, store: str, action: str) -> Decision:
        started = time.monotonic()
        verdict = judge_entry(content, self._llm)
        decision = Decision(uuid.uuid4().hex[:8], store, action, content, verdict,
                            round(time.monotonic() - started, 2))
        record = {**asdict(decision), "verdict": asdict(verdict), "at": time.time()}
        self._append("decisions.jsonl", record)
        return decision

    def record_review(self, decision: Decision, outcome: str, *, pending_id: Optional[str] = None) -> None:
        """Log what happened to a flagged write: ``approved``, ``denied``, ``staged`` or ``unreviewed``."""
        record = {"review_of": decision.id, "outcome": outcome, "pending_id": pending_id, "at": time.time()}
        self._append("decisions.jsonl", record)
        if outcome == "unreviewed":
            self._append("quarantine.jsonl", {**asdict(decision), "verdict": asdict(decision.verdict),
                                               "at": time.time()})

    def _append(self, name: str, record: dict) -> None:
        with self._lock:
            self._dir.mkdir(parents=True, exist_ok=True)
            with open(self._dir / name, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")


def refusal_message(decision: Decision, review: Optional[str] = None) -> Optional[str]:
    """Model-facing text for a write that did not commit, or None when it may proceed.
    ``review`` is the human's answer to a flag (see ``MemGuard.record_review``)."""
    v = decision.verdict
    if v.verdict == "block":
        return f"memguard blocked this memory write ({v.category}): {v.reason} Nothing was saved."
    if v.verdict == "flag":
        if review == "approved":
            return None
        if review == "denied":
            return (f"memguard flagged this memory write ({v.category}) and the user declined to save it. "
                    f"Nothing was saved.")
        return (f"memguard held this memory write for human review ({v.category}): {v.reason} "
                f"No reviewer was available, so nothing was saved (id {decision.id}).")
    return None
