"""
Code-level confirmation gate for destructive actions (sending email, cancelling
events). The model cannot bypass this by "deciding" confirmation happened: the
action tools only STAGE an action here and return a summary. The staged action is
executed only when confirm_pending_action is called on a later turn.

Staging is keyed per conversation via a context variable set by the bot before it
runs the (threaded) chat loop, so concurrent conversations never cross wires.
"""
import contextvars
from typing import Callable

# Set by bot.py before each chat() call; copied into the worker thread by
# asyncio.to_thread so tool functions can read which conversation they serve.
current_conversation: contextvars.ContextVar[str] = contextvars.ContextVar(
    "current_conversation", default="global"
)

# conversation id -> staged action
_pending: dict[str, dict] = {}


def stage(summary: str, executor: Callable[[], str]) -> str:
    """Stage a destructive action for the current conversation and return a
    message telling the model to get explicit confirmation first."""
    conv = current_conversation.get()
    _pending[conv] = {"summary": summary, "executor": executor}
    return (
        f"STAGED (NOT yet done): {summary}\n"
        "Show Bryan this summary and ask him to confirm. "
        "If he confirms, call confirm_pending_action. If not, call cancel_pending_action."
    )


def confirm_pending_action() -> str:
    """Execute the action staged for the current conversation."""
    conv = current_conversation.get()
    action = _pending.pop(conv, None)
    if not action:
        return "Nothing is staged to confirm."
    try:
        return action["executor"]()
    except Exception as e:
        return f"Action failed: {e}"


def cancel_pending_action() -> str:
    """Discard the action staged for the current conversation."""
    conv = current_conversation.get()
    action = _pending.pop(conv, None)
    if not action:
        return "Nothing was staged to cancel."
    return f"Cancelled: {action['summary']}"


TOOL_DEFS = [
    {
        "name": "confirm_pending_action",
        "description": (
            "Execute the action that was just staged (e.g. sending an email or cancelling "
            "an event). Call this ONLY after Bryan has explicitly confirmed."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "cancel_pending_action",
        "description": "Discard a staged action when Bryan declines or changes his mind.",
        "input_schema": {"type": "object", "properties": {}},
    },
]

DISPATCH = {
    "confirm_pending_action": lambda: confirm_pending_action(),
    "cancel_pending_action": lambda: cancel_pending_action(),
}
