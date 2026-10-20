"""
The weekly usage report (studio/CLAUDE.md, "The usability standard", 4 and 5): what the lead session reads every
week to write docs/usage/<date>.md. GET /api/report/weekly?start=YYYY-MM-DD[&format=md]

- start: a Monday (Singapore); default the last full Monday-Sunday week.
- Per person (name only): active days and page views per page.
- The golden paths G1-G5 as funnels over each browser tab's events: starts, completions, the median time, and
  how many reached each step (where people drop).
- Dead clicks and rage clicks (page + control), dialogs closed without their action, errors (grouped),
  friction flags (page, control, words), Ask Studio questions (per page, the question, whether it was answered).
- "Did anyone use what we built last week?": FEATURES, each with the events that count as using it.
- Test walks (localStorage studio.qa = 1 on the page: every event carries qa) are left out of all of it; one line
  says how many.

Read with "Authorization: Bearer <Studio/report_token>" (32 random bytes, 0600, made on first start, compared in
constant time; the owner sees, copies and rotates it in More > Weekly report access) or by the signed-in owner.
"""
import hmac
import json
import os
import re
import secrets
import statistics
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone

import settings

SGT = timezone(timedelta(hours=8))

# What shipped recently, and the events that count as using it (kind, target regex[, detail key=value]).
# Add a row when something ships; drop it after a month.
FEATURES = [
    {"key": "quick_note", "name": "Quick note box under the video", "shipped": "2026-10-19",
     "events": [("action", r"^note_added$", "via=quick"), ("click", r"^#quick-note")]},
    {"key": "edit_again", "name": "Edit it again", "shipped": "2026-10-20",
     "events": [("click", r"^#redo-(one|all)$"), ("action", r"^redo$")]},
    {"key": "send_back_fixes", "name": "Send back with Studio's fixes", "shipped": "2026-10-19",
     "events": [("action", r"^sent_back$", "fixes=true")]},
    {"key": "caption_preview", "name": "Caption preview", "shipped": "2026-10-20",
     "events": [("click", r"data-fold=production\.caption"), ("click", r"^#cap-")]},
    {"key": "hook_options", "name": "Hook options (Use this)", "shipped": "2026-10-20",
     "events": [("click", r"^\[data-hook-use")]},
    {"key": "ideas_groups", "name": "Ideas board groups and the project ⋯ menu", "shipped": "2026-10-19",
     "events": [("click", r"^#plan-tools$"), ("click", r"^\[data-(more|less|tool)"), ("click", r"grp")]},
    {"key": "details_sheet", "name": "Details sheet on an idea", "shipped": "2026-10-20",
     "events": [("click", r"^\[data-details\]"), ("click", r"^\[data-set-(kind|format)")]},
    {"key": "voice_file", "name": "Your voice file", "shipped": "2026-10-20",
     "events": [("click", r"^#voice-(open|switch|write|save)$")]},
    {"key": "note_remove", "name": "Note × (take a note back)", "shipped": "2026-10-20",
     "events": [("click", r"^\[data-note-delete")]},
    {"key": "one_note_box", "name": "One note box: Add a note at a moment, Undo", "shipped": "2026-10-21",
     "events": [("click", r"^#note-jump$"), ("click", r"^\[data-quick-undo"), ("action", r"^note_undone$")]},
    {"key": "team_calls", "name": "The team's Go / Park and Send back", "shipped": "2026-10-21",
     "events": [("action", r"^(idea_decided|sent_back)$", "team=true")]},
    {"key": "toast_open", "name": "Open on a toast (after Go, after Add)", "shipped": "2026-10-21",
     "events": [("action", r"^toast_open$")]},
    {"key": "month_gap_add", "name": "+ Add on a month's gap", "shipped": "2026-10-21",
     "events": [("action", r"^month_gap_add$"), ("click", r"^\[data-add-kind")]},
    {"key": "team_jobs", "name": "The team's Plan: Your jobs", "shipped": "2026-10-21",
     "events": [("action", r"^home_film$"), ("click", r"^\.hm-bryan")]},
    {"key": "stage_menu", "name": "Review's More stages (phone)", "shipped": "2026-10-21",
     "events": [("action", r"^stage_menu$"), ("click", r"^#tabs-more$")]},
]

