import anthropic
from datetime import datetime
from zoneinfo import ZoneInfo

from config import ANTHROPIC_API_KEY, CLAUDE_MODEL
from tools.gmail import TOOL_DEFS as GMAIL_TOOLS, DISPATCH as GMAIL_DISPATCH
from tools.outlook_mail import TOOL_DEFS as OUTLOOK_MAIL_TOOLS, DISPATCH as OUTLOOK_MAIL_DISPATCH
from tools.calendar import TOOL_DEFS as CALENDAR_TOOLS, DISPATCH as CALENDAR_DISPATCH
from tools.todo import TOOL_DEFS as TODO_TOOLS, DISPATCH as TODO_DISPATCH
from tools.maps import TOOL_DEFS as MAPS_TOOLS, DISPATCH as MAPS_DISPATCH

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

_SGT = ZoneInfo("Asia/Singapore")

def _system_prompt() -> str:
    now = datetime.now(_SGT)
    date_str = now.strftime("%A, %d %B %Y")   # e.g. "Monday, 02 June 2025"
    time_str = now.strftime("%H:%M")           # e.g. "14:35"
    return f"""\
Today is {date_str}, {time_str} SGT (UTC+8).

You are Bryan's personal assistant on Telegram. You help with:
- Voice messages: YES, fully supported — Telegram voice notes are automatically transcribed to text before reaching you, so you already handle them seamlessly
- Personal email (Gmail): search, read, send
- Work email (Outlook): search, read, send
- Calendar (Outlook): list events, create events, cancel events
- Tasks (Microsoft To Do): list, add, complete tasks
- Sending Telegram messages to contacts: coming soon
- Navigation and directions (Google Maps): get directions or travel time between any two places
- General questions: always available

Keep replies concise — Bryan is on mobile.
Do NOT use markdown formatting (no **bold**, no _italics_, no bullet points with *, no backticks). Use plain text only. You may use emoji sparingly.
When a task needs a capability not yet available, say so clearly.
IMPORTANT: Before sending any email or creating/cancelling any calendar event, always show Bryan the details and ask for explicit confirmation.
"""

# Aggregate all tools — add new phases here
TOOLS = [
    *GMAIL_TOOLS,
    *OUTLOOK_MAIL_TOOLS,
    *CALENDAR_TOOLS,
    *TODO_TOOLS,
    *MAPS_TOOLS,
]

DISPATCH: dict = {
    **GMAIL_DISPATCH,
    **OUTLOOK_MAIL_DISPATCH,
    **CALENDAR_DISPATCH,
    **TODO_DISPATCH,
    **MAPS_DISPATCH,
}


def _execute_tool(name: str, inputs: dict) -> str:
    if name not in DISPATCH:
        return f"Unknown tool: {name}"
    try:
        return DISPATCH[name](**inputs)
    except Exception as e:
        return f"Tool error ({name}): {e}"


def chat(history: list[dict]) -> str:
    """Run the Claude tool-use loop until the model returns a final text response."""
    while True:
        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=2048,
            system=_system_prompt(),
            messages=history,
            tools=TOOLS,
        )

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
