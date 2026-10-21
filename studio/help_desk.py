"""
Help one press away (studio/CLAUDE.md, "The usability standard", 3 and 4): Ask Studio and the friction flag.

- Ask Studio: a how-to question typed in the help panel is answered by one Claude call that knows Studio. The
  knowledge is README.md cut into sections, and only the few that match the question's words (and the page
  it was asked on) go with it, plus the page's own help lines and its name, never what's on screen. Answers
  are short and step by step, and may name a control ("Tap Send back at the bottom"). Each person may ask
  settings.HELP_DAILY questions a (Singapore) day; usage kind "help". Without Claude it says so and the
  panel shows the page's help instead.
- Something not working?: the page, the part tapped (a descriptor, never its words), what they typed, the
  screen size and Studio's version. The owner reads them in More > Feedback and marks them done.
"""
import json
import logging
import math
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import catalog
import settings

log = logging.getLogger("studio.help")

README = Path(__file__).resolve().parent / "README.md"
SGT = timezone(timedelta(hours=8))
MAX_QUESTION = 500
MAX_WORDS = 1000
MAX_KNOWLEDGE = 7000      # characters of README sent with a question
client_factory = None     # tests put a stub here; None = anthropic.Anthropic

# Always sent: how Studio is laid out and who decides what (short; the README has the detail).
BASICS = """Studio has two halves that share one bar: Plan (the planner) and Review.
- The bar: Plan · Ideas · Review · More (on a phone the same four at the bottom). More holds the Posting calendar,
  Results, References, Style guide, Content plan, Client questions, Today on site, the Library and Instagram.
- Plan opens on what needs you: Needs your call, Coming up, this month's post slots and the projects. + New idea is
  the orange button at the top.
- Ideas is one project's posts grouped by what to do next: Ready to make, Film these, Waiting for your call, Parked,
  Made. The project menu (Project ⋯) has the shoot plan, Today on site, the brief and series.
- An idea's page: Your call (Go / Park), the shots to film (Film shot N on a phone, Upload shot N on a computer), the
  Details sheet (kind of post, format, caption), comments, and one main button at the bottom for the next step
  (Storyboard it with Claude, Write slides, Make this, Schedule).
- Review: To review, Being edited, Done, Library. A draft's page: the video, the quick note box under it (typing
  pauses the video; Enter pins the note to that moment), Studio's read, your notes, folded rows (caption, what the
  editor did, transcript, checks), and the bar at the bottom: Reject, Send back with N fixes, Approve.
- Who decides: Bryan (the owner) approves, rejects or sends back drafts, marks ideas Go / Park / Dismiss, presses
  Make this (it costs an edit), schedules posts and writes the final caption. The team plans, comments, films,
  uploads and leaves notes; they see "Bryan decides" where it's his call.
- A grey button that can't be pressed yet says why when tapped."""

# Words that point at the sections a page's questions are usually about.
PAGE_WORDS = {
    "home": "plan needs call new idea month slots projects coming",
    "ideas": "ideas board project waiting call film parked made series organise",
    "idea": "idea storyboard shots film upload go park make details kind caption hook comments",
    "shoot": "shoot plan site day phases shots place",
    "today": "today site checklist shots film tick",
    "calendar": "posting calendar schedule instagram post",
    "results": "results insights views reach instagram",
    "instagram": "instagram connect token app secret",
    "refs": "references import analyse taste",
    "style": "style guide approve taste report",
    "content": "content plan month mix kinds targets offers",
    "questions": "client questions topics suggest",
    "review": "review drafts approve send back notes",
    "draft": "draft notes approve send back reject caption studio read fixes video slideshow",
    "carousel": "carousel slides notes approve send back concept pictures slideshow reel music",
    "producing": "being edited editor revising",
    "done": "done approved rejected archive remove back to review",
    "library": "library clips archive free up space select tidy",
    "clip": "clip quality verdict tags notes moments",
}
_STOP = set("""a an and are as at be but by can do does for from get go got has have how i if in into is it its
me my no not of on or our so than that the their then there these they this to up us was we what when where which
who why will with you your yes would could should shall may might just""".split())


