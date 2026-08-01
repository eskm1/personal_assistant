"""Google Calendar (the calendar attached to Bryan's personal Gmail account).

Deliberately a separate module from tools/calendar.py, which is the Outlook/work
calendar over Microsoft Graph. Two calendars, two accounts, two tool sets — the
tool names carry "google" so the model never has to guess which one it is
reaching for.
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from auth.google_oauth import get_calendar_service
from config import USER_TIMEZONE
from tools import pending

_TZ = ZoneInfo(USER_TIMEZONE)

# Google truncates event lists rather than paginating for us; 25 matches the
# Outlook tool and is about as much as is readable on a phone.
_MAX_EVENTS = 25


def _rfc3339(value: str, default: datetime) -> str:
    """Normalise the model's date/datetime string into what Google's API needs.

    Accepts what the Outlook calendar tool accepts ('2026-08-05T14:00:00' or
    '2026-08-05') and anything already carrying an offset. Naive values are read
    as USER_TIMEZONE — a naive string sent as-is is interpreted as UTC by Google,
    which silently shifts every result by 8 hours for a Singapore user.
    """
    value = (value or "").strip()
    if not value:
        return default.isoformat()

    text = value.replace("Z", "+00:00")
    if len(text) == 10:  # bare date
        text += "T00:00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError as e:
        raise ValueError(f"Could not read '{value}' as a date/time: {e}") from e

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_TZ)
    return dt.isoformat()


def _when(event: dict) -> str:
    """Human-readable span. All-day events carry 'date'; timed ones 'dateTime'."""
    start, end = event.get("start", {}), event.get("end", {})
    if "date" in start:
        first = start["date"]
        # Google's all-day end date is exclusive: a one-day event ends the next
        # morning, so reporting it raw makes every all-day event look 2 days long.
        last = end.get("date", "")
        try:
            last_incl = (datetime.fromisoformat(last) - timedelta(days=1)).strftime("%Y-%m-%d")
        except ValueError:
            last_incl = first
        return f"{first} (all day)" if last_incl == first else f"{first} → {last_incl} (all day)"

    def fmt(slot: dict) -> str:
        raw = slot.get("dateTime", "")
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(_TZ).strftime("%Y-%m-%d %H:%M")
        except ValueError:
            return raw

    return f"{fmt(start)} → {fmt(end)}"


# ── Tool functions ────────────────────────────────────────────────────────────

def list_google_calendars() -> str:
    """List the calendars on the Google account, with their IDs."""
    try:
        svc = get_calendar_service()
        items = svc.calendarList().list(maxResults=50).execute().get("items", [])
        if not items:
            return "No calendars found on the Google account."

        lines = []
        for c in items:
            marks = []
            if c.get("primary"):
                marks.append("primary")
            if c.get("accessRole") in ("reader", "freeBusyReader"):
                marks.append("read-only")
            suffix = f" ({', '.join(marks)})" if marks else ""
            lines.append(f"{c.get('summary', '(unnamed)')}{suffix}\n  calendar_id: {c.get('id', '')}")

        return "\n".join(lines)

    except Exception as e:
        return f"Google Calendar list error: {e}"


def list_google_calendar_events(start: str = "", end: str = "", calendar_id: str = "primary") -> str:
    """List events on a Google calendar. Defaults to the next 7 days on primary."""
    try:
        now = datetime.now(_TZ)
        time_min = _rfc3339(start, now)
        time_max = _rfc3339(end, now + timedelta(days=7))

        svc = get_calendar_service()
        events = svc.events().list(
            calendarId=calendar_id or "primary",
            timeMin=time_min,
            timeMax=time_max,
            # Expand recurring events into their actual occurrences; without it
            # a weekly standup comes back as one undated master record.
            singleEvents=True,
            orderBy="startTime",
            maxResults=_MAX_EVENTS,
            timeZone=USER_TIMEZONE,
        ).execute().get("items", [])

        if not events:
            return f"No Google Calendar events between {time_min[:10]} and {time_max[:10]}."

        lines = []
        for e in events:
            line = (
                f"ID: {e.get('id', '')}\n"
                f"Title: {e.get('summary', '(no title)')}\n"
                f"When: {_when(e)}\n"
            )
            if e.get("location"):
                line += f"Location: {e['location']}\n"
            if e.get("hangoutLink"):
                line += f"Meet: {e['hangoutLink']}\n"
            lines.append(line)

        return f"(times shown in {USER_TIMEZONE})\n" + "\n---\n".join(lines)

    except Exception as e:
        return f"Google Calendar list error: {e}"


def create_google_calendar_event(
    title: str,
    start: str,
    end: str,
    location: str = "",
    attendees: str = "",
    notes: str = "",
    calendar_id: str = "primary",
) -> str:
    try:
        body: dict = {
            "summary": title,
            "start": {"dateTime": _rfc3339(start, datetime.now(_TZ)), "timeZone": USER_TIMEZONE},
            "end": {"dateTime": _rfc3339(end, datetime.now(_TZ)), "timeZone": USER_TIMEZONE},
        }
        if location:
            body["location"] = location
        if notes:
            body["description"] = notes
        if attendees:
            body["attendees"] = [{"email": a.strip()} for a in attendees.split(",") if a.strip()]

        svc = get_calendar_service()
        event = svc.events().insert(calendarId=calendar_id or "primary", body=body).execute()
        return f"Google Calendar event created: '{title}' starting {start} (ID: {event.get('id', '')})"

    except Exception as e:
        return f"Google Calendar create error: {e}"


def _do_cancel_google_calendar_event(event_id: str, calendar_id: str) -> str:
    try:
        get_calendar_service().events().delete(
            calendarId=calendar_id or "primary", eventId=event_id
        ).execute()
        return "Event deleted from your Google Calendar."
    except Exception as e:
        return f"Google Calendar cancel error: {e}"


def cancel_google_calendar_event(event_id: str, event_title: str = "", calendar_id: str = "primary") -> str:
    """Stage a Google Calendar deletion for confirmation (does not delete immediately)."""
    label = f"'{event_title}'" if event_title else f"event {event_id}"
    summary = f"Delete Google Calendar {label}"
    return pending.stage(summary, lambda: _do_cancel_google_calendar_event(event_id, calendar_id))


# ── Tool definitions (Anthropic schema) ──────────────────────────────────────

_CAL_ID_PROP = {
    "type": "string",
    "description": "Google calendar ID from list_google_calendars. Defaults to the main one.",
    "default": "primary",
}

TOOL_DEFS = [
    {
        "name": "list_google_calendar_events",
        "description": (
            "List events on Bryan's PERSONAL Google Calendar (the one on his Gmail account). "
            "Defaults to the next 7 days. Use this for personal plans, travel and flights; "
            "use list_calendar_events for the Outlook work calendar."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "start": {"type": "string", "description": "Start datetime or date, e.g. '2026-08-05T00:00:00' or '2026-08-05' (defaults to now)", "default": ""},
                "end": {"type": "string", "description": "End datetime or date (defaults to 7 days from now)", "default": ""},
                "calendar_id": _CAL_ID_PROP,
            },
        },
    },
    {
        "name": "list_google_calendars",
        "description": (
            "List the calendars on Bryan's Google account with their IDs. Only needed when he asks "
            "about a calendar other than his main one — everything else defaults to 'primary'."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "create_google_calendar_event",
        "description": (
            "Create an event on Bryan's personal Google Calendar. "
            "IMPORTANT: Always confirm title, start time, end time and attendees with him before calling."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Event title"},
                "start": {"type": "string", "description": "Start datetime ISO 8601 e.g. '2026-08-05T14:00:00' (assumed to be in Bryan's timezone)"},
                "end": {"type": "string", "description": "End datetime ISO 8601"},
                "location": {"type": "string", "description": "Location (optional)", "default": ""},
                "attendees": {"type": "string", "description": "Comma-separated attendee email addresses (optional)", "default": ""},
                "notes": {"type": "string", "description": "Event description/notes (optional)", "default": ""},
                "calendar_id": _CAL_ID_PROP,
            },
            "required": ["title", "start", "end"],
        },
    },
    {
        "name": "cancel_google_calendar_event",
        "description": (
            "Stage the deletion of a personal Google Calendar event. This does NOT delete immediately — "
            "it stages the deletion and returns a summary. Show Bryan the summary and, once he confirms, "
            "call confirm_pending_action to actually delete it."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "event_id": {"type": "string", "description": "Event ID from list_google_calendar_events"},
                "event_title": {"type": "string", "description": "Event title, for a clearer confirmation message", "default": ""},
                "calendar_id": _CAL_ID_PROP,
            },
            "required": ["event_id"],
        },
    },
]

DISPATCH = {
    "list_google_calendar_events": list_google_calendar_events,
    "list_google_calendars": list_google_calendars,
    "create_google_calendar_event": create_google_calendar_event,
    "cancel_google_calendar_event": cancel_google_calendar_event,
}
