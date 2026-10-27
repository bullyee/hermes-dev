"""E4 config builder and pre-batch check; no Hermes, no model.

    python3 -m pytest evalcfg -q
"""

import os

import pytest
import yaml

from evalcfg import build_config as bc
from evalcfg import check_env


def test_arms_differ_only_by_the_defense():
    stock, _, _ = bc.build("stock", "e4b")
    defense, _, _ = bc.build("defense", "e4b")
    shared = {k for k in stock.keys() | defense.keys() if k not in ("memguard", "plugins")}
    assert all(stock.get(k) == defense.get(k) for k in shared)
    assert stock["plugins"]["hermes-memory-store"] == defense["plugins"]["hermes-memory-store"]
    assert stock["memguard"] == {"enabled": False} and defense["memguard"]["enabled"] is True
    assert stock["plugins"]["enabled"] == [] and "agcp_guard" in defense["plugins"]["enabled"]


def test_base_settings_both_arms():
    for arm in ("stock", "defense"):
        config, _, _ = bc.build(arm, "e4b")
        assert config["agent"]["disabled_toolsets"] == ["session_search"]
        assert config["auxiliary"]["title_generation"]["enabled"] is False
        assert config["memory"]["provider"] == "holographic"
        assert config["plugins"]["hermes-memory-store"]["auto_extract"] is False
        assert config["approvals"]["unattended_mode"] == "approve"
        defer = config["tools"]["tool_search"]["defer"]
        assert "cronjob_manage" not in defer and {"session_search", "process_manage", "todo_list"} <= set(defer)


def test_scenario_list_replaces_base_list():
    config, _, _ = bc.build("defense", "e4b", "asi06-2")
    assert config["agent"]["disabled_toolsets"] == ["session_search", "terminal", "file", "code_execution"]


def test_set_parses_yaml_values():
    config, text, _ = bc.build("defense", "e4b", sets=["model.base_url=http://example.invalid:11414/v1",
                                                       "memguard.timeout=45"])
    assert config["model"]["base_url"] == "http://example.invalid:11414/v1"
    assert config["memguard"]["timeout"] == 45
    assert "# set: memguard.timeout=45" in text
    with pytest.raises(ValueError):
        bc.parse_set("no-equals-sign")


def test_todo_refused_unless_allowed():
    with pytest.raises(ValueError, match="model.base_url"):
        bc.build("stock", "cloud")
    config, _, _ = bc.build("stock", "cloud", allow_todo=True)
    assert config["model"]["default"] == "TODO"


def test_hash_is_stable(tmp_path):
    _, text, digest = bc.build("defense", "e4b")
    assert digest == bc.build("defense", "e4b")[2]
    assert digest != bc.build("stock", "e4b")[2]
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    assert check_env.read_config(path) == ([], digest)
    assert yaml.safe_load(text)["memguard"]["enabled"] is True


def test_check_config_catches_edits_and_wrong_arm(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(bc.build("defense", "e4b", sets=["memguard.timeout=45"])[1], encoding="utf-8")
    _, _, problems = check_env.check_config([path], "defense", "e4b", None)
    assert problems == []
    assert check_env.check_config([path], "stock", "e4b", None)[2]
    assert check_env.check_config([path], "defense", "e4b", "asi06-2")[2]
    path.write_text(path.read_text(encoding="utf-8").replace("timeout: 45", "timeout: 60"), encoding="utf-8")
    assert check_env.check_config([path], "defense", "e4b", None)[2]
    assert "missing" in check_env.check_config([tmp_path / "nope.yaml"], "defense", "e4b", None)[2][0]


def test_check_restart(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text("x", encoding="utf-8")
    assert check_env.check_restart(tmp_path, [config])[1]
    os.utime(config, (1000, 1000))
    (tmp_path / "gateway-starts.log").write_text("900.5\n2000.0\n", encoding="utf-8")
    assert check_env.check_restart(tmp_path, [config]) == (2000.0, [])
    os.utime(config, (3000, 3000))
    assert "restart" in check_env.check_restart(tmp_path, [config])[1][0]


def test_check_model_digest(monkeypatch):
    replies = {"/api/version": {"version": "0.40.8"},
               "/api/tags": {"models": [{"name": "gemma4:e4b", "digest": "dc35e8d9c606" + "0" * 52},
                                        {"name": "gemma4:26b", "digest": "5571076f3d70" + "0" * 52}]},
               "/api/show": {"parameters": "temperature                    1\ntop_k   64\ntop_p  0.95\nstop  \"<eos>\""}}
    monkeypatch.setattr(check_env, "_get", lambda url, body=None: replies[url[url.index("/api/"):]])
    summary, problems = check_env.check_model("agent", "http://h:11414/v1", "gemma4:e4b")
    assert problems == [] and summary == "agent gemma4:e4b dc35e8d9c606 (Ollama 0.40.8; temperature 1 top_k 64 top_p 0.95)"
    assert check_env.check_model("judge", "http://h:11414/v1", "gemma4:e4b", "temperature 0")[0].endswith("(Ollama 0.40.8; temperature 0)")
    assert "expected 001e5dafc3c7" in check_env.check_model("agent", "http://h:11414/v1", "gemma4:26b")[1][0]
    assert check_env.check_model("agent", "https://cloud.invalid/v1", "some-cloud-model") == ("agent some-cloud-model (no digest)", [])


def test_check_model_unreachable():
    _, problems = check_env.check_model("judge", "http://127.0.0.1:9/v1", "gemma4:e4b")
    assert "cannot reach Ollama" in problems[0]


def test_no_plugins_needs_no_container():
    assert check_env.check_plugins("no-such-container", []) == []
