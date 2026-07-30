from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from auth.ms_graph import graph_get, graph_post, graph_patch

_SGT = ZoneInfo("Asia/Singapore")

# Graph's To Do endpoint has no $filter on tasks, so everything is fetched and
# filtered here. One page is plenty for a personal list; the brief says so
# rather than silently truncating if it isn't.
_PAGE = 100


def _get_list_id(list_name: str = "Tasks") -> str | None:
    data = graph_get("me/todo/lists")
    for lst in data.get("value", []):
        if lst["displayName"].lower() == list_name.lower():
            return lst["id"]
    return None


def list_todo_lists() -> str:
    try:
        data = graph_get("me/todo/lists")
        lists = data.get("value", [])
        if not lists:
            return "No To Do lists found."
        return "Your To Do lists:\n" + "\n".join(f"- {l['displayName']}" for l in lists)
    except Exception as e:
        return f"Error listing To Do lists: {e}"


def list_tasks(list_name: str = "Tasks", filter_status: str = "incomplete") -> str:
    try:
        list_id = _get_list_id(list_name)
        if not list_id:
            return f"To Do list '{list_name}' not found. Use list_todo_lists to see available lists."

        params: dict = {"$top": 100}

        data = graph_get(f"me/todo/lists/{list_id}/tasks", params=params)
        tasks = data.get("value", [])

        # Filter in Python — Graph To Do API doesn't support $filter on tasks
        if filter_status == "completed":
            tasks = [t for t in tasks if t.get("status") == "completed"]
        elif filter_status == "incomplete":
            tasks = [t for t in tasks if t.get("status") != "completed"]

        if not tasks:
            return f"No tasks found in '{list_name}'."

        lines = []
        for t in tasks:
            due = t.get("dueDateTime", {})
            due_str = f" — due {due['dateTime'][:10]}" if due else ""
            importance = " (!)" if t.get("importance") == "high" else ""
            lines.append(f"• [{t['id'][:12]}] {t.get('title', '(untitled)')}{due_str}{importance}")

        return f"Tasks in '{list_name}':\n" + "\n".join(lines)

    except Exception as e:
        return f"List tasks error: {e}"


def add_task(title: str, list_name: str = "Tasks", due_date: str = "") -> str:
    try:
        list_id = _get_list_id(list_name)
        if not list_id:
            return f"To Do list '{list_name}' not found."

        body: dict = {"title": title}
        if due_date:
            body["dueDateTime"] = {"dateTime": f"{due_date}T00:00:00", "timeZone": "UTC"}

        task = graph_post(f"me/todo/lists/{list_id}/tasks", body)
        return f"Task added: '{title}' to '{list_name}' (ID: {task['id'][:12]})"

    except Exception as e:
        return f"Add task error: {e}"


def complete_task(task_id_prefix: str, list_name: str = "Tasks") -> str:
    try:
        list_id = _get_list_id(list_name)
        if not list_id:
            return f"To Do list '{list_name}' not found."

        data = graph_get(f"me/todo/lists/{list_id}/tasks", params={"$select": "id,title", "$top": 100})
        match = next(
            (t for t in data.get("value", []) if t["id"].startswith(task_id_prefix)),
            None,
        )
        if not match:
            return f"No task found with ID starting '{task_id_prefix}'. Use list_tasks to see current task IDs."

        graph_patch(f"me/todo/lists/{list_id}/tasks/{match['id']}", {"status": "completed"})
        return f"Task '{match['title']}' marked as completed."

    except Exception as e:
        return f"Complete task error: {e}"


# ── Ava's daily push ──────────────────────────────────────────────────────────

def _due_date(task: dict) -> str | None:
    """Graph returns dueDateTime as midnight on the due day; the date is the part
    that matters, so the time-of-day and its timezone are ignored deliberately."""
    dt = (task.get("dueDateTime") or {}).get("dateTime")
    return dt[:10] if dt else None


