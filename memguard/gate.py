"""Write gate: turns a judge verdict into commit / ask a human / refuse, and logs every decision.

``allow`` commits, ``flag`` goes to a human (the adapter asks inline or stages the write for
later review), ``block`` refuses it. Every decision (allow included) goes to ``decisions.jsonl``,
and the human's answer to a flag is appended there as a review record, so a run can be scored
afterwards (including how often a flag was a false alarm). A flag nobody could review is also
kept in ``quarantine.jsonl``.

The same judge runs again when memory is read back (``check_recall``), so an entry that reached
disk without passing the write check (hand edit, another writer, an older rule set) is caught
before the agent sees it. ``ledger.jsonl`` caches judge verdicts by content hash and rules
version, nothing else: human answers are logged, never cached, because whoever wrote or approved
an entry cannot vouch for it to another user of the same memory.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

from memguard.judge import LLM, RULES_VERSION, Verdict, judge_entry


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
        decision = self._judge(content, store, action)
        self._cache(content, decision)
        return decision

    def check_recall(self, content: str, *, store: str, use_cache: bool = True) -> Verdict:
        """Judge verdict for an entry being read back. With ``use_cache`` a verdict made under the
        current rules is reused: it depends only on the text, never on who wrote or approved it.
        A judge error is not cached (the entry is held now and judged again next time)."""
        if use_cache and (cached := self.lookup(content)) is not None:
            return cached
        decision = self._judge(content, store, "recall")
        self._cache(content, decision)
        return decision.verdict

    def lookup(self, content: str) -> Optional[Verdict]:
        """Cached judge verdict for ``content`` under the current rules."""
        record = self._current(self._read("ledger.jsonl")).get(_key(content))
        return Verdict(**record["verdict"]) if record else None

    def held(self) -> list:
        """Cached entries the judge holds (flag or block) under the current rules, newest first."""
        return sorted((r for r in self._current(self._read("ledger.jsonl")).values()
                       if r["verdict"]["verdict"] != "allow"), key=lambda r: -r["at"])

    def record_review(self, decision: Decision, outcome: str, *, pending_id: Optional[str] = None) -> None:
        """Log what happened to a flagged write: ``approved``, ``denied``, ``staged`` or ``unreviewed``.
        Logged only: a writer's approval never vouches for the entry to a later reader."""
        self._append("decisions.jsonl", {"review_of": decision.id, "outcome": outcome, "pending_id": pending_id,
                                         "at": time.time()})
        if outcome == "unreviewed":
            self._append("quarantine.jsonl", {**asdict(decision), "verdict": asdict(decision.verdict),
                                               "at": time.time()})

    def record_recall_review(self, content: str, store: str, outcome: str, session: str) -> None:
        """Log a reader's answer to a flagged recall (``approved``, ``denied``, ``unreviewed``)."""
        self._append("decisions.jsonl", {"recall_review": _key(content), "store": store, "outcome": outcome,
                                         "session": session, "at": time.time()})

    @staticmethod
    def _current(records: list) -> dict:
        """key -> latest ledger record judged under the current rules."""
        return {r["key"]: r for r in records if r.get("rules") == RULES_VERSION}

    def _judge(self, content: str, store: str, action: str) -> Decision:
        started = time.monotonic()
        verdict = judge_entry(content, self._llm)
        decision = Decision(uuid.uuid4().hex[:8], store, action, content, verdict,
                            round(time.monotonic() - started, 2))
        self._append("decisions.jsonl", {**asdict(decision), "verdict": asdict(verdict), "at": time.time()})
        return decision

    def _cache(self, content: str, decision: Decision) -> None:
        if not decision.verdict.error:
            self._append("ledger.jsonl", {"key": _key(content), "rules": RULES_VERSION, "verdict": asdict(decision.verdict),
                                          "decision_id": decision.id, "content": content, "at": time.time()})

    def _read(self, name: str) -> list:
        path = self._dir / name
        if not path.exists():
            return []
        with self._lock, open(path, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    def _append(self, name: str, record: dict) -> None:
        with self._lock:
            self._dir.mkdir(parents=True, exist_ok=True)
            with open(self._dir / name, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _key(content: str) -> str:
    return hashlib.sha256(content.strip().encode("utf-8")).hexdigest()[:16]


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
