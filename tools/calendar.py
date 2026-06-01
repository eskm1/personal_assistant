from datetime import datetime, timezone, timedelta
from auth.ms_graph import graph_get, graph_post, graph_delete
from config import USER_TIMEZONE


def list_calendar_events(start: str = "", end: str = "") -> str:
    try:
        now = datetime.now(timezone.utc)
        start = start or now.strftime("%Y-%m-%dT%H:%M:%S")
        end = end or (now + timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S")

        params = {
            "startDateTime": start,
            "endDateTime": end,
            "$select": "id,subject,start,end,location,organizer,isAllDay",
            "$top": 25,
            "$orderby": "start/dateTime",
        }
        data = graph_get("me/calendarView", params=params)
        events = data.get("value", [])

        if not events:
            return f"No calendar events between {start[:10]} and {end[:10]}."

        lines = []
        for e in events:
            loc = e.get("location", {}).get("displayName", "")
            start_dt = e["start"]["dateTime"][:16].replace("T", " ")
            end_dt = e["end"]["dateTime"][:16].replace("T", " ")
            line = (
                f"ID: {e['id'][:20]}...\n"
                f"Title: {e.get('subject', '(no title)')}\n"
                f"When: {start_dt} → {end_dt}\n"
            )
            if loc:
                line += f"Location: {loc}\n"
            lines.append(line)

        return "\n---\n".join(lines)

    except Exception as e:
        return f"Calendar list error: {e}"


def create_calendar_event(
    title: str,
    start: str,
    end: str,
    location: str = "",
    attendees: str = "",
    notes: str = "",
) -> str:
    try:
        body: dict = {
            "subject": title,
            "start": {"dateTime": start, "timeZone": USER_TIMEZONE},
            "end": {"dateTime": end, "timeZone": USER_TIMEZONE},
        }
        if location:
            body["location"] = {"displayName": location}
        if notes:
            body["body"] = {"contentType": "Text", "content": notes}
        if attendees:
            body["attendees"] = [
                {"emailAddress": {"address": a.strip()}, "type": "required"}
                for a in attendees.split(",")
                if a.strip()
            ]

        event = graph_post("me/events", body)
        return f"Event created: '{title}' starting {start} (ID: {event.get('id', '')[:20]}...)"

    except Exception as e:
        return f"Calendar create error: {e}"


def cancel_calendar_event(event_id: str) -> str:
    try:
        graph_delete(f"me/events/{event_id}")
        return "Event cancelled and removed from your calendar."
    except Exception as e:
        return f"Calendar cancel error: {e}"


TOOL_DEFS = [
    {
        "name": "list_calendar_events",
        "description": "List Outlook calendar events in a date range. Defaults to the next 7 days if no range is given.",
        "input_schema": {
            "type": "object",
            "properties": {
                "start": {"type": "string", "description": "Start datetime ISO 8601 e.g. '2024-06-01T00:00:00' (defaults to now)", "default": ""},
                "end": {"type": "string", "description": "End datetime ISO 8601 (defaults to 7 days from now)", "default": ""},
            },
        },
    },
    {
        "name": "create_calendar_event",
        "description": (
            "Create a new event on the user's Outlook calendar. "
            "IMPORTANT: Always confirm title, start time, end time, and attendees with the user before calling."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Event title"},
                "start": {"type": "string", "description": "Start datetime ISO 8601 e.g. '2024-06-01T14:00:00'"},
                "end": {"type": "string", "description": "End datetime ISO 8601"},
                "location": {"type": "string", "description": "Location (optional)", "default": ""},
                "attendees": {"type": "string", "description": "Comma-separated attendee email addresses (optional)", "default": ""},
                "notes": {"type": "string", "description": "Event description/notes (optional)", "default": ""},
            },
            "required": ["title", "start", "end"],
        },
    },
    {
        "name": "cancel_calendar_event",
        "description": "Cancel and delete a calendar event by its ID. Always confirm with the user before calling.",
        "input_schema": {
            "type": "object",
            "properties": {
                "event_id": {"type": "string", "description": "Full event ID from list_calendar_events"},
            },
            "required": ["event_id"],
        },
    },
]

DISPATCH = {
    "list_calendar_events": list_calendar_events,
    "create_calendar_event": create_calendar_event,
    "cancel_calendar_event": cancel_calendar_event,
}