def morning_todo_brief(list_name: str = "Tasks") -> str | None:
    """Ava's daily morning push: every open Microsoft To Do task, grouped by when
    it's due, undated last.

    Returns None only when the list is genuinely empty. Errors return a warning
    instead of None because this is the ONLY daily push — if a broken brief were
    silent it would be indistinguishable from a clear day, and a false all-clear
    is worse than a nag.
    """
    try:
        list_id = _get_list_id(list_name)
        if not list_id:
            return (
                f"⚠️ Morning brief: To Do list '{list_name}' not found. "
                "Ask me to list your To Do lists to see the available names."
            )
        data = graph_get(f"me/todo/lists/{list_id}/tasks", params={"$top": _PAGE})
        raw = data.get("value", [])
    except Exception as e:
        return (
            f"⚠️ Morning brief: couldn't read your To Do list ({str(e)[:120]}). "
            "Your Microsoft sign-in may need refreshing."
        )

    tasks = [t for t in raw if t.get("status") != "completed"]
    if not tasks:
        return None

    today = datetime.now(_SGT).date()
    overdue, due_today, tomorrow, upcoming, undated = [], [], [], [], []

    for t in tasks:
        title = t.get("title") or "(untitled)"
        if t.get("importance") == "high":
            title += " (!)"
        day = _due_date(t)
        try:
            dd = datetime.strptime(day, "%Y-%m-%d").date() if day else None
        except ValueError:
            dd = None
        if dd is None:
            undated.append((None, f"• {title}"))
        elif dd < today:
            overdue.append((dd, f"• {title} (due {day})"))
        elif dd == today:
            due_today.append((dd, f"• {title}"))
        elif dd == today + timedelta(days=1):
            tomorrow.append((dd, f"• {title}"))
        else:
            upcoming.append((dd, f"• {title} (due {day})"))

    sections = []
    for heading, group in (
        ("⏰ Overdue", overdue),
        ("🔴 Today", due_today),
        ("🟠 Tomorrow", tomorrow),
        ("📅 Upcoming", upcoming),
        ("🗓 No due date", undated),
    ):
        if group:
            group.sort(key=lambda x: x[0] or today)
            sections.append(heading + "\n" + "\n".join(line for _, line in group))

    n = len(tasks)
    header = f"☀️ Morning brief — {n} open to-do{'' if n == 1 else 's'}"
    if len(raw) >= _PAGE:
        sections.append(f"(Only the first {_PAGE} tasks were read — there may be more.)")
    return header + "\n\n" + "\n\n".join(sections)


TOOL_DEFS = [
    {
        "name": "list_todo_lists",
        "description": "List all available Microsoft To Do task lists.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_tasks",
        "description": "List tasks from a Microsoft To Do list.",
        "input_schema": {
            "type": "object",
            "properties": {
                "list_name": {"type": "string", "description": "To Do list name (default: 'Tasks')", "default": "Tasks"},
                "filter_status": {
                    "type": "string",
                    "description": "Filter tasks: 'incomplete' (default) shows not-started and in-progress; 'completed' shows only done tasks; '' shows all",
                    "default": "incomplete",
                    "enum": ["incomplete", "completed", ""],
                },
            },
        },
    },
    {
        "name": "add_task",
        "description": "Add a new task to Microsoft To Do.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Task title"},
                "list_name": {"type": "string", "description": "Which To Do list (default: 'Tasks')", "default": "Tasks"},
                "due_date": {"type": "string", "description": "Due date as YYYY-MM-DD (optional)", "default": ""},
            },
            "required": ["title"],
        },
    },
    {
        "name": "complete_task",
        "description": "Mark a Microsoft To Do task as completed.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id_prefix": {"type": "string", "description": "First 12 characters of the task ID shown in list_tasks"},
                "list_name": {"type": "string", "description": "Which To Do list (default: 'Tasks')", "default": "Tasks"},
            },
            "required": ["task_id_prefix"],
        },
    },
]

DISPATCH = {
    "list_todo_lists": list_todo_lists,
    "list_tasks": list_tasks,
    "add_task": add_task,
    "complete_task": complete_task,
}
