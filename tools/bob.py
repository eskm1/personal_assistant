"""
Delegation to Bob — the UM Pod, Urban Makers' WhatsApp agent on the quote engine.

Bob lives in the project WhatsApp groups (whatsapp-pod in the quote-engine repo),
but his working memory is the same Supabase project this bot already talks to:
wa_tasks (the single to-do list), project_chat (rolling digest), wa_daily_reports
(evening reports), timeline_items, punchlist_items. So "asking Bob to do X" from
Telegram writes to the exact tables Bob manages — the task then shows up in his
evening group report, his 06:00 morning DM digest to every admin, and the
project hub's Tasks panel. No WhatsApp round-trip needed.

Reads are immediate; anything that changes Bob's world is staged behind the
confirmation gate, mainly so Bryan sees WHICH project got matched before it
lands in a report the whole team reads.
"""
import json
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

from config import UMCPM_SUPABASE_URL, UMCPM_SERVICE_KEY
from tools import pending
from tools.wiki import WRITE_TOOL_DEFS as WIKI_WRITE_TOOL_DEFS, WRITE_DISPATCH as WIKI_WRITE_DISPATCH

_REST = f"{UMCPM_SUPABASE_URL.rstrip('/')}/rest/v1"
_SGT = ZoneInfo("Asia/Singapore")

CREATED_BY = "Bryan (via Ava)"


def _headers(extra: dict | None = None) -> dict:
    h = {
        "apikey": UMCPM_SERVICE_KEY,
        "Authorization": f"Bearer {UMCPM_SERVICE_KEY}",
        "Content-Type": "application/json",
    }
    if extra:
        h.update(extra)
    return h


def _safe_term(term: str) -> str:
    return re.sub(r'[,()"\*%]', " ", term).strip()


def _find_projects(query: str) -> list[dict]:
    term = _safe_term(query)
    if not term:
        return []
    params = {
        "select": "id,slug,project_name,client_name,stage,status",
        "or": f"(project_name.ilike.*{term}*,slug.ilike.*{term}*,client_name.ilike.*{term}*)",
        "order": "created_at.desc",
        "limit": "5",
    }
    r = requests.get(f"{_REST}/projects", headers=_headers(), params=params, timeout=15)
    r.raise_for_status()
    return r.json()


def _ambiguous(hits: list[dict]) -> str:
    return "Several projects match — which one?\n" + "\n".join(
        f"• {p['project_name']}" + (f" ({p['client_name']})" if p.get("client_name") else "")
        for p in hits
    )


def _urgency(due: str | None) -> str:
    """Mirror the pod's morning-digest flags: overdue / today / tomorrow / soon."""
    if not due:
        return ""
    today = datetime.now(_SGT).date()
    d = datetime.strptime(due, "%Y-%m-%d").date()
    if d < today:
        return " ⏰ OVERDUE"
    if d == today:
        return " 🔴 today"
    if d == today + timedelta(days=1):
        return " 🟠 tomorrow"
    if d <= today + timedelta(days=3):
        return " 🟡 soon"
    return ""


def _fmt_task(t: dict, n: int | None = None) -> str:
    line = f"{n}. " if n else "• "
    line += t["description"]
    if t.get("due_date"):
        line += f" (due {t['due_date']}{_urgency(t['due_date'])})"
    if t.get("owner"):
        line += f" — {t['owner']}"
    return line


def _open_tasks(project_id: str) -> list[dict]:
    params = {
        "select": "id,description,owner,due_date,created_by",
        "project_id": f"eq.{project_id}",
        "status": "eq.open",
        "order": "due_date.asc.nullslast,created_at.asc",
    }
    r = requests.get(f"{_REST}/wa_tasks", headers=_headers(), params=params, timeout=15)
    r.raise_for_status()
    return r.json()


# ── Read tools ────────────────────────────────────────────────────────────────

def bob_list_tasks(project: str = "") -> str:
    """List open tasks on Bob's list — one project's, or all projects grouped."""
    try:
        if project.strip():
            hits = _find_projects(project)
            if not hits:
                return f"Bob has no project matching '{project}'."
            if len(hits) > 1:
                return _ambiguous(hits)
            tasks = _open_tasks(hits[0]["id"])
            if not tasks:
                return f"✅ No open tasks on {hits[0]['project_name']}."
            return f"📝 Open tasks — {hits[0]['project_name']}:\n" + "\n".join(
                _fmt_task(t, i + 1) for i, t in enumerate(tasks)
            )
        params = {
            "select": "id,description,owner,due_date,project_id,projects(project_name)",
            "status": "eq.open",
            "order": "due_date.asc.nullslast",
            "limit": "100",
        }
        r = requests.get(f"{_REST}/wa_tasks", headers=_headers(), params=params, timeout=15)
        r.raise_for_status()
        rows = r.json()
        if not rows:
            return "✅ Bob has no open tasks on any project."
        by_project: dict[str, list] = {}
        for t in rows:
            name = (t.get("projects") or {}).get("project_name") or "(unknown project)"
            by_project.setdefault(name, []).append(t)
        out = []
        for name, tasks in by_project.items():
            out.append(f"📁 {name}\n" + "\n".join(_fmt_task(t) for t in tasks))
        return "📝 Bob's open tasks, all projects:\n\n" + "\n\n".join(out)
    except Exception as e:
        return f"Bob task list error: {e}"


