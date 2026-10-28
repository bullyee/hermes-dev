"""memguard's Hermes hooks. When Hermes stops a run or times a tool out it abandons the call but its
thread runs on, so a memory write whose judge is still running would land after the run ended (R3).
Hermes reports the abandoned call at once as ``post_tool_call`` with status cancelled/timeout; the
id is handed to memguard, which blocks that call's write once the judge returns."""


def _on_post_tool_call(tool_call_id: str = "", status: str = "", **_) -> None:
    if tool_call_id and status in ("cancelled", "timeout"):
        from memguard.adapters.hermes import note_abandoned
        note_abandoned(tool_call_id)


def register(ctx) -> None:
    ctx.register_hook("post_tool_call", _on_post_tool_call)