# The golden paths. Each step: alternatives (kind, regex on the target, or on the page for views[, detail k=v]).
# A tab "starts" a path at its first step and completes it when it reaches every required step in order.
FUNNELS = [
    {"key": "G1", "name": "Review a draft", "who": "", "steps": [
        {"name": "Opened a draft waiting for review", "match": [("view", r"^(draft|carousel)$", "waiting=true")]},
        {"name": "Played it", "optional": True, "match": [("action", r"^video_played$")]},
        {"name": "Left a note", "optional": True, "match": [("action", r"^note_added$")]},
        {"name": "Approved, sent back or rejected", "match": [("action", r"^(approved|sent_back|rejected)$")]},
    ]},
    {"key": "G2", "name": "Decide on ideas", "who": "", "steps": [
        {"name": "Saw ideas waiting for a call", "match": [("view", r"^(home|ideas|idea)$", "calls=true")]},
        {"name": "Go, Park or Dismiss", "match": [("action", r"^idea_decided$")]},
        {"name": "Make this", "optional": True, "match": [("action", r"^make_this$")]},
    ]},
    {"key": "G3", "name": "Film or upload to a shot", "who": "", "steps": [
        {"name": "Opened an idea with shots to film", "match": [("view", r"^(idea|today)$", "film=true")]},
        {"name": "Pressed Film or Upload", "match": [("click", r"^\[data-(up-film|up-lib|up-choose|primary-attach)")]},
        {"name": "The upload finished", "match": [("action", r"^upload_done$")]},
    ]},
    {"key": "G4", "name": "Plan the month", "who": "", "steps": [
        {"name": "Pressed + New idea", "match": [("click", r"^#(home-new-idea|plan-add-one)$"), ("click", r"^\[data-add-kind")]},
        {"name": "Saved the idea", "match": [("action", r"^idea_created$")]},
        {"name": "Gave it a kind", "match": [("action", r"^idea_kind$")]},
        {"name": "Go", "optional": True, "match": [("action", r"^idea_decided$", "decision=go")]},
    ]},
    {"key": "G5", "name": "Schedule an approved post", "who": "owner", "steps": [
        {"name": "Opened an approved draft", "match": [("view", r"^(draft|carousel|idea)$", "approved=true")]},
        {"name": "Pressed Schedule", "match": [("click", r"^\[data-post-schedule")]},
        {"name": "Scheduled it", "match": [("action", r"^post_scheduled$")]},
    ]},
]


# ── The key ──────────────────────────────────────────────────────────────────

def token(create: bool = True) -> str:
    """The report's key, made (0600) the first time it's asked for."""
    path = settings.REPORT_TOKEN_FILE
    try:
        value = path.read_text(encoding="utf-8").strip()
        if len(value) >= 40:
            return value
    except OSError:
        pass
    if not create:
        return ""
    return rotate()


