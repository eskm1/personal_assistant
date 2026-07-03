import anthropic
from datetime import datetime
from zoneinfo import ZoneInfo

from config import ANTHROPIC_API_KEY, CLAUDE_MODEL
from tools.gmail import TOOL_DEFS as GMAIL_TOOLS, DISPATCH as GMAIL_DISPATCH
from tools.outlook_mail import TOOL_DEFS as OUTLOOK_MAIL_TOOLS, DISPATCH as OUTLOOK_MAIL_DISPATCH
from tools.calendar import TOOL_DEFS as CALENDAR_TOOLS, DISPATCH as CALENDAR_DISPATCH
from tools.todo import TOOL_DEFS as TODO_TOOLS, DISPATCH as TODO_DISPATCH
from tools.maps import TOOL_DEFS as MAPS_TOOLS, DISPATCH as MAPS_DISPATCH
from tools.umcpm import TOOL_DEFS as UMCPM_TOOLS, DISPATCH as UMCPM_DISPATCH
from tools.pending import TOOL_DEFS as PENDING_TOOLS, DISPATCH as PENDING_DISPATCH

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

_SGT = ZoneInfo("Asia/Singapore")


def _current_time() -> str:
    now = datetime.now(_SGT)
    return now.strftime("%A, %d %B %Y, %H:%M SGT (UTC+8)")


# Date only (no minutes) so the cached system-prompt prefix stays stable all day.
def _system_prompt() -> str:
    date_str = datetime.now(_SGT).strftime("%A, %d %B %Y")
    return f"""\
Today is {date_str} (Singapore, UTC+8). Call get_current_time if you need the exact time of day.

You are Bryan's personal assistant on Telegram. You help with:
- Voice messages: YES, fully supported — Telegram voice notes are automatically transcribed to text before reaching you, so you already handle them seamlessly
- Personal email (Gmail): search, read, send
- Work email (Outlook): search, read, send
- Calendar (Outlook): list events, create events, cancel events
- Tasks (Microsoft To Do): list, add, complete tasks
- Sending Telegram messages to contacts: coming soon
- Navigation and directions (Google Maps): get directions or travel time between any two places
- Urban Makers (umcpm) quotation tool: create projects with draft quotes, add draft quotes to existing projects, list projects, and return review links
- General questions: always available

Keep replies concise — Bryan is on mobile.
Do NOT use markdown formatting (no **bold**, no _italics_, no bullet points with *, no backticks). Use plain text only. You may use emoji sparingly.
When a task needs a capability not yet available, say so clearly.

CONFIRMATION FLOW for destructive actions (sending email, cancelling an event):
The send/cancel tools do NOT act immediately — they STAGE the action and return a summary starting with "STAGED".
When you get a STAGED result, show Bryan the summary and ask him to confirm.
Only when he clearly confirms, call confirm_pending_action. If he declines, call cancel_pending_action.
Never claim an email was sent or an event cancelled until confirm_pending_action reports success.
"""


TIME_TOOL = {
    "name": "get_current_time",
    "description": "Get the current date and time in Singapore (SGT, UTC+8). Use when the exact time of day matters.",
    "input_schema": {"type": "object", "properties": {}},
}

# Aggregate all tools — add new phases here
TOOLS = [
    TIME_TOOL,
    *GMAIL_TOOLS,
    *OUTLOOK_MAIL_TOOLS,
    *CALENDAR_TOOLS,
    *TODO_TOOLS,
    *MAPS_TOOLS,
    *UMCPM_TOOLS,
    *PENDING_TOOLS,
]

# Mark the end of the tools array as a prompt-cache breakpoint. The tools +
# system prefix (~2-3k tokens) is then cached and reused across turns/tool loops.
# Replace (not mutate) the last entry so the shared source dict is untouched.
TOOLS[-1] = {**TOOLS[-1], "cache_control": {"type": "ephemeral"}}

DISPATCH: dict = {
    "get_current_time": lambda: _current_time(),
    **GMAIL_DISPATCH,
    **OUTLOOK_MAIL_DISPATCH,
    **CALENDAR_DISPATCH,
    **TODO_DISPATCH,
    **MAPS_DISPATCH,
    **UMCPM_DISPATCH,
    **PENDING_DISPATCH,
}


def _execute_tool(name: str, inputs: dict) -> str:
    if name not in DISPATCH:
        return f"Unknown tool: {name}"
    try:
        return DISPATCH[name](**inputs)
    except Exception as e:
        return f"Tool error ({name}): {e}"


def chat(history: list[dict], is_owner: bool = True) -> str:
    """Run the Claude tool-use loop until the model returns a final text response.

    is_owner=False disables all personal tools (email, calendar, tasks, maps)
    so non-owners in group chats only get general Claude conversation.
    """
    active_tools = TOOLS if is_owner else []

    # System prompt as a cacheable content block (prefix reused across turns).
    system = [{
        "type": "text",
        "text": _system_prompt(),
        "cache_control": {"type": "ephemeral"},
    }]

    while True:
        kwargs: dict = dict(
            model=CLAUDE_MODEL,
            max_tokens=4096,
            system=system,
            messages=history,
        )
        if active_tools:
            kwargs["tools"] = active_tools

        response = client.messages.create(**kwargs)

        assistant_content = response.content
        history.append({"role": "assistant", "content": assistant_content})

        if response.stop_reason == "end_turn":
            for block in assistant_content:
                if block.type == "text":
                    return block.text
            return "(no response)"

        if response.stop_reason == "tool_use":
            tool_results = []
            for block in assistant_content:
                if block.type == "tool_use":
                    result = _execute_tool(block.name, block.input)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    })
            history.append({"role": "user", "content": tool_results})
        else:
            return f"(unexpected stop reason: {response.stop_reason})"
