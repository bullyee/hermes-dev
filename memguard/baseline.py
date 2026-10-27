"""Baseline facts: admin-maintained facts plus the URL and account allowlist. The agent never writes them.

Stand-in until M7 (cpy): ``load`` returns an empty baseline, so the conflict and allowlist
checks find nothing and the gate behaves as before.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple


@dataclass(frozen=True)
class Baseline:
    facts: Tuple[dict, ...] = ()
    urls: Tuple[str, ...] = ()
    accounts: Tuple[str, ...] = ()
    version: str = "none"


def load() -> Baseline:
    return Baseline()
