"""Compare Hermes's regex memory scan with the memguard LLM judge on the same entries.

Usage (from the repo root):
    python3 -m memguard.eval.run_compare --base-url http://localhost:11434/v1 --model gemma4:e4b
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import time
from pathlib import Path

from memguard.judge import judge_entry
from memguard.llm_openai import openai_compatible

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]


def _load_hermes_scanner():
    """Hermes's own strict-scope scan, loaded by path so no Hermes runtime is needed."""
    spec = importlib.util.spec_from_file_location("hermes_threat_patterns", REPO / "tools" / "threat_patterns.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return lambda text: module.first_threat_message(text, scope="strict") is not None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:11434/v1")
    parser.add_argument("--model", default="gemma4:e4b")
    parser.add_argument("--cases", default=str(HERE / "cases.json"))
    parser.add_argument("--out", default=str(HERE / "results.json"))
    args = parser.parse_args()

    cases = json.loads(Path(args.cases).read_text())
    regex_blocks = _load_hermes_scanner()
    llm = openai_compatible(args.base_url, args.model)

    rows = []
    for case in cases:
        started = time.monotonic()
        verdict = judge_entry(case["text"], llm)
        rows.append({**case, "regex_blocked": regex_blocks(case["text"]), "judge": verdict.verdict,
                     "category": verdict.category, "reason": verdict.reason, "judge_error": verdict.error,
                     "seconds": round(time.monotonic() - started, 1)})
        r = rows[-1]
        print(f"{r['id']:22} {r['label']:6}  regex={'BLOCK' if r['regex_blocked'] else 'allow':5}  "
              f"judge={r['judge']:5}{' (error)' if r['judge_error'] else ''}  {r['category']}", flush=True)
        # Saved after every entry: a slow local judge run can be interrupted part-way.
        Path(args.out).write_text(json.dumps({"model": args.model, "rows": rows}, indent=2))

    def caught(row):  # flag counts as caught: the entry is held instead of committed
        return row["judge"] != "allow"

    attacks = [r for r in rows if r["label"] == "attack"]
    benign = [r for r in rows if r["label"] == "benign"]
    print(f"\nattacks caught   regex {sum(r['regex_blocked'] for r in attacks)}/{len(attacks)}"
          f"   judge {sum(caught(r) for r in attacks)}/{len(attacks)}")
    print(f"benign held      regex {sum(r['regex_blocked'] for r in benign)}/{len(benign)}"
          f"   judge {sum(caught(r) for r in benign)}/{len(benign)}")
    Path(args.out).write_text(json.dumps({"model": args.model, "rows": rows}, indent=2))
    print(f"details: {args.out}")


if __name__ == "__main__":
    main()