def bob_project_brief(project: str) -> str:
    """Bob's current picture of one project: digest summary, latest report, open tasks."""
    try:
        hits = _find_projects(project)
        if not hits:
            return f"Bob has no project matching '{project}'."
        if len(hits) > 1:
            return _ambiguous(hits)
        p = hits[0]
        out = [f"📁 {p['project_name']}"
               + (f" — {p['client_name']}" if p.get("client_name") else "")
               + (f" · stage: {p['stage']}" if p.get("stage") else "")]

        r = requests.get(f"{_REST}/project_chat", headers=_headers(), params={
            "select": "summary,updated_at", "project_id": f"eq.{p['id']}", "limit": "1",
        }, timeout=15)
        r.raise_for_status()
        chat = r.json()
        if chat and chat[0].get("summary"):
            out.append(f"\nChat digest (as of {chat[0]['updated_at'][:10]}):\n{chat[0]['summary']}")

        r = requests.get(f"{_REST}/wa_daily_reports", headers=_headers(), params={
            "select": "report_date,headline,report",
            "project_id": f"eq.{p['id']}",
            "order": "report_date.desc", "limit": "1",
        }, timeout=15)
        r.raise_for_status()
        reports = r.json()
        if reports:
            rep = reports[0]
            out.append(f"\nLatest daily report ({rep['report_date']}): {rep.get('headline') or '(no headline)'}")
            body = rep.get("report") or {}
            for key, label in (("new_info", "New"), ("decisions", "Decisions"),
                               ("action_items", "Actions"), ("open_questions", "Open questions")):
                items = body.get(key) or []
                if items:
                    out.append(f"{label}: " + "; ".join(str(x) for x in items[:5]))

        tasks = _open_tasks(p["id"])
        out.append(f"\n📝 Open tasks ({len(tasks)}):" if tasks else "\n✅ No open tasks.")
        out.extend(_fmt_task(t, i + 1) for i, t in enumerate(tasks[:15]))
        if len(tasks) > 15:
            out.append(f"(+{len(tasks) - 15} more — bob_list_tasks for all)")
        return "\n".join(out)
    except Exception as e:
        return f"Bob brief error: {e}"


def bob_updates(days: int = 2) -> str:
    """What changed in Bob's world recently: done tasks, new tasks, overdue, report headlines."""
    try:
        days = max(1, min(int(days), 14))
        now = datetime.now(_SGT)
        since_iso = (now - timedelta(days=days)).isoformat()
        since_date = (now - timedelta(days=days)).date().isoformat()
        sel = "id,description,owner,due_date,status,created_by,created_at,done_at,projects(project_name)"
        pname = lambda t: (t.get("projects") or {}).get("project_name") or "(unknown project)"

        r = requests.get(f"{_REST}/wa_tasks", headers=_headers(), params={
            "select": sel, "status": "eq.done", "done_at": f"gte.{since_iso}",
            "order": "done_at.desc", "limit": "30",
        }, timeout=15)
        r.raise_for_status()
        done = r.json()

        r = requests.get(f"{_REST}/wa_tasks", headers=_headers(), params={
            "select": sel, "created_at": f"gte.{since_iso}",
            "order": "created_at.desc", "limit": "30",
        }, timeout=15)
        r.raise_for_status()
        # Skip ones Bryan added himself — he knows about those.
        added = [t for t in r.json() if t.get("created_by") != CREATED_BY]

        r = requests.get(f"{_REST}/wa_tasks", headers=_headers(), params={
            "select": sel, "status": "eq.open", "due_date": f"lt.{now.date().isoformat()}",
            "order": "due_date.asc", "limit": "30",
        }, timeout=15)
        r.raise_for_status()
        overdue = r.json()

        r = requests.get(f"{_REST}/wa_daily_reports", headers=_headers(), params={
            "select": "report_date,headline,projects(project_name)",
            "report_date": f"gte.{since_date}",
            "order": "report_date.desc", "limit": "15",
        }, timeout=15)
        r.raise_for_status()
        reports = [x for x in r.json() if x.get("headline")]

        out = [f"Bob's last {days} day(s):"]
        if done:
            out.append("\n✅ Completed:")
            out.extend(f"• [{pname(t)}] {t['description']}" for t in done)
        if added:
            out.append("\n🆕 New tasks (added by the team / Bob):")
            out.extend(
                f"• [{pname(t)}] {t['description']}"
                + (f" (due {t['due_date']}{_urgency(t['due_date'])})" if t.get("due_date") else "")
                + (f" — added by {t['created_by']}" if t.get("created_by") else "")
                for t in added
            )
        if overdue:
            out.append("\n⏰ Overdue — needs attention:")
            out.extend(
                f"• [{pname(t)}] {t['description']} (due {t['due_date']})"
                + (f" — {t['owner']}" if t.get("owner") else "")
                for t in overdue
            )
        if reports:
            out.append("\n📋 Daily report headlines:")
            out.extend(f"• {x['report_date']} [{pname(x)}] {x['headline']}" for x in reports)
        if len(out) == 1:
            return f"Quiet — nothing completed, added, overdue, or reported in Bob's world in the last {days} day(s)."
        return "\n".join(out)
    except Exception as e:
        return f"Bob updates error: {e}"


