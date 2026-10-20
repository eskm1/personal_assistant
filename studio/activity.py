"""
Usage capture: what people do in Review and the planner, so the weekly report can say what works, what
confuses and whether anyone used what was built (studio/CLAUDE.md, "The usability standard").

The pages' shared script (cloud/planner/capture.js) batches small events and posts them to /api/usage:
page views, presses on controls, dead clicks (a press that changed nothing), rage clicks, dialogs closed
without their action, errors and failed API calls, and a few named steps of the golden paths ("action").
Never content: a control is described by its id, a data-* action name, a fixed label from the app's own
chrome or its tag and classes, never by the words on a card, a title, a caption or a note. This module
checks that again on the way in (lengths, shapes, kinds) and keeps the rows settings.USAGE_DAYS days.

  events: [{t: ms since 1970, k: kind, p: page, r: route, g: target, d: {small detail}}]
"""
import json
import logging
import re
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone

import settings

log = logging.getLogger("studio.activity")

KINDS = {"view", "click", "dead", "rage", "dialog", "error", "api_error", "action"}
MAX_EVENTS = 200          # per request
MAX_BODY = 64 * 1024      # bytes per request
RATE = (120, 300)         # requests per person per 5 minutes (a tab sends one every ~15 s, and one as it closes)
_PAGE = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")
_SESSION = re.compile(r"^[A-Za-z0-9]{6,32}$")
_rate: dict[str, deque] = {}
_seen: dict[tuple, float] = {}   # recent batches (person, tab, batch id): a page resends one as it closes
_lock = threading.Lock()
_last_prune = 0.0


def _clean_text(value, limit: int, numbers: bool = False) -> str:
    """A short single line: no control characters, no long numbers (ids and phone numbers become :n,
    unless numbers=True: an error's file and line)."""
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", str(value or "")).strip()
    if not numbers:
        text = re.sub(r"\d{3,}", ":n", text)
    return text[:limit]


def clean_route(value) -> str:
    """#/production/12 -> #/production/:id; query strings and anything odd dropped."""
    route = str(value or "").split("?", 1)[0]
    route = re.sub(r"/\d+", "/:id", route)
    route = re.sub(r"[^A-Za-z0-9#/:._-]", "", route)
    return route[:80]


def _clean_detail(kind: str, detail) -> str | None:
    if not isinstance(detail, dict) or not detail:
        return None
    out = {}
    for key, value in list(detail.items())[:8]:
        key = re.sub(r"[^a-z_]", "", str(key).lower())[:16]
        if not key:
            continue
        if isinstance(value, bool) or isinstance(value, (int, float)):
            out[key] = value if isinstance(value, (bool, int)) else round(float(value), 2)
        elif isinstance(value, str):
            error = kind == "error" and key in ("msg", "src")
            out[key] = clean_route(value) if key == "path" else _clean_text(value, 200 if error else 60, numbers=key == "src")
    text = json.dumps(out, ensure_ascii=False)
    return text[:600] if out else None


def _limited(user_id: str) -> bool:
    limit, window = RATE
    now = time.time()
    with _lock:
        q = _rate.setdefault(user_id, deque())
        while q and now - q[0] > window:
            q.popleft()
        if len(q) >= limit:
            return True
        q.append(now)
    return False


def iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).isoformat(timespec="seconds")


def _again(user_id: str, session: str, batch) -> bool:
    """This batch arrived already (a request still on its way when the page closed is sent again by beacon)."""
    if not isinstance(batch, str) or not re.match(r"^[A-Za-z0-9]{6,32}$", batch):
        return False
    key = (user_id, session, batch)
    with _lock:
        if key in _seen:
            return True
        if len(_seen) > 5000:
            for k in list(_seen)[:2500]:
                del _seen[k]
        _seen[key] = time.time()
    return False


def record(conn, who: dict, session: str, events: list, qa: bool = False, batch=None) -> int:
    """Store a batch from one tab. who: {id (email), name, role}. qa: a test walk's (localStorage studio.qa = 1),
    kept but left out of the weekly report. Returns how many were kept. Raises ValueError for a batch that
    isn't one (the route answers 400)."""
    if not settings.USAGE:
        return 0
    if not isinstance(events, list):
        raise ValueError("events must be a list")
    session = session if isinstance(session, str) and _SESSION.match(session) else "none"
    if _again(who["id"], session, batch):
        return 0
    now = datetime.now(timezone.utc)
    rows = []
    for e in events[:MAX_EVENTS]:
        if not isinstance(e, dict):
            continue
        kind = str(e.get("k") or "")
        if kind not in KINDS:
            continue
        page = str(e.get("p") or "")
        page = page if _PAGE.match(page) else "other"
        try:
            at = datetime.fromtimestamp(float(e.get("t")) / 1000, timezone.utc)
            if abs((at - now).total_seconds()) > 86400:  # a clock far off: when it arrived
                at = now
        except (TypeError, ValueError, OverflowError, OSError):
            at = now
        rows.append((iso(at), who["id"], (who.get("name") or "")[:60], who.get("role") or "", session, page,
                     clean_route(e.get("r")), kind, _clean_text(e.get("g"), 120) or None, _clean_detail(kind, e.get("d")),
                     1 if qa else None))
    if rows:
        conn.executemany("""INSERT INTO usage_events (at, user_id, user_name, role, session, page, route, kind, target, detail, qa)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", rows)
        conn.commit()
    maybe_prune(conn)
    return len(rows)


def add(conn, who: dict, page: str, kind: str, target: str, detail: dict | None = None, qa: bool = False) -> None:
    """One event recorded by the server itself (say, a question to Ask Studio). Never raises."""
    try:
        record(conn, who, "server", [{"t": time.time() * 1000, "k": kind, "p": page, "g": target, "d": detail or {}}], qa)
    except Exception as e:
        log.warning("Couldn't record a usage event: %s", e)


def prune(conn, days: int | None = None) -> int:
    """Forget events, friction flags and help questions older than settings.USAGE_DAYS days."""
    cutoff = iso(datetime.now(timezone.utc) - timedelta(days=days or settings.USAGE_DAYS))
    gone = 0
    for table in ("usage_events", "friction_flags", "help_questions"):
        gone += conn.execute(f"DELETE FROM {table} WHERE at < ?", (cutoff,)).rowcount
    conn.commit()
    return gone


def maybe_prune(conn) -> None:
    """Once a day, from whichever request comes first (the worker restarts often enough that a timer isn't needed)."""
    global _last_prune
    if time.time() - _last_prune < 86400:
        return
    _last_prune = time.time()
    try:
        gone = prune(conn)
        if gone:
            log.info("Usage: forgot %s rows older than %s days", gone, settings.USAGE_DAYS)
    except Exception as e:
        log.warning("Couldn't prune usage events: %s", e)
