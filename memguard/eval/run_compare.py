"""Compare Hermes's regex memory scan with the memguard LLM judge on the same entries.

Usage (from the repo root):
    python3 -m memguard.eval.run_compare --base-url http://localhost:11414/v1 --model gemma4:e4b \
        --cases memguard/eval/cases-heldout.json --trials 3

Each case has an ``expected`` verdict. The two errors that matter: an attack allowed (poison
saved) and a benign entry blocked (the user can't override a block). A flag on a benign entry
only costs the user a prompt. A case is unstable when its trials disagree.
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
    parser.add_argument("--base-url", default="http://localhost:11414/v1")  # remote Ollama tunnel; never local Ollama
    parser.add_argument("--model", default="gemma4:e4b")
    parser.add_argument("--cases", default=str(HERE / "cases.json"))
    parser.add_argument("--out", default=str(HERE / "results.json"))
    parser.add_argument("--trials", type=int, default=1)
    args = parser.parse_args()

    cases = json.loads(Path(args.cases).read_text())
    regex_blocks = _load_hermes_scanner()
    llm = openai_compatible(args.base_url, args.model)

    rows = []
    for trial in range(args.trials):
        for case in cases:
            started = time.monotonic()
            verdict = judge_entry(case["text"], llm)
            rows.append({**case, "trial": trial, "regex_blocked": regex_blocks(case["text"]), "judge": verdict.verdict,
                         "category": verdict.category, "reason": verdict.reason, "judge_error": verdict.error,
                         "seconds": round(time.monotonic() - started, 1)})
            r = rows[-1]
            mark = "" if r["judge"] == r.get("expected", r["judge"]) else f"  (expected {r['expected']})"
            print(f"t{trial} {r['id']:24} {r['label']:6} regex={'BLOCK' if r['regex_blocked'] else 'allow':5}  "
                  f"judge={r['judge']:5}{' (error)' if r['judge_error'] else ''} {r['category']}{mark}", flush=True)
            # Saved after every entry: a slow local judge run can be interrupted part-way.
            Path(args.out).write_text(json.dumps({"model": args.model, "trials": args.trials, "rows": rows}, indent=2))

    def count(label, pred):
        sel = [r for r in rows if r["label"] == label]
        return f"{sum(map(pred, sel))}/{len(sel)}"

    print(f"\n{args.trials} trial(s) x {len(cases)} cases, judge calls counted per trial")
    print(f"attacks held (flag/block)   regex {count('attack', lambda r: r['regex_blocked'])}"
          f"   judge {count('attack', lambda r: r['judge'] != 'allow')}")
    print(f"attacks allowed (poisoned)  judge {count('attack', lambda r: r['judge'] == 'allow')}")
    print(f"benign blocked (no override) regex {count('benign', lambda r: r['regex_blocked'])}"
          f"   judge {count('benign', lambda r: r['judge'] == 'block')}")
    print(f"benign flagged (asks user)  judge {count('benign', lambda r: r['judge'] == 'flag')}")
    if all("expected" in c for c in cases):
        print(f"matches expected verdict    judge {sum(r['judge'] == r['expected'] for r in rows)}/{len(rows)}")
    by_case = {}
    for r in rows:
        by_case.setdefault(r["id"], set()).add(r["judge"])
    unstable = sorted(k for k, v in by_case.items() if len(v) > 1)
    if args.trials > 1:
        print(f"unstable cases              {len(unstable)}/{len(by_case)} {' '.join(unstable)}")
    print(f"details: {args.out}")

if __name__ == "__main__":
    main()
