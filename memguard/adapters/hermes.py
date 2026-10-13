"""Hermes Agent adapter: runs the judge on Hermes's configured model and gates memory writes.

Switched by ``memguard.enabled`` in config.yaml (default off, so stock behaviour is the
baseline). ``memguard.timeout`` sets the judge call timeout in seconds (default 180; a CPU-only
local model needs most of that).

A ``flag`` verdict goes to the user through Hermes's own memory-approval prompt (the harness
shows it, so text in the conversation cannot answer it). When nobody can answer (gateway
without a prompt, ``hermes chat -q``, cron, background review), a caller that passes ``stage``
puts the write into Hermes's pending store for ``/memory approve``; otherwise it is refused.
"""

from __future__ import annotations

import json
import logging
from typing import Callable, Iterable, Optional, Tuple

from memguard.gate import Decision, MemGuard, refusal_message

logger = logging.getLogger("memguard")


def _settings() -> dict:
    from hermes_cli.config import cfg_get, load_config
    return cfg_get(load_config(), "memguard", default={}) or {}


def enabled() -> bool:
    from utils import is_truthy_value
    try:
        return is_truthy_value(_settings().get("enabled"), default=False)
    except Exception:
        logger.warning("memguard: could not read config; leaving memory writes unguarded", exc_info=True)
        return False


def _hermes_llm(system: str, user: str) -> str:
    from agent.auxiliary_client import call_llm, extract_content_or_reasoning
    timeout = float(_settings().get("timeout", 180))
    response = call_llm(messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                        temperature=0.0, timeout=timeout)
    return extract_content_or_reasoning(response) or ""


def _guard() -> MemGuard:
    # State lives in the active profile's home, resolved per call (profiles can switch).
    from hermes_constants import get_hermes_home
    return MemGuard(_hermes_llm, get_hermes_home() / "memguard")


# Stages the whole pending write for a flagged entry: (tool result JSON, pending id or None if not staged).
Stage = Callable[[Decision], Tuple[str, Optional[str]]]


def _ask_user(decision: Decision) -> Optional[bool]:
    """The user's answer to a flagged write: True save, False drop, None nobody can answer now."""
    from tools import write_approval as wa
    if wa.current_origin() == "background_review":
        return None
    v = decision.verdict
    return wa._prompt_inline_memory_approval(f"memguard flagged this ({v.category}): {v.reason}", decision.content)


def guard_write(store: str, action: str, contents: Iterable[Optional[str]],
                stage: Optional[Stage] = None) -> Optional[str]:
    """JSON tool result when any of ``contents`` must not be written now, None to proceed."""
    if not enabled():
        return None
    guard = _guard()
    for content in contents:
        if not content or not content.strip():
            continue
        decision = guard.check_write(content, store=store, action=action)
        logger.info("memguard %s %s/%s: %s (%s, %.1fs)", decision.id, store, action,
                    decision.verdict.verdict, decision.verdict.category, decision.seconds)
        review = None
        if decision.verdict.verdict == "flag":
            answer = _ask_user(decision)
            if answer is None and stage is not None:
                result, pending_id = stage(decision)
                guard.record_review(decision, "staged" if pending_id else "unreviewed", pending_id=pending_id)
                return result
            review = {True: "approved", False: "denied", None: "unreviewed"}[answer]
            guard.record_review(decision, review)
        if (message := refusal_message(decision, review)) is not None:
            return json.dumps({"success": False, "error": message, "memguard": {
                "id": decision.id, "verdict": decision.verdict.verdict, "category": decision.verdict.category}},
                ensure_ascii=False)
    return None