def _words(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z][a-z0-9']+", text.lower()) if len(w) > 2 and w not in _STOP]


_sections: list[tuple[str, str]] = []
_sections_mtime = 0.0


def sections() -> list[tuple[str, str]]:
    """README.md as (heading path, text) pieces of at most ~1500 characters, cached until the file changes."""
    global _sections, _sections_mtime
    try:
        mtime = README.stat().st_mtime
    except OSError:
        return []
    if _sections and mtime == _sections_mtime:
        return _sections
    out: list[tuple[str, str]] = []
    h2 = h3 = ""
    buf: list[str] = []
    in_code = False

    def flush():
        text = "\n".join(buf).strip()
        buf.clear()
        if not text:
            return
        title = " > ".join(x for x in (h2, h3) if x) or "Studio"
        # Long sections in paragraph-sized pieces, so one match doesn't drag in a page of setup steps.
        piece = ""
        for para in re.split(r"\n\s*\n", text):
            if piece and len(piece) + len(para) > 1500:
                out.append((title, piece.strip()))
                piece = ""
            piece += para + "\n\n"
        if piece.strip():
            out.append((title, piece.strip()))

    for line in README.read_text(encoding="utf-8").splitlines():
        if line.startswith("```"):
            in_code = not in_code
            buf.append(line)
            continue
        if not in_code and re.match(r"^#{1,3} ", line):
            flush()
            level = len(line) - len(line.lstrip("#"))
            name = line.lstrip("#").strip()
            if level <= 2:
                h2, h3 = name, ""
            else:
                h3 = name
            continue
        buf.append(line)
    flush()
    _sections, _sections_mtime = out, mtime
    return out


def knowledge(question: str, page: str = "") -> str:
    """The README pieces that best match the question (and, more lightly, the page), up to MAX_KNOWLEDGE."""
    secs = sections()
    if not secs:
        return ""
    q = _words(question)
    p = _words(PAGE_WORDS.get(page, ""))
    docs = [set(_words(title + " " + text)) for title, text in secs]
    n = len(docs)

    def idf(w):
        df = sum(1 for d in docs if w in d)
        return math.log((n + 1) / (df + 0.5))

    weights = {w: idf(w) * 2 for w in set(q)}
    for w in set(p):
        weights[w] = weights.get(w, 0) + idf(w) * 0.5
    scored = []
    for i, (title, text) in enumerate(secs):
        words = _words(title + " " + text)
        if not words:
            continue
        counts: dict[str, int] = {}
        for w in words:
            counts[w] = counts.get(w, 0) + 1
        score = sum(weight * (1 + math.log(counts[w])) for w, weight in weights.items() if w in counts)
        score += sum(1.5 * weights.get(w, 0) for w in set(_words(title)) if w in q)  # a heading that names it
        # Setup steps (PowerShell, DNS, installs) rarely answer how to use a screen.
        if re.search(r"(?i)powershell|winget|nameserver|cloudflare|tailscale|pip install|\.\\venv", text):
            score *= 0.4
        if score > 0:
            scored.append((score / (1 + len(words) / 400), i))
    scored.sort(reverse=True)
    picked, size = [], 0
    for _score, i in scored[:6]:
        title, text = secs[i]
        if size + len(text) > MAX_KNOWLEDGE:
            continue
        picked.append((i, f"## {title}\n{text}"))
        size += len(text)
    return "\n\n".join(t for _i, t in sorted(picked))


SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "string", "description": "The answer, plain text, short numbered steps when there are steps"},
        "covered": {"type": "boolean", "description": "true when Studio's notes answer it; false when you had to guess or they don't cover it"},
    },
    "required": ["answer", "covered"],
    "additionalProperties": False,
}

SYSTEM = """You are Ask Studio, the help inside Studio, Urban Makers' app for planning, editing and posting Instagram Reels
and carousels. Someone using Studio asked how to do something. Answer from Studio's notes below.

How to answer:
- Short and plain, for someone new to Studio, on a phone. At most 5 numbered steps, or 2-3 sentences when there are
  no steps. No preamble, no "Great question".
- Name the control as it reads on screen and where it is: "Tap **Send back** at the bottom of the draft". Bold
  (**like this**) only control names.
- Only what Studio can do as described in the notes. Never invent buttons, settings or features. If the notes don't
  cover it, say so in one sentence, suggest the closest thing that exists, and set covered to false. Also suggest
  "Something not working?" in Help when it sounds like a fault.
- How to use the screens, not how to install or configure the server (unless they ask about setup).
- Bryan (the owner) decides approvals, Go / Park / Dismiss, Make this, scheduling and captions; if the person is on
  the team and asks how to do one of those, say Bryan does it and what they can do instead (a note, a comment).
- Never ask for passwords or keys, and never repeat any.

The person: {who}. They are on: {page_name}.
This page's help:
{page_help}

Studio, in short:
{basics}

Studio's notes (the README sections that match the question):
{notes}"""


def sg_day(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).astimezone(SGT).date().isoformat()


def asked_today(conn, user_id: str) -> int:
    start = datetime.fromisoformat(sg_day()).replace(tzinfo=SGT).astimezone(timezone.utc).isoformat(timespec="seconds")
    return conn.execute("SELECT COUNT(*) FROM help_questions WHERE user_id = ? AND at >= ? AND error IS NULL",
                        (user_id, start)).fetchone()[0]


def _client():
    if client_factory:
        return client_factory()
    import anthropic
    return anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY or None)