def morning_brief() -> str | None:
    """Ava's daily push: tasks due today + everything that changed in the last day.
    Returns None when there is nothing worth waking Bryan up for (or on error —
    the caller logs; a broken morning push must not page him daily)."""
    try:
        today = datetime.now(_SGT).date().isoformat()
        r = requests.get(f"{_REST}/wa_tasks", headers=_headers(), params={
            "select": "description,owner,projects(project_name)",
            "status": "eq.open", "due_date": f"eq.{today}",
            "order": "created_at.asc", "limit": "20",
        }, timeout=15)
        r.raise_for_status()
        due_today = r.json()

        sections = []
        if due_today:
            pname = lambda t: (t.get("projects") or {}).get("project_name") or "(unknown project)"
            sections.append("🔴 Due today:\n" + "\n".join(
                f"• [{pname(t)}] {t['description']}" + (f" — {t['owner']}" if t.get("owner") else "")
                for t in due_today
            ))

        updates = bob_updates(days=1)
        if not updates.startswith(("Quiet", "Bob updates error")):
            # Drop the "Bob's last N day(s):" header — the brief has its own.
            sections.append(updates.split("\n", 1)[1].strip())

        if not sections:
            return None
        return "☀️ Morning brief — Bob's world\n\n" + "\n\n".join(sections)
    except Exception:
        return None


# ── Write executors (run only after confirmation) ─────────────────────────────

def _do_add_task(project: dict, description: str, due_date: str, owner: str) -> str:
    try:
        row = {
            "project_id": project["id"],
            "description": description,
            "owner": owner or None,
            "due_date": due_date or None,
            "created_by": CREATED_BY,
            "source": "app",
        }
        r = requests.post(f"{_REST}/wa_tasks", headers=_headers({"Prefer": "return=representation"}),
                          data=json.dumps(row), timeout=15)
        r.raise_for_status()
        return (
            f"✅ Handed to Bob — task on {project['project_name']}:\n{description}"
            + (f"\nDue {due_date}{_urgency(due_date)}" if due_date else "")
            + (f" · owner: {owner}" if owner else "")
            + "\nIt's on the project hub's Tasks list now, joins Bob's evening report in the group, "
              "and every admin's 06:00 morning digest."
        )
    except Exception as e:
        return f"❌ Could not add the task: {e}"


def _do_complete_task(task: dict, project_name: str) -> str:
    try:
        r = requests.patch(
            f"{_REST}/wa_tasks?id=eq.{task['id']}", headers=_headers(),
            data=json.dumps({"status": "done", "done_at": datetime.now(_SGT).isoformat()}),
            timeout=15,
        )
        r.raise_for_status()
        return f"✅ Marked done on {project_name}: {task['description']}"
    except Exception as e:
        return f"❌ Could not complete the task: {e}"


# ── Write tools (stage via the confirmation gate) ─────────────────────────────

def bob_add_task(project: str, description: str, due_date: str = "", owner: str = "") -> str:
    """Stage handing Bob a task (does not write until confirmed)."""
    try:
        description = description.strip()
        if not description:
            return "Give Bob a task description."
        if due_date and not re.match(r"^\d{4}-\d{2}-\d{2}$", due_date):
            return f"due_date must be YYYY-MM-DD (got '{due_date}') — convert the date first."
        hits = _find_projects(project)
        if not hits:
            return f"Bob has no project matching '{project}'. Try another name, or list_umcpm_projects."
        if len(hits) > 1:
            return _ambiguous(hits)
        p = hits[0]
        summary = (
            f"Hand Bob a task on 📁 {p['project_name']}: '{description}'"
            + (f", due {due_date}" if due_date else "")
            + (f", owner {owner}" if owner else "")
        )
        return pending.stage(summary, lambda: _do_add_task(p, description, due_date, owner.strip()))
    except Exception as e:
        return f"Bob task error: {e}"


