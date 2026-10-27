"""Write pipeline (judge -> conflict -> allowlist -> policy) with a fake judge; no agent needed.

    python3 -m pytest memguard/tests -q
"""

import json

import pytest

import memguard.policy
from memguard.gate import MemGuard, refusal_message
from memguard.types import Stamp

STAMP = Stamp(principal="alice", session_id="s1", turn_id="t1", origin="user_turn", delegated_from=None,
              tainted=False, taint_sources=(), user_confirmed=False)


def judge_says(verdict):
    return lambda system, user: json.dumps({"verdict": verdict, "category": "exfiltration", "reason": "sends data out"})


@pytest.mark.parametrize("verdict, outcome", [("allow", "commit"), ("flag", "stage"), ("block", "block")])
def test_policy_stand_in_keeps_verdict_mapping(tmp_path, verdict, outcome):
    decision = MemGuard(judge_says(verdict), tmp_path).check_write("x", store="memory", action="add", stamp=STAMP)
    assert decision.outcome == outcome
    assert decision.commits is (outcome == "commit")


def test_one_log_line_per_write_with_every_step(tmp_path):
    MemGuard(judge_says("flag"), tmp_path).check_write("x", store="memory", action="add", stamp=STAMP)
    lines = (tmp_path / "decisions.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["stamp"]["principal"] == "alice"
    assert record["conflict"]["status"] == "unrelated"
    assert record["allowlist"] == {"unknown": []}
    assert record["policy"] == {"outcome": "stage", "rule": "R-flag", "reason": "sends data out"}


def test_policy_failure_stages_the_write(tmp_path, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("boom")
    monkeypatch.setattr(memguard.policy, "decide", broken)
    decision = MemGuard(judge_says("allow"), tmp_path).check_write("x", store="memory", action="add", stamp=STAMP)
    assert (decision.outcome, decision.policy.rule) == ("stage", "R-error")


def test_refusal_message_follows_outcome(tmp_path):
    guard = MemGuard(judge_says("block"), tmp_path)
    assert "blocked" in refusal_message(guard.check_write("x", store="memory", action="add"))
    staged = MemGuard(judge_says("flag"), tmp_path).check_write("y", store="memory", action="add")
    assert refusal_message(staged, "approved") is None
    assert "held this memory write" in refusal_message(staged)


def test_recall_logs_verdict_only(tmp_path):
    MemGuard(judge_says("allow"), tmp_path).check_recall("x", store="memory")
    record = json.loads((tmp_path / "decisions.jsonl").read_text(encoding="utf-8"))
    assert record["action"] == "recall" and record["policy"] is None
