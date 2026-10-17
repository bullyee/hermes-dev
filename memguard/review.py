"""List stored entries the memguard judge holds (flag or block) under the current rules.

    python3 -m memguard.review [--state-dir DIR]

DIR defaults to $HERMES_HOME/memguard (in the Hermes container: /opt/data/memguard). There is no
global approve: a flagged entry is approved per session by the user reading it, at the prompt,
because whoever wrote an entry cannot vouch for it to another user of the same memory. Remove an
entry you don't want with the agent's memory tool.
"""

from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

from memguard.gate import MemGuard


def _no_llm(system: str, user: str) -> str:
    raise RuntimeError("the review command never calls the judge")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", default=str(Path(os.environ.get("HERMES_HOME", "~/.hermes")).expanduser() / "memguard"))
    args = parser.parse_args()
    rows = MemGuard(_no_llm, Path(args.state_dir)).held()
    for r in rows:
        v = r["verdict"]
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(r["at"]))
        print(f"{v['verdict']:5}  {v['category']:20}  {when}  {r['content'][:90]}")
    print(f"{len(rows)} entr{'y' if len(rows) == 1 else 'ies'} held")


if __name__ == "__main__":
    main()