def bob_complete_task(project: str, task: str) -> str:
    """Stage marking one of Bob's open tasks done (does not write until confirmed)."""
    try:
        hits = _find_projects(project)
        if not hits:
            return f"Bob has no project matching '{project}'."
        if len(hits) > 1:
            return _ambiguous(hits)
        p = hits[0]
        tasks = _open_tasks(p["id"])
        if not tasks:
            return f"✅ No open tasks on {p['project_name']} to complete."
        needle = task.strip().lower()
        matches = [t for t in tasks if needle in t["description"].lower()]
        if not matches:
            return (
                f"No open task on {p['project_name']} matching '{task}'. Open tasks:\n"
                + "\n".join(_fmt_task(t, i + 1) for i, t in enumerate(tasks))
            )
        if len(matches) > 1:
            return "Several tasks match — which one?\n" + "\n".join(_fmt_task(t) for t in matches)
        t = matches[0]
        summary = f"Mark Bob's task done on 📁 {p['project_name']}: '{t['description']}'"
        return pending.stage(summary, lambda: _do_complete_task(t, p["project_name"]))
    except Exception as e:
        return f"Bob complete error: {e}"


# ── Tool definitions (Anthropic schema) ──────────────────────────────────────

TOOL_DEFS = [
    {
        "name": "bob_add_task",
        "description": (
            "Hand Bob (the Urban Makers WhatsApp project agent) a task on a project — use when Bryan says "
            "'ask Bob to…' schedule a task, a site meeting, a reminder, anything project-related with an "
            "optional due date and owner. Does NOT write immediately: it stages the task showing which "
            "project matched, for Bryan to confirm. Once confirmed, the task lands on Bob's single task "
            "list (wa_tasks): the project hub's Tasks panel, Bob's evening report in the project's WhatsApp "
            "group, and the 06:00 morning digest he DMs every admin. Convert natural-language dates "
            "('Friday', 'tomorrow') to YYYY-MM-DD yourself. For a meeting, phrase the description as e.g. "
            "'Site meeting with client 2pm' and set due_date to the meeting day — and offer to also put it "
            "on Bryan's own Outlook calendar, which Bob's list does not do."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Project name/slug/client to match (partial ok)"},
                "description": {"type": "string", "description": "What needs doing, concise"},
                "due_date": {"type": "string", "description": "Due date YYYY-MM-DD (optional)", "default": ""},
                "owner": {"type": "string", "description": "Assignee name, e.g. 'Ben' (optional)", "default": ""},
            },
            "required": ["project", "description"],
        },
    },
    {
        "name": "bob_list_tasks",
        "description": (
            "List open tasks on Bob's task list — for one project, or across ALL projects grouped by "
            "project with urgency flags (overdue/today/tomorrow), same as Bob's morning digest."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Project to filter to; omit for all projects", "default": ""},
            },
        },
    },
    {
        "name": "bob_complete_task",
        "description": (
            "Mark one of Bob's open tasks as done, matched by project + a fragment of the task description. "
            "Stages for confirmation; if several tasks match you get the list back to disambiguate."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Project the task belongs to"},
                "task": {"type": "string", "description": "Fragment of the task description to match"},
            },
            "required": ["project", "task"],
        },
    },
    {
        "name": "bob_updates",
        "description": (
            "What has changed in Bob's world recently, across ALL projects: tasks completed, new tasks "
            "added by the team or Bob (not Bryan's own), overdue tasks needing attention, and daily report "
            "headlines. Use for 'any updates from Bob?' / 'what has Bob done?' / 'anything I need to "
            "resolve?'. Default window 2 days; up to 14."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "description": "Look-back window in days (1-14, default 2)", "default": 2},
            },
        },
    },
    # Wiki writes are Bob's domain — defined in tools/wiki.py, exposed here.
    *WIKI_WRITE_TOOL_DEFS,
    {
        "name": "bob_project_brief",
        "description": (
            "Ask Bob for his current picture of one project: the rolling WhatsApp chat digest, the latest "
            "evening report (headline, decisions, actions, open questions), and open tasks. Use for "
            "'ask Bob what's happening on X' / 'any updates from the Tan project?'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Project name/slug/client to match"},
            },
            "required": ["project"],
        },
    },
]

DISPATCH = {
    "bob_add_task": bob_add_task,
    "bob_list_tasks": bob_list_tasks,
    "bob_complete_task": bob_complete_task,
    "bob_project_brief": bob_project_brief,
    "bob_updates": bob_updates,
    **WIKI_WRITE_DISPATCH,
}
