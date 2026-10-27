"""E4 config builder and run manifest; no Hermes, no model.

    python3 -m pytest evalcfg -q
"""

import argparse
import json

import pytest
import yaml

from evalcfg import build_config as bc
from evalcfg import manifest


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


def test_scenario_list_replaces_base_list():
    config, _, _ = bc.build("defense", "e4b", "asi06-2")
    assert config["agent"]["disabled_toolsets"] == ["session_search", "terminal", "file", "code_execution"]


def test_set_parses_yaml_values():
    config, text, _ = bc.build("defense", "e4b", sets=["model.base_url=http://host.docker.internal:11414/v1",
                                                       "memguard.timeout=45"])
    assert config["model"]["base_url"] == "http://host.docker.internal:11414/v1"
    assert config["memguard"]["timeout"] == 45
    assert "# set: memguard.timeout=45" in text
    with pytest.raises(ValueError):
        bc.parse_set("no-equals-sign")


def test_todo_refused_unless_allowed():
    with pytest.raises(ValueError, match="model.base_url"):
        bc.build("stock", "cloud")
    config, _, _ = bc.build("stock", "cloud", allow_todo=True)
    assert config["model"]["default"] == "TODO"


def test_hash_is_stable_and_matches_manifest(tmp_path):
    _, text, digest = bc.build("defense", "e4b")
    assert digest == bc.build("defense", "e4b")[2]
    assert digest != bc.build("stock", "e4b")[2]
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    facts = manifest.config_facts(path)
    assert facts["config_sha256"] == facts["header_sha256"] == digest
    assert yaml.safe_load(text)["memguard"]["enabled"] is True


def test_manifest_records_unreachable_model_without_guessing(tmp_path):
    _, text, _ = bc.build("defense", "e4b", sets=["model.base_url=http://127.0.0.1:9/v1",
                                                  "memguard.base_url=http://127.0.0.1:9/v1"])
    (tmp_path / "config.yaml").write_text(text, encoding="utf-8")
    home = tmp_path / "home"
    (home / "skills" / "demo").mkdir(parents=True)
    (home / "SOUL.md").write_text("soul", encoding="utf-8")
    args = argparse.Namespace(config=str(tmp_path / "config.yaml"), arm="defense", container="c", scenario="ASI06-1A",
                              trial=1, code_commit="abc", redteam_commit=None, hermes_home=str(home),
                              session=["s1"], run_id="r1")
    record = manifest.build_manifest(args)
    json.dumps(record)
    assert record["schema"] == manifest.SCHEMA and record["run_id"] == "r1"
    assert "error" in record["agent_model"] and "digest" not in record["agent_model"]
    assert record["judge_model"]["model"] == "gemma4:e4b"
    assert record["data_dir"]["skills"] == ["demo"] and record["data_dir"]["soul_sha256"]
