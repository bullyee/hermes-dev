"""Hermes Agent adapter: runs the judge through Hermes's LLM client and gates memory writes.

Switched by ``memguard.enabled`` in config.yaml (default off, so stock behaviour is the
baseline). ``memguard.timeout`` sets the judge call timeout in seconds (default 180; a CPU-only
local model needs most of that). ``memguard.base_url`` and ``memguard.model`` give judge calls
their own OpenAI-compatible endpoint and model (default: the agent's own), so the judge stays the
same when the agent's model changes.

Each write carries a stamp (``_make_stamp``: profile, session, turn, origin) into the gate's
policy step. After the judge returns, ``_veto`` blocks the write whatever the verdict when its tool
call was abandoned meanwhile (Hermes stopped the run or timed the call out, but the call's thread
runs on and would write after the run ended; ``plugins/memguard_hooks`` reports these ids), or when
the session has an agcp.taint.v1 note (the other ASI defense, agcp_guard, aborted it).

A write the policy stages goes to the user through Hermes's own memory-approval prompt (the
harness shows it, so text in the conversation cannot answer it). When nobody can answer (gateway
without a prompt, ``hermes chat -q``, cron, background review), a caller that passes ``stage``
puts the write into Hermes's pending store for ``/memory approve``; otherwise it is refused.

On read (``screen_recall``), entries are checked with the same judge before the agent sees them:
built-in memory when a session loads it, holographic facts on every prefetch and fact_store read.
A blocked entry is withheld; a flagged one is withheld until the reader approves it for their
session (``resolve_pending``). ``memguard.recall_timeout`` (default 30 s) bounds each uncached
judge call; ``memguard.recall_cache: false`` re-judges on every read instead of reusing verdicts.
Recall judge calls run with thinking off (``memguard.recall_thinking``, default false): a thinking
judge takes ~30 s per entry, far past Hermes's 8 s holographic prefetch timeout. Even without
thinking, a prefetch judges up to 5 uncached facts one by one (~12 s), so while memguard is
enabled the prefetch bound is ``memguard.prefetch_timeout`` (default 20 s) instead of 8 s.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
from pathlib import Path
from typing import Callable, Iterable, Optional, Tuple

from memguard.gate import Decision, MemGuard, refusal_message
from memguard.judge import Verdict
from memguard.types import PolicyDecision, Stamp

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


def prefetch_timeout(default: float) -> float:
    """MemoryManager's external prefetch bound: ``memguard.prefetch_timeout`` while enabled, else ``default``."""
    if not enabled():
        return default
    try:
        return float(_settings().get("prefetch_timeout", 20))
    except (TypeError, ValueError):
        logger.warning("memguard: bad prefetch_timeout; keeping %.0f s", default)
        return default


def _hermes_llm(system: str, user: str, *, timeout_key: str = "timeout", default_timeout: float = 180,
                thinking: bool = True) -> str:
    from agent.auxiliary_client import call_llm, extract_content_or_reasoning
    settings = _settings()
    timeout = float(settings.get(timeout_key, default_timeout))
    model = settings.get("model") or None
    route: dict = {}
    # Own endpoint and model are optional: a single-request server would queue the judge behind the
    # agent, and without a model the judge follows the agent's main model.
    response = call_llm(messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                        temperature=0.0, timeout=timeout, base_url=settings.get("base_url") or None,
                        model=model, route_info=route,
                        reasoning_config=None if thinking else {"enabled": False})
    if model and route.get("model") != model:
        # When the judge's endpoint fails (timeout, no connection), Hermes retries the call on the
        # agent's main model. That verdict is not the pinned judge's: fail like any judge error (flag).
        raise RuntimeError(f"answered by {route.get('model')}, not memguard.model {model}")
    return extract_content_or_reasoning(response) or ""


def _recall_llm(system: str, user: str) -> str:
    from utils import is_truthy_value
    thinking = is_truthy_value(_settings().get("recall_thinking"), default=False)
    return _hermes_llm(system, user, timeout_key="recall_timeout", default_timeout=30, thinking=thinking)


def _guard(llm=None) -> MemGuard:
    # State lives in the active profile's home, resolved per call (profiles can switch).
    from hermes_constants import get_hermes_home
    return MemGuard(llm or _hermes_llm, get_hermes_home() / "memguard")


# Stages the whole pending write for a flagged entry: (tool result JSON, pending id or None if not staged).
Stage = Callable[[Decision], Tuple[str, Optional[str]]]


