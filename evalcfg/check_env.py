"""Pre-batch check: run on the team server before each batch (one arm x model x scenario, N trials).

    python3 evalcfg/check_env.py --hermes-home ~/develop-agent-security/data-austin \
        --container hermes-austin --arm defense --model e4b [--scenario asi06-2] [--profile alice ...]

Exits 1 if any check fails:
1. config: config.yaml (and each --profile's) has the same content as what build_config.py builds
   for this arm/model/scenario plus the --set lines in its header: not hand-edited, not another
   arm, and the overlays have not changed since it was written.
2. restart: the gateway started after its config was written (Hermes reads its config at start).
   A profile with its own gateway has its own gateway-starts.log; otherwise the root one counts.
3. models: the agent model, and on the defense arm the memguard judge, have the expected Ollama
   digest at the config's endpoint. A model not in DIGESTS (cloud) is reported without one.
4. plugins: every name in plugins.enabled is installed and enabled in the container.

On success the last line sums up the environment for the report's environment table.
Stdlib + PyYAML only (team server: Python 3.8).
"""

from __future__ import annotations

import argparse
import datetime
import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import List, Optional, Tuple

import yaml

try:
    from evalcfg import build_config as bc
except ImportError:  # run as a script: python3 evalcfg/check_env.py
    import build_config as bc

# Expected Ollama digests (first 12 hex) on .219 via the 2022 machine. A re-pull changes the
# digest; update here only after the team agrees, since results across digests do not mix.
DIGESTS = {"gemma4:e4b": "dc35e8d9c606", "gemma4:26b": "001e5dafc3c7"}


def read_config(path: Path) -> Tuple[List[str], dict]:
    """(--set items from the header, parsed settings) of a build_config.py output. Content, not
    text, is compared: Hermes may rewrite the file with different formatting."""
    text = path.read_text(encoding="utf-8")
    return re.findall(r"^# set: (.*)$", text, re.M), yaml.safe_load(text) or {}


def check_config(paths: List[Path], arm: str, model: str, scenario: Optional[str]) -> Tuple[dict, str, List[str]]:
    """(merged settings, config sha, problems)."""
    problems: List[str] = []
    settings, digest = {}, ""
    for path in paths:
        if not path.is_file():
            problems.append(f"{path}: missing")
            continue
        sets, actual = read_config(path)
        settings, _, digest = bc.build(arm, model, scenario, sets)
        if actual != settings:
            problems.append(f"{path}: not what build_config.py builds for --arm {arm} --model {model}"
                            + (f" --scenario {scenario}" if scenario else "")
                            + (f" (sets: {', '.join(sets)})" if sets else "") + "; rebuild it")
    return settings, digest, problems


def last_start(home: Path) -> Optional[float]:
    ledger = home / "gateway-starts.log"
    starts = ledger.read_text(encoding="utf-8").split() if ledger.is_file() else []
    return float(starts[-1]) if starts else None


def check_restart(home: Path, configs: List[Path]) -> Tuple[Optional[float], List[str]]:
    """(root gateway's last start, problems)."""
    root, problems = last_start(home), []
    for path in configs:
        own = last_start(path.parent) if path.parent != home else None
        started = own if own is not None else root
        if started is None:
            problems.append(f"{home / 'gateway-starts.log'}: no gateway start recorded")
        elif path.is_file() and path.stat().st_mtime > started:
            problems.append(f"{path} was written after its gateway started; restart that gateway")
    return root, problems


def _get(url: str, body: Optional[dict] = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)


def check_model(role: str, base_url: str, model: str, sampling: Optional[str] = None) -> Tuple[str, List[str]]:
    """(summary for the environment line, problems). ``sampling`` replaces the model's Ollama defaults
    in the summary when the caller sends its own (memguard's judge sends temperature 0)."""
    expected = DIGESTS.get(model)
    if expected is None:
        return f"{role} {model} (no digest)", []
    root = re.sub(r"/v1/?$", "", base_url or "")
    try:
        version = _get(f"{root}/api/version").get("version")
        tags = {m["name"]: m["digest"] for m in _get(f"{root}/api/tags").get("models", [])}
        params = _get(f"{root}/api/show", {"model": model}).get("parameters") or ""
    except Exception as exc:
        return "", [f"{role} {model}: cannot reach Ollama at {root} ({exc}); is the tunnel up?"]
    actual = (tags.get(model) or "")[:12]
    if actual != expected:
        return "", [f"{role} {model} at {root}: digest {actual or 'absent'}, expected {expected}"]
    sampling = sampling or " ".join(" ".join(line.split()) for line in params.splitlines()
                                    if line.split()[:1] in (["temperature"], ["top_k"], ["top_p"]))
    return f"{role} {model} {actual} (Ollama {version}; {sampling or 'no sampling defaults'})", []


def check_plugins(container: str, wanted: List[str]) -> List[str]:
    if not wanted:
        return []
    try:
        # As the hermes user: a root `hermes` run could leave root-owned files in the data dir.
        out = subprocess.run(["docker", "exec", "-u", "hermes", container, "/opt/hermes/.venv/bin/hermes", "plugins", "list",
                              "--enabled", "--json"], capture_output=True, text=True, timeout=120, check=True).stdout
        enabled = {p["name"] for p in json.loads(out) if p.get("status") == "enabled"}
    except Exception as exc:
        return [f"cannot list plugins in {container}: {exc}"]
    return [f"plugin {name} is not installed and enabled in {container}" for name in wanted if name not in enabled]


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--hermes-home", required=True, help="the container's data dir on the server")
    parser.add_argument("--container", required=True)
    parser.add_argument("--arm", required=True, choices=["stock", "defense"])
    parser.add_argument("--model", required=True, choices=sorted(p.stem for p in (bc.HERE / "models").glob("*.yaml")))
    parser.add_argument("--scenario", choices=sorted(p.stem for p in (bc.HERE / "scenarios").glob("*.yaml")))
    parser.add_argument("--profile", action="append", default=[], help="also check this profile's config (repeat)")
    args = parser.parse_args(argv)

    home = Path(args.hermes_home).expanduser()
    configs = [home / "config.yaml"] + [home / "profiles" / name / "config.yaml" for name in args.profile]
    settings, digest, problems = check_config(configs, args.arm, args.model, args.scenario)
    started, restart_problems = check_restart(home, configs)
    problems += restart_problems
    agent = settings.get("model", {})
    parts = [f"{args.arm} {args.model}" + (f" {args.scenario}" if args.scenario else ""), f"config {digest[:12]}"]
    checks = [("agent", agent.get("base_url", ""), agent.get("default", ""), None)]
    judge = settings.get("memguard", {})
    if judge.get("enabled"):
        checks.append(("judge", judge.get("base_url") or agent.get("base_url", ""),
                       judge.get("model") or agent.get("default", ""), "temperature 0"))
    for role, base_url, model, sampling in checks:
        summary, model_problems = check_model(role, base_url, model, sampling)
        problems += model_problems
        parts.append(summary)
    wanted = settings.get("plugins", {}).get("enabled") or []
    problems += check_plugins(args.container, wanted)
    parts.append(f"plugins {', '.join(wanted) or 'none'}")
    if started is not None:
        parts.append("gateway started " + datetime.datetime.fromtimestamp(started).strftime("%Y-%m-%d %H:%M"))

    for problem in problems:
        print(f"FAIL {problem}")
    if problems:
        return 1
    print("OK " + " | ".join(parts))
    return 0


if __name__ == "__main__":
    sys.exit(main())
