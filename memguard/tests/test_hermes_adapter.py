"""Judge calls through Hermes (memguard.model pins the judge, E6) with a fake call_llm; no agent needed.

    python3 -m pytest memguard/tests -q
"""

import sys
import types

import pytest

from memguard.adapters import hermes
from memguard.judge import judge_entry

REPLY = '{"verdict": "allow", "category": "none", "reason": "ok"}'


@pytest.fixture
def call_llm(monkeypatch):
    """Stands in for agent.auxiliary_client.call_llm. Like Hermes, it reports the model that answered in
    route_info: the requested one, the agent's when none was requested, or ``answered_by``."""
    def fake(*, route_info=None, **kwargs):
        fake.calls.append(kwargs)
        if route_info is not None:
            route_info["model"] = fake.answered_by or kwargs.get("model") or "agent-model"
        return REPLY

    fake.calls, fake.answered_by = [], None
    module = types.ModuleType("agent.auxiliary_client")
    module.call_llm, module.extract_content_or_reasoning = fake, lambda response: response
    monkeypatch.setitem(sys.modules, "agent.auxiliary_client", module)
    return fake


def use_settings(monkeypatch, **settings):
    monkeypatch.setattr(hermes, "_settings", lambda: settings)


def test_judge_uses_its_own_model_and_endpoint(monkeypatch, call_llm):
    use_settings(monkeypatch, model="gemma4:e4b", base_url="http://judge/v1")
    assert hermes._hermes_llm("system", "entry") == REPLY
    assert (call_llm.calls[0].get("model"), call_llm.calls[0]["base_url"]) == ("gemma4:e4b", "http://judge/v1")


def test_judge_follows_the_agent_without_its_own_model(monkeypatch, call_llm):
    use_settings(monkeypatch)
    assert hermes._hermes_llm("system", "entry") == REPLY
    assert (call_llm.calls[0].get("model"), call_llm.calls[0]["base_url"]) == (None, None)


def test_verdict_from_another_model_holds_the_entry(monkeypatch, call_llm):
    # Hermes's fallback after the judge's endpoint times out or refuses: the agent's model answers.
    use_settings(monkeypatch, model="gemma4:e4b", base_url="http://judge/v1")
    call_llm.answered_by = "gemma4:26b"
    verdict = judge_entry("x", hermes._hermes_llm)
    assert (verdict.verdict, verdict.error) == ("flag", True)
    assert "gemma4:26b" in verdict.reason