def _ask_user(decision: Decision) -> Optional[bool]:
    """The user's answer to a flagged write: True save, False drop, None nobody can answer now."""
    from tools import write_approval as wa
    if wa.current_origin() == "background_review":
        return None
    return wa._prompt_inline_memory_approval(f"memguard flagged this ({decision.verdict.category}): {decision.reason}",
                                             decision.content)


def _make_stamp() -> Stamp:
    """Who and where the current write comes from: principal is the profile the write lands in,
    session and turn the ids Hermes binds around every tool call (``agent/tool_executor.py``); outside
    a tool call the session falls back to the gateway session key. tainted and delegated_from stay
    unset until M3."""
    from tools import write_approval as wa
    principal = session_id = turn_id = ""
    try:
        from hermes_cli.profiles import current_profile_name
        principal = current_profile_name("default") or "default"
    except Exception:
        logger.debug("memguard: no profile name for the stamp", exc_info=True)
    try:
        from tools.approval_context import _approval_session_id, _approval_turn_id
        session_id, turn_id = _approval_session_id.get(), _approval_turn_id.get()
    except Exception:
        logger.debug("memguard: no session/turn id for the stamp", exc_info=True)
    origin = "background_review" if wa.current_origin() == "background_review" else "user_turn"
    return Stamp(principal=principal, session_id=session_id or _session(), turn_id=turn_id or None,
                 origin=origin, delegated_from=None, tainted=False, taint_sources=(), user_confirmed=False)


def guard_write(store: str, action: str, contents: Iterable[Optional[str]],
                stage: Optional[Stage] = None) -> Optional[str]:
    """JSON tool result when any of ``contents`` must not be written now, None to proceed."""
    if not enabled():
        return None
    guard = _guard()
    stamp, tool_call_id = _make_stamp(), _tool_call_id()
    for content in contents:
        if not content or not content.strip():
            continue
        decision = guard.check_write(content, store=store, action=action, stamp=stamp,
                                     veto=lambda: _veto(stamp, tool_call_id))
        rule = (decision.veto or decision.policy).rule
        logger.info("memguard %s %s/%s: %s by %s (%s, %.1fs)", decision.id, store, action, decision.outcome,
                    rule, decision.verdict.category, decision.seconds)
        review = None
        if decision.outcome == "stage":
            answer = _ask_user(decision)
            if answer is None and stage is not None:
                result, pending_id = stage(decision)
                guard.record_review(decision, "staged" if pending_id else "unreviewed", pending_id=pending_id)
                return result
            review = {True: "approved", False: "denied", None: "unreviewed"}[answer]
            guard.record_review(decision, review)
        if (message := refusal_message(decision, review)) is not None:
            return json.dumps({"success": False, "error": message, "memguard": {
                "id": decision.id, "verdict": decision.verdict.verdict, "category": decision.verdict.category,
                "outcome": decision.outcome, "rule": rule}},
                ensure_ascii=False)
    return None


# --- Vetoes: blocks decided outside the judge, checked once it has returned (it can take 30 s) -----

_abandoned: set = set()   # tool_call_ids Hermes stopped waiting for


def note_abandoned(tool_call_id: str) -> None:
    """``post_tool_call`` reported this call cancelled or timed out (``plugins/memguard_hooks``)."""
    _abandoned.add(tool_call_id)


def _tool_call_id() -> str:
    from tools.approval_context import _approval_tool_call_id
    return _approval_tool_call_id.get()


def _veto(stamp: Stamp, tool_call_id: str) -> Optional[PolicyDecision]:
    if tool_call_id and tool_call_id in _abandoned:
        return PolicyDecision("block", "R-abandoned",
                              f"tool call {tool_call_id} was abandoned (run stopped or timed out) while it was judged.")
    if _blue_team_tainted(stamp.session_id):
        return PolicyDecision("block", "R-taint-note", "藍隊污染註記: another defense aborted this session (agcp.taint.v1).")
    return None


# Reader copied from winston's agcp.taint.v1 spec (污染註記格式說明 §2, §5), not imported from agcp_guard.
_SAFE_ID = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9._-]{0,127}")


def _taint_file(session_id: str) -> Path:
    from hermes_constants import get_hermes_home
    name = (session_id if _SAFE_ID.fullmatch(session_id)
            else "~h-" + hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:40])
    return get_hermes_home() / "agcp" / "taint" / f"{name}.json"


