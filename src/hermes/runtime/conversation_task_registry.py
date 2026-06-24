"""Process-global map: per-cycle Nous task_id → chat conversation_id.

The security `pre_tool_call` hook is built ONCE at startup and only receives
Nous's per-cycle `task_id` — a RANDOM uuid minted in `_run_conversation_with_cdp`
(nous_engine), unrelated to the chat thread. To anchor a HITL approval card to
the conversation the owner is actually looking at, the engine registers the
cycle's real `conversation_id` here (keyed by that task_id) right before running
the agent; the hook resolves it back when it registers a pending approval.

Without this, the approval row is stored with the random task_id as its
`conversation_id`, so the in-chat widget — which filters by the active thread —
never matches it and the card NEVER renders (the "I never saw an approval card"
bug). Process-global + locked: the engine runs the cycle in an executor thread
and the hook fires within it; keys are unique per cycle so there is no contention.
"""

from __future__ import annotations

import threading

_lock = threading.Lock()
_conv_by_task: dict[str, str] = {}


def set_conversation_for_task(task_id: str, conversation_id: str) -> None:
    """Bind a cycle's task_id to its chat conversation_id. No-op if either empty."""
    if not task_id or not conversation_id:
        return
    with _lock:
        _conv_by_task[task_id] = conversation_id


def get_conversation_for_task(task_id: str) -> str:
    """Resolve the chat conversation_id for a cycle's task_id, or "" if unknown."""
    if not task_id:
        return ""
    with _lock:
        return _conv_by_task.get(task_id, "")


def clear_conversation_for_task(task_id: str) -> None:
    """Drop the binding once the cycle ends (called from the engine's finally)."""
    if not task_id:
        return
    with _lock:
        _conv_by_task.pop(task_id, None)
