"""Vetoes after the judge (M1): an abandoned tool call and the blue team's agcp.taint.v1 note block a
write whatever the verdict; the ids they key on come from the context Hermes binds around a tool call.

    python3 -m pytest memguard/tests -q
"""

import json
from types import SimpleNamespace

import pytest

from memguard.adapters import hermes
from memguard.gate import MemGuard
from memguard.types import PolicyDecision
from plugins.memguard_hooks import _on_post_tool_call
from tools.approval_context import (_approval_session_id, _approval_tool_call_id, _approval_turn_id,
                                    reset_current_observability_context, set_current_observability_context)

ALLOW = '{"verdict": "allow", "category": "none", "reason": "ok"}'


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(hermes, "_settings", lambda: {"enabled": True})
    monkeypatch.setattr(hermes, "_guard", lambda llm=None: MemGuard(lambda system, user: ALLOW, tmp_path / "memguard"))
    monkeypatch.setattr(hermes, "_abandoned", set())
    return tmp_path


@pytest.fixture
def call(home):
    tokens = set_current_observability_context(turn_id="t1", tool_call_id="call_1", session_id="api-0123abcd")
    yield
    reset_current_observability_context(tokens)


def taint(home, name):
    (home / "agcp" / "taint").mkdir(parents=True, exist_ok=True)
    (home / "agcp" / "taint" / name).write_text('{"schema": "agcp.taint.v1"}', encoding="utf-8")


def logged(home):
    return [json.loads(line) for line in (home / "memguard" / "decisions.jsonl").read_text(encoding="utf-8").splitlines()]


def test_veto_overrides_the_policy_and_both_are_logged(tmp_path):
    veto = PolicyDecision("block", "R-taint-note", "藍隊污染註記")
    decision = MemGuard(lambda system, user: ALLOW, tmp_path).check_write("x", store="memory", action="add",
                                                                         veto=lambda: veto)
    assert (decision.outcome, decision.reason) == ("block", "藍隊污染註記")
    record = json.loads((tmp_path / "decisions.jsonl").read_text(encoding="utf-8"))
    assert (record["policy"]["outcome"], record["veto"]["rule"]) == ("commit", "R-taint-note")


def test_clean_session_writes(home, call):
    assert hermes.guard_write("memory", "add", ["user prefers tea"]) is None
    stamp = logged(home)[0]["stamp"]
    assert (stamp["session_id"], stamp["turn_id"]) == ("api-0123abcd", "t1")


def test_blue_team_note_blocks_the_session(home, call):
    taint(home, "api-0123abcd.json")
    refused = json.loads(hermes.guard_write("holographic", "add", ["user prefers tea"]))
    assert refused["memguard"]["rule"] == "R-taint-note"
    assert logged(home)[0]["veto"]["reason"].startswith("藍隊污染註記")


def test_note_for_an_unsafe_session_id_is_hashed(home):
    # Test vector from the agcp.taint.v1 spec.
    taint(home, "~h-c14cddc033f64b9dea80ea675cf280a015e67251.json")
    assert hermes._blue_team_tainted("a/b") and not hermes._blue_team_tainted("a_b")


def test_unreadable_note_counts_as_tainted(home):
    (home / "agcp" / "taint" / "s1.json").mkdir(parents=True)  # opening it raises IsADirectoryError
    assert hermes._blue_team_tainted("s1")


def test_abandoned_call_is_blocked(home, call):
    _on_post_tool_call(tool_name="memory", tool_call_id="call_1", status="cancelled")
    _on_post_tool_call(tool_name="memory", tool_call_id="call_2", status="ok")
    refused = json.loads(hermes.guard_write("memory", "add", ["user prefers tea"]))
    assert refused["memguard"]["rule"] == "R-abandoned"
    assert hermes._abandoned == {"call_1"}


def test_tool_runs_with_its_call_ids():
    from agent.tool_executor import _ToolCallRef, _execute_with_call_ids
    agent = SimpleNamespace(session_id="api-0123abcd", _current_turn_id="t1")
    ref = _ToolCallRef("fact_store", {}, "task", "call_1", [])
    seen = _execute_with_call_ids(agent, ref, lambda args: (_approval_session_id.get(), _approval_turn_id.get(),
                                                            _approval_tool_call_id.get()))
    assert seen == ("api-0123abcd", "t1", "call_1")
    assert _approval_tool_call_id.get() == ""