def _blue_team_tainted(session_id: str) -> bool:
    """A note exists for the session. Only a missing file is clean: any other read error counts as
    tainted (fail-closed). Opened by name, never listed: the directory also holds probes and temp files."""
    if not session_id:
        return False
    try:
        with open(_taint_file(session_id), encoding="utf-8") as fh:
            fh.read(1)
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return True


# --- Recall: what the agent may see of stored memory ---------------------------------------
# A flagged entry read back is replaced by a pending placeholder; ``resolve_pending`` (called on a
# thread that can prompt) asks the reader and swaps the text back in on yes. Answers last for the
# reader's session only and are never cached: whoever wrote or approved an entry cannot vouch for
# it to another user of the same memory.

_PENDING_RE = re.compile(r"\[WITHHELD by memguard #([0-9a-f]{12}):[^\]]*\]")
_pending: dict = {}    # token -> (store, content, verdict)
_answers: dict = {}    # (session, entry key) -> True approved / False denied
_state_lock = threading.Lock()


def _session() -> str:
    try:
        from tools.terminal_tool import _current_session_key
        return _current_session_key() or "local"
    except Exception:
        return "local"


def _withheld(store: str, why: str) -> str:
    return f"[WITHHELD by memguard: a stored {store} entry was {why}. Its text is not shown. Do not try to recover it.]"


def screen_recall(store: str, content: Optional[str]) -> Optional[str]:
    """Text the agent may see for one stored entry: the entry itself, or a placeholder."""
    if not content or not content.strip() or not enabled():
        return content
    try:
        from utils import is_truthy_value
        use_cache = is_truthy_value(_settings().get("recall_cache"), default=True)
        verdict = _guard(_recall_llm).check_recall(content, store=store, use_cache=use_cache)
    except Exception:
        logger.warning("memguard: recall check failed; holding the entry", exc_info=True)
        verdict = Verdict("flag", "other", "the recall check failed", error=True)
    if verdict.verdict == "allow":
        return content
    if verdict.verdict == "block":
        logger.info("memguard blocked a %s entry on recall (%s)", store, verdict.category)
        return _withheld(store, "blocked")
    answer = _answers.get((_session(), _entry_key(content)))
    if answer is not None:
        return content if answer else _withheld(store, "declined by the user")
    token = hashlib.sha256(f"{_session()}|{content}".encode()).hexdigest()[:12]
    with _state_lock:
        _pending[token] = (store, content, verdict)
    return f"[WITHHELD by memguard #{token}: a stored {store} entry is waiting for the user's approval.]"


def resolve_pending(text: Optional[str]) -> Optional[str]:
    """Ask the user about each pending placeholder in ``text``; approved entries are put back.
    Without anyone to ask (no prompt on this thread, gateway, cron) placeholders stay as they are."""
    if not text or "WITHHELD by memguard #" not in text:
        return text

    def swap(match):
        with _state_lock:
            item = _pending.get(match.group(1))
        if item is None:
            return match.group(0)
        store, content, verdict = item
        key = (_session(), _entry_key(content))
        if key not in _answers:
            answer = _ask_reader(store, content, verdict)
            _guard().record_recall_review(content, store, {True: "approved", False: "denied", None: "unreviewed"}[answer],
                                          key[0])
            if answer is None:
                return match.group(0)
            _answers[key] = answer
        return content if _answers[key] else _withheld(store, "declined by the user")

    return _PENDING_RE.sub(swap, text)


def _ask_reader(store: str, content: str, verdict: Verdict) -> Optional[bool]:
    """True show it, False keep it withheld, None nobody can answer on this thread."""
    from tools import write_approval as wa
    from tools.approval_context import _no_user_can_answer
    if wa.current_origin() == "background_review" or _no_user_can_answer():
        return None
    try:
        from tools.terminal_tool import _get_approval_callback
        from tools.approval_prompt import callback_accepts
        callback = _get_approval_callback()
        if callback is None:
            return None
        extra = {"title": "Use stored memory?"} if callback_accepts(callback, "title") else {}
        choice = callback(content, f"memguard flagged a stored {store} entry ({verdict.category}): {verdict.reason} "
                          f"Let the agent use it this session?", allow_permanent=False, **extra)
    except Exception:
        logger.warning("memguard: recall prompt failed; keeping the entry withheld", exc_info=True)
        return None
    return {"once": True, "session": True, "deny": False}.get(choice)


def _entry_key(content: str) -> str:
    return hashlib.sha256(content.strip().encode("utf-8")).hexdigest()[:16]