def rotate() -> str:
    """A new key; the old one stops working at once."""
    path = settings.REPORT_TOKEN_FILE
    value = secrets.token_urlsafe(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(value + "\n")
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return value


def token_ok(header: str) -> bool:
    """An "Authorization: Bearer <key>" header with the report's key (constant time)."""
    given = header[7:].strip() if header.lower().startswith("bearer ") else ""
    expected = token(create=False)
    return bool(given and expected) and hmac.compare_digest(given.encode(), expected.encode())


# ── The week ─────────────────────────────────────────────────────────────────

def last_full_week(today: date | None = None) -> date:
    today = today or datetime.now(SGT).date()
    return today - timedelta(days=today.weekday() + 7)


def window(start: date) -> tuple[str, str]:
    a = datetime(start.year, start.month, start.day, tzinfo=SGT)
    b = a + timedelta(days=7)
    return (a.astimezone(timezone.utc).isoformat(timespec="seconds"), b.astimezone(timezone.utc).isoformat(timespec="seconds"))


def _detail(row) -> dict:
    try:
        return json.loads(row["detail"] or "{}") or {}
    except (ValueError, TypeError):
        return {}


def _matches(event, alt) -> bool:
    kind, pattern = alt[0], alt[1]
    if event["kind"] != kind:
        return False
    field = event["page"] if kind == "view" else (event["target"] or "")
    if not re.search(pattern, field or ""):
        return False
    if len(alt) > 2:
        key, value = alt[2].split("=", 1)
        got = event["d"].get(key)
        if value in ("true", "false"):
            return bool(got) == (value == "true")
        return str(got) == value
    return True


def _funnel(f: dict, sessions: dict) -> dict:
    steps = f["steps"]
    reached = [0] * len(steps)
    starts = completes = 0
    times = []
    drops = Counter()
    for events in sessions.values():
        if f["who"] and not any(e["role"] == f["who"] for e in events):
            continue
        i0 = next((k for k, e in enumerate(events) if any(_matches(e, a) for a in steps[0]["match"])), None)
        if i0 is None:
            continue
        starts += 1
        reached[0] += 1
        t0 = events[i0]["at"]
        pos, last_required, done, end = i0, 0, True, t0
        for s in range(1, len(steps)):
            hit = next((k for k in range(pos + 1, len(events)) if any(_matches(events[k], a) for a in steps[s]["match"])), None)
            if hit is None:
                if not steps[s].get("optional"):
                    done = False
                    drops[steps[last_required]["name"]] += 1
                    break
                continue
            reached[s] += 1
            pos = hit
            if not steps[s].get("optional"):
                last_required, end = s, events[hit]["at"]
        if done:
            completes += 1
            times.append((datetime.fromisoformat(end) - datetime.fromisoformat(t0)).total_seconds())
    return {"key": f["key"], "name": f["name"], "starts": starts, "completions": completes,
            "median_seconds": round(statistics.median(times)) if times else None,
            "steps": [{"name": s["name"], "optional": bool(s.get("optional")), "reached": reached[k]} for k, s in enumerate(steps)],
            "dropped_after": [{"step": name, "sessions": n} for name, n in drops.most_common()]}


def weekly(conn, start: date | None = None) -> dict:
    start = start or last_full_week()
    since, until = window(start)
    rows = conn.execute("SELECT * FROM usage_events WHERE at >= ? AND at < ? ORDER BY at, id", (since, until)).fetchall()
    qa_left_out = sum(1 for r in rows if r["qa"])
    rows = [r for r in rows if not r["qa"]]
    events = [{"at": r["at"], "user": r["user_id"], "name": r["user_name"] or r["user_id"].split("@")[0], "role": r["role"],
               "session": r["session"], "page": r["page"], "route": r["route"], "kind": r["kind"], "target": r["target"],
               "d": _detail(r)} for r in rows]
    names = {}
    for e in events:
        names[e["user"]] = e["name"]

    # People
    people = defaultdict(lambda: {"days": set(), "views": Counter(), "events": 0})
    for e in events:
        p = people[e["user"]]
        p["events"] += 1
        p["days"].add(datetime.fromisoformat(e["at"]).astimezone(SGT).date().isoformat())
        if e["kind"] == "view":
            p["views"][e["page"]] += 1
    users = [{"name": names[u], "active_days": len(p["days"]), "page_views": dict(p["views"].most_common()), "events": p["events"]}
             for u, p in sorted(people.items(), key=lambda kv: -len(kv[1]["days"]))]

    # Funnels, per tab
    sessions = defaultdict(list)
    for e in events:
        if e["session"] and e["session"] != "server":
            sessions[(e["user"], e["session"])].append(e)
    funnels = [_funnel(f, sessions) for f in FUNNELS]

    def top(kind, n=15):
        c = Counter((e["page"], e["target"] or "?") for e in events if e["kind"] == kind)
        return [{"page": p, "control": t, "count": k} for (p, t), k in c.most_common(n)]

    cancelled = Counter((e["page"], e["target"] or "dialog") for e in events
                        if e["kind"] == "dialog" and e["d"].get("result") == "cancelled")
    errors = Counter()
    for e in events:
        if e["kind"] == "error":
            errors[(e["page"], "js", f'{e["d"].get("msg", "")} ({e["d"].get("src", "")})')] += 1
        elif e["kind"] == "api_error":
            errors[(e["page"], "api", f'{e["d"].get("method", "")} {e["d"].get("path", "")} -> {e["d"].get("status", "")}')] += 1

    flags = [{"at": r["at"], "who": r["user_name"], "page": r["page"], "route": r["route"], "control": r["control"],
              "words": r["words"], "viewport": r["viewport"], "done": bool(r["done_at"])}
             for r in conn.execute("SELECT * FROM friction_flags WHERE at >= ? AND at < ? AND qa IS NULL ORDER BY id", (since, until))]
    qa_left_out += conn.execute("SELECT COUNT(*) FROM friction_flags WHERE at >= ? AND at < ? AND qa IS NOT NULL", (since, until)).fetchone()[0]
    qrows = conn.execute("SELECT * FROM help_questions WHERE at >= ? AND at < ? AND qa IS NULL ORDER BY id", (since, until)).fetchall()
    qa_left_out += conn.execute("SELECT COUNT(*) FROM help_questions WHERE at >= ? AND at < ? AND qa IS NOT NULL", (since, until)).fetchone()[0]
    per_page = Counter(r["page"] for r in qrows)
    questions = [{"page": r["page"], "who": r["user_name"], "question": (r["question"] or "")[:300],
                  "answered": bool(r["answered"]), "unavailable": bool(r["error"]),
                  "helpful": None if r["helpful"] is None else bool(r["helpful"])} for r in qrows]

    features = []
    for f in FEATURES:
        used = [e for e in events if any(_matches(e, a) for a in f["events"])]
        features.append({"key": f["key"], "name": f["name"], "shipped": f["shipped"],
                         "users": sorted({names[e["user"]] for e in used}), "uses": len(used)})

    return {
        "week": {"start": start.isoformat(), "end": (start + timedelta(days=6)).isoformat(), "timezone": "Asia/Singapore"},
        "capture_on": settings.USAGE,
        "events": len(events), "qa_left_out": qa_left_out,
        "users": users, "funnels": funnels,
        "dead_clicks": top("dead"), "rage_clicks": top("rage"),
        "cancelled_dialogs": [{"page": p, "dialog": t, "count": k} for (p, t), k in cancelled.most_common(15)],
        "errors": [{"page": p, "type": kind, "error": msg, "count": k} for (p, kind, msg), k in errors.most_common(20)],
        "friction_flags": flags,
        "help": {"asked": len(qrows), "per_page": dict(per_page.most_common()), "answered": sum(1 for q in questions if q["answered"]),
                 "questions": questions},
        "features": features,
    }


def markdown(r: dict) -> str:
    w = r["week"]
    out = [f"# Studio usage, week of {w['start']} to {w['end']} (Singapore)", ""]
    out.append(f"{r['events']} events" + ("" if r["capture_on"] else " (capture is off: STUDIO_USAGE=0)") +
               f". Test walks left out: {r['qa_left_out']} events (qa).")
    out += ["", "## People", ""]
    if not r["users"]:
        out.append("Nobody used Studio this week (or capture was off).")
    for u in r["users"]:
        views = ", ".join(f"{p} {n}" for p, n in u["page_views"].items()) or "no page views"
        days = f"{u['active_days']} active day{'' if u['active_days'] == 1 else 's'}"
        out.append(f"- **{u['name']}**: {days}; views: {views}")
    out += ["", "## Golden paths", "", "| Path | Starts | Completed | Median time | Steps reached | Dropped after |", "|---|---|---|---|---|---|"]
    for f in r["funnels"]:
        steps = " → ".join(f"{s['name']}{' (optional)' if s['optional'] else ''} {s['reached']}" for s in f["steps"])
        drop = "; ".join(f"{d['step']} ({d['sessions']})" for d in f["dropped_after"]) or "–"
        med = f"{f['median_seconds']} s" if f["median_seconds"] is not None else "–"
        out.append(f"| {f['key']} {f['name']} | {f['starts']} | {f['completions']} | {med} | {steps} | {drop} |")

    def table(title, rows, cols):
        nonlocal out
        out += ["", f"## {title}", ""]
        if not rows:
            out.append("None.")
            return
        out += ["| " + " | ".join(c for c, _ in cols) + " |", "|" + "---|" * len(cols)]
        for row in rows:
            out.append("| " + " | ".join(str(row.get(k, "")).replace("|", "/").replace("\n", " ") for _, k in cols) + " |")

    table("Dead clicks", r["dead_clicks"], [("Page", "page"), ("Control", "control"), ("Count", "count")])
    table("Rage clicks", r["rage_clicks"], [("Page", "page"), ("Control", "control"), ("Count", "count")])
    table("Dialogs closed without their action", r["cancelled_dialogs"], [("Page", "page"), ("Dialog", "dialog"), ("Count", "count")])
    table("Errors", r["errors"], [("Page", "page"), ("Type", "type"), ("Error", "error"), ("Count", "count")])
    table("Friction flags", r["friction_flags"], [("When", "at"), ("Who", "who"), ("Page", "page"), ("Control", "control"),
                                                  ("Words", "words"), ("Screen", "viewport"), ("Done", "done")])
    h = r["help"]
    out += ["", "## Ask Studio", "", f"{h['asked']} questions, {h['answered']} answered from Studio's notes."
            + (" Per page: " + ", ".join(f"{p} {n}" for p, n in h["per_page"].items()) + "." if h["per_page"] else "")]
    for q in h["questions"]:
        state = "answered" if q["answered"] else "Claude unavailable" if q["unavailable"] else "not covered"
        helped = "" if q["helpful"] is None else (", helped" if q["helpful"] else ", didn't help")
        out.append(f"- ({q['page']}, {q['who']}, {state}{helped}) {q['question']}")
    out += ["", "## Did anyone use what we built last week?", "", "| Feature | Shipped | Users | Uses |", "|---|---|---|---|"]
    for f in r["features"]:
        out.append(f"| {f['name']} | {f['shipped']} | {', '.join(f['users']) or 'nobody'} | {f['uses']} |")
    return "\n".join(out) + "\n"