def ask(conn, who: dict, question: str, page: str, page_name: str, page_help: list, qa: bool = False) -> dict:
    """{id, answer, answered, left} or {id, unavailable, error, left}; ValueError for an empty question,
    LookupError when today's questions are used up."""
    question = re.sub(r"\s+", " ", str(question or "")).strip()[:MAX_QUESTION]
    if len(question) < 3:
        raise ValueError("Type a question first")
    page = re.sub(r"[^a-z0-9_-]", "", str(page or "").lower())[:40] or "other"
    page_name = re.sub(r"[\x00-\x1f]", " ", str(page_name or ""))[:60]
    lines = [re.sub(r"[\x00-\x1f]", " ", str(x))[:300] for x in (page_help if isinstance(page_help, list) else [])][:6]
    used = asked_today(conn, who["id"])
    if used >= settings.HELP_DAILY:
        raise LookupError(f"That's today's {settings.HELP_DAILY} questions. Ask again tomorrow, or use Show me and the page help.")
    cur = conn.execute("""INSERT INTO help_questions (at, user_id, user_name, page, question, qa) VALUES (?, ?, ?, ?, ?, ?)""",
                       (catalog.now(), who["id"], who.get("name") or "", page, question, 1 if qa else None))
    conn.commit()
    qid = cur.lastrowid
    left = max(settings.HELP_DAILY - used - 1, 0)
    if not settings.ANTHROPIC_API_KEY and not client_factory:
        return _unavailable(conn, qid, "no Claude key on this Studio machine", left + 1)
    system = SYSTEM.format(who=f"{who.get('name') or 'someone'} ({'the owner' if who.get('role') == 'owner' else 'on the team'})",
                           page_name=page_name or page, page_help="\n".join(f"- {x}" for x in lines) or "(none)",
                           basics=BASICS, notes=knowledge(question, page) or "(nothing matched)")
    try:
        import llm
        import usage
        response = llm.final_message(
            _client(), model=settings.HELP_MODEL, max_tokens=4000,
            betas=["server-side-fallback-2026-07-01"], fallbacks="default",
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
            system=system, messages=[{"role": "user", "content": question}])
        usage.record("help", f"help:{qid}", response)
        if response.stop_reason in ("refusal", "max_tokens"):
            raise RuntimeError(f"no answer ({response.stop_reason})")
        data = json.loads(next(b.text for b in response.content if b.type == "text"))
        answer = str(data.get("answer") or "").strip()[:2000]
        if not answer:
            raise RuntimeError("an empty answer")
        covered = bool(data.get("covered"))
    except Exception as e:  # anything at all: the panel falls back to the page's help
        log.warning("Ask Studio couldn't answer (%s): %s", page, e)
        return _unavailable(conn, qid, str(e)[:200], left + 1)
    conn.execute("UPDATE help_questions SET answer = ?, answered = ? WHERE id = ?", (answer, int(covered), qid))
    conn.commit()
    return {"id": qid, "answer": answer, "answered": covered, "left": left}


def _unavailable(conn, qid: int, error: str, left: int) -> dict:
    conn.execute("UPDATE help_questions SET answered = 0, error = ? WHERE id = ?", (error, qid))
    conn.commit()
    return {"id": qid, "unavailable": True, "error": "Ask Studio can't answer right now (Claude isn't reachable).", "left": left}


def helpful(conn, who: dict, qid: int, value: bool) -> bool:
    cur = conn.execute("UPDATE help_questions SET helpful = ? WHERE id = ? AND user_id = ?", (int(bool(value)), qid, who["id"]))
    conn.commit()
    return cur.rowcount > 0


# ── Something not working? ───────────────────────────────────────────────────

def flag(conn, who: dict, data: dict) -> int:
    import activity

    def short(key, limit):
        return re.sub(r"[\x00-\x1f\x7f]+", " ", str(data.get(key) or "")).strip()[:limit]

    page = re.sub(r"[^a-z0-9_-]", "", short("page", 40).lower()) or "other"
    cur = conn.execute("""INSERT INTO friction_flags (at, user_id, user_name, page, route, control, words, viewport, version, qa)
                          VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                       (catalog.now(), who["id"], who.get("name") or "", page, activity.clean_route(data.get("route")),
                        short("control", 160), short("words", MAX_WORDS),
                        re.sub(r"[^0-9x]", "", short("viewport", 20)), short("version", 40), 1 if data.get("qa") else None))
    conn.commit()
    return cur.lastrowid


def flags(conn, limit: int = 200) -> list[dict]:
    rows = conn.execute("SELECT * FROM friction_flags ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [{k: r[k] for k in ("id", "at", "user_name", "page", "route", "control", "words", "viewport", "version", "done_at", "done_by", "qa")}
            for r in rows]


def mark_flag(conn, flag_id: int, done: bool, by: str) -> bool:
    cur = conn.execute("UPDATE friction_flags SET done_at = ?, done_by = ? WHERE id = ?",
                       (catalog.now() if done else None, by if done else None, flag_id))
    conn.commit()
    return cur.rowcount > 0
