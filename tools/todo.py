from auth.ms_graph import graph_get, graph_post, graph_patch


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
