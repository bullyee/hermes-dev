"""Run manifest: one JSON record per evaluation run, written by the red-team wrapper (E3).

    python3 evalcfg/manifest.py --config merged.yaml --arm defense --container hermes-austin \
        --scenario ASI06-1A --trial 3 --code-commit <git sha in the container> \
        [--redteam-commit <sha>] [--hermes-home /path/to/data-dir] [--session S1 --session S2] \
        [--out runs/<run_id>.json]

What it records, so two runs can be shown to differ only by the arm:
- the merged config (sha256 of its body, as build_config.py printed it) and its sources;
- the agent model and the memguard judge model: Ollama version, digest, default sampling
  parameters (Hermes sends no temperature, so these are what the run used); for a non-Ollama
  endpoint only the URL and model name;
- the code: git commit inside the container, red-team script commit;
- the data dir: sha256 of SOUL.md and the installed skill / plugin / profile names, because they
  change the system prompt and must match across arms.

Stdlib + PyYAML only (team server: Python 3.8).
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import re
import sys
import urllib.request
import uuid
from pathlib import Path
from typing import List, Optional

import yaml

SCHEMA = "asi-eval.run.v1"


def _get(url: str, body: Optional[dict] = None, timeout: float = 10) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def model_info(base_url: str, model: str) -> dict:
    """Ollama facts for ``model`` behind an OpenAI-compatible ``base_url`` (``.../v1``)."""
    info = {"base_url": base_url, "model": model}
    root = re.sub(r"/v1/?$", "", base_url or "")
    try:
        info["ollama_version"] = _get(f"{root}/api/version").get("version")
        tags = {m["name"]: m for m in _get(f"{root}/api/tags").get("models", [])}
        entry = tags.get(model) or tags.get(f"{model}:latest")
        info["digest"] = entry["digest"] if entry else None
        show = _get(f"{root}/api/show", {"model": model})
        info["parameters"] = show.get("parameters")
        info["modified_at"] = show.get("modified_at")
    except Exception as exc:  # cloud endpoint, or Ollama unreachable: say so, never guess
        info["error"] = f"not an Ollama endpoint or unreachable: {exc}"
    return info


def config_facts(config_path: Path) -> dict:
    text = config_path.read_text(encoding="utf-8")
    header = dict(re.findall(r"^# (sources|config_sha256): (.*)$", text, re.M))
    body = "".join(line for line in text.splitlines(keepends=True) if not line.startswith("#"))
    return {"path": str(config_path), "sources": header.get("sources"),
            "config_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
            "header_sha256": header.get("config_sha256"), "settings": yaml.safe_load(body) or {}}


def data_dir_facts(home: Path) -> dict:
    def names(sub: str) -> List[str]:
        d = home / sub
        return sorted(p.name for p in d.iterdir() if not p.name.startswith(".")) if d.is_dir() else []
    soul = home / "SOUL.md"
    return {"hermes_home": str(home),
            "soul_sha256": hashlib.sha256(soul.read_bytes()).hexdigest() if soul.is_file() else None,
            "skills": names("skills"), "plugins": names("plugins"), "profiles": names("profiles")}


def build_manifest(args: argparse.Namespace) -> dict:
    config = config_facts(Path(args.config))
    settings = config.pop("settings")
    agent = settings.get("model", {})
    judge = settings.get("memguard", {}) if settings.get("memguard", {}).get("enabled") else {}
    return {
        "schema": SCHEMA,
        "run_id": args.run_id or uuid.uuid4().hex[:12],
        "at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "arm": args.arm,
        "container": args.container,
        "scenario": args.scenario,
        "trial": args.trial,
        "sessions": args.session,
        "config": config,
        "agent_model": model_info(agent.get("base_url", ""), agent.get("default", "")),
        "judge_model": (model_info(judge.get("base_url") or agent.get("base_url", ""),
                                   judge.get("model") or agent.get("default", "")) if judge else None),
        "code_commit": args.code_commit,
        "redteam_commit": args.redteam_commit,
        "data_dir": data_dir_facts(Path(args.hermes_home)) if args.hermes_home else None,
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", required=True, help="the merged config.yaml from build_config.py")
    parser.add_argument("--arm", required=True, choices=["stock", "defense"])
    parser.add_argument("--container", required=True)
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--trial", type=int, required=True)
    parser.add_argument("--code-commit", required=True, help="git commit of the Hermes code in the container")
    parser.add_argument("--redteam-commit")
    parser.add_argument("--hermes-home", help="the container's data dir as seen from where this runs")
    parser.add_argument("--session", action="append", default=[], help="session id used by the run (repeat)")
    parser.add_argument("--run-id")
    parser.add_argument("--out", help="write here instead of stdout")
    args = parser.parse_args(argv)
    text = json.dumps(build_manifest(args), ensure_ascii=False, indent=1)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
