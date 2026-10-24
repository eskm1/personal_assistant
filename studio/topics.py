"""
Suggested topics (cloud/CONTRACT.md, "Suggested topics"). Once a week (STUDIO_TOPICS_DAY at
STUDIO_TOPICS_HOUR, Singapore time), when the month falls behind (a kind short past mid-month
with no suggestion covering it; at most once a day), or when someone presses "Suggest topics"
in the planner (job kind "topics"), Claude suggests a few posts (STUDIO_TOPICS_COUNT) for the
month's gaps, in one call. Each is a proposed idea Bryan Go's, Parks or Dismisses:
  - a carousel (most Know-how, Ideas and Work with us topics) with its slide outline, in the
    standing Topics project, arc "Studio suggests";
  - a Reel where Bryan already explained it on site, in that footage's project (arc "From your
    footage", as footage_ideas.py does), its shots pointing at the clip.
Each carries a short "why now" (ideas.suggestion) the planner shows on the card.

Where topics come from, best first: what Bryan said on site (the library's transcripts), the
team's list of questions clients ask (client_questions; the question is linked to the post), Urban
Makers' own wiki (wiki.py: the trade articles in the quote engine, sanitised, the least used first;
mostly carousels when there's no footage of it, each citing its article), the saved references and
the style guide (format and tone), the shoot calendar (a Know-how post timed for when that work is
filmed) and Singapore renovation basics, stated carefully. Never a price or
figure Bryan didn't write, never an offer that isn't in the content plan, never a place name in
public text, nothing about quotes. Never a repeat of a post already planned, made or dismissed
(dismissed suggestions are remembered here too, in idea_runs, so they don't come back reworded).

Runs only with online planning (it needs the month's counts). Every run is recorded in the
catalog's idea_runs (trigger "topics:weekly" | "topics:behind" | "topics:job"), like the ideas
from footage, and costs one Claude call (usage kind "topics").
"""
import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import anthropic

import catalog
import content_plan
import footage_ideas
import playbook
import settings
import wiki
import wording

log = logging.getLogger("studio.topics")

SGT = timezone(timedelta(hours=8))   # Singapore: no summer time
DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
TOPICS = "Topics"
TOPICS_BRIEF = ("Urban Makers posts that aren't about one client's renovation: practical know-how (HDB rules, "
                "materials that last in Singapore, what things really involve), design ideas (layouts, zoning, "
                "light, colour), and how to work with us.")
ARC_TITLE = "Studio suggests"
ARC_PURPOSE = "Posts Studio suggested for the month's gaps (topics.py). Go, Park or Dismiss them."
TRIGGERS = ("weekly", "behind", "job")
FILLABLE = ("knowhow", "ideas", "offer")            # kinds a topic fills without new footage
KINDS_OK = ("knowhow", "ideas", "onsite", "offer")  # never Finished homes: that needs handover footage
SOURCES = ("transcript", "question", "wiki", "calendar", "reference", "basics", "plan")
TRANSCRIPT_DAYS = 120       # how far back Bryan's site talk is read
TRANSCRIPT_BUDGET = 60_000  # characters of transcripts per call (~15k tokens)
TRANSCRIPT_CHARS = 3000     # per clip
MIN_SPEECH = 160            # a clip with less speech than this has nothing to teach
QUESTIONS_MAX = 40
TAKEN_MAX = 250             # posts already planned or made, listed so they aren't repeated
DISMISSED_MAX = 150
CALENDAR_DAYS = 28
SIMILAR = 0.75              # share of the shorter title's words above which it counts as a repeat
EARLY_HOURS = 12            # a run this long before the weekly slot counts as that week's (say, right after an update)
CHECK_SECONDS = 600         # how often the "month falls behind" check may ask the database
TAKE_NOTE = "Bryan explains it here"
PURPOSE_MAX = 1500          # an idea's purpose with its wiki facts (the planner's Write slides reads up to 2000)

_checked_at = 0.0           # when run_due last asked for the month (the behind check)

# What Claude can see as its "why now" source, for the planner's card.
_EXPLAINS = re.compile(
    r"\b(because|the reason|that's why|so that|otherwise|make sure|you need to|you have to|you should|"
    r"don't|never|always|how to|what we do|we always|the trick|important|the problem|if you)\b|因为|所以|一定要|不要",
    re.IGNORECASE)
_NUM = r"\d+(?:[.,]\d+)*"
_MONEY = re.compile(rf"(?:S\$|SGD\s?|US\$|\$){_NUM}(?:\s?(?:k|mil|million)\b)?|\b{_NUM}\s?k?\s?(?:dollars|bucks|sgd)\b"
                    r"|\b\d+\s?%\s?off\b", re.IGNORECASE)
_WORD = re.compile(r"[a-z]+")
_STOP = set("a an the and or of to in on for with your you we our is are be it this that how why what when "
            "do does don't before after from at by into can should my me i".split())


def _sgt(now: datetime | None = None) -> datetime:
    return (now or datetime.now(timezone.utc)).astimezone(SGT)


# ── When ──────────────────────────────────────────────────────────────────────

def week_slot(now: datetime | None = None) -> datetime | None:
    """This week's run time (the last STUDIO_TOPICS_DAY at STUDIO_TOPICS_HOUR, Singapore time,
    at or before now), or None when the weekly run is off."""
    if settings.TOPICS_DAY not in DAYS:
        return None
    local = _sgt(now)
    back = (local.weekday() - DAYS.index(settings.TOPICS_DAY)) % 7
    slot = (local - timedelta(days=back)).replace(hour=settings.TOPICS_HOUR, minute=0, second=0, microsecond=0)
    return slot if slot <= local else slot - timedelta(days=7)


def _runs(conn, since: datetime, states=("done", "failed", "running"), triggers=TRIGGERS) -> list:
    marks = ",".join("?" * len(triggers))
    smarks = ",".join("?" * len(states))
    return conn.execute(
        f"""SELECT * FROM idea_runs WHERE trigger IN ({marks}) AND state IN ({smarks}) AND started_at >= ?
            ORDER BY id DESC""",
        [*(f"topics:{t}" for t in triggers), *states,
         since.astimezone(timezone.utc).isoformat(timespec="seconds")]).fetchall()


def is_open(i: dict) -> bool:
    """A suggestion still waiting for Bryan's call."""
    return bool(i.get("suggestion")) and i.get("status") == "new" and (i.get("decision") or "proposed") == "proposed"


def gaps(month: dict, ideas: list[dict]) -> dict:
    """{kind: {short, open, uncovered}} for the kinds a topic can fill: how many posts the month
    still needs, how many open suggestions already aim at it, and what's left."""
    out = {}
    kinds = (month or {}).get("kinds") or {}
    open_ = [i for i in ideas if is_open(i)]
    for k in FILLABLE:
        short = int((kinds.get(k) or {}).get("short") or 0)
        n = sum(1 for i in open_ if i.get("post_type") == k)
        out[k] = {"short": short, "open": n, "uncovered": max(short - n, 0)}
    return out


def due(conn, month: dict | None, ideas: list[dict], now: datetime | None = None) -> str | None:
    """Which automatic run is due now ("weekly" | "behind"), or None. At most one automatic run a
    (Singapore) day, none while the open suggestions already fill the count."""
    if settings.TOPICS_COUNT <= 0:
        return None
    local = _sgt(now)
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    if _runs(conn, midnight, triggers=("weekly", "behind")):
        return None  # once a day
    if sum(1 for i in ideas if is_open(i)) >= settings.TOPICS_COUNT:
        return None  # don't flood: Bryan hasn't decided on the last lot yet
    slot = week_slot(now)
    if slot and not _runs(conn, slot - timedelta(hours=EARLY_HOURS), states=("done",)):
        return "weekly"
    if settings.TOPICS_BEHIND and month and local.day > 14:
        if any(g["uncovered"] and not g["open"] for g in gaps(month, ideas).values()):
            return "behind"
    return None


def busy(conn) -> bool:
    """Heavier work first: footage arriving or being tagged, edits queued, or new clips the
    ideas from footage haven't read yet (they make the transcripts fresher)."""
    if footage_ideas._inbox_busy():
        return True
    recent = (datetime.now(timezone.utc) - timedelta(hours=6)).isoformat(timespec="seconds")
    if conn.execute("SELECT 1 FROM clips WHERE status = 'processing' AND ingested_at > ? LIMIT 1", (recent,)).fetchone():
        return True
    if conn.execute("SELECT 1 FROM productions WHERE status IN ('planned', 'revising', 'producing') LIMIT 1").fetchone():
        return True
    return settings.IDEAS_AFTER_MINUTES > 0 and bool(footage_ideas.pending(conn, cloud=True))


# ── What Claude reads ─────────────────────────────────────────────────────────

def _day(stamp) -> str:
    t = footage_ideas._parse(stamp)
    return t.astimezone(SGT).strftime("%a %d %b") if t else "unknown day"


def _explains(text: str) -> int:
    return len(_EXPLAINS.findall(text or ""))


def _segments(r) -> str:
    import ideas
    try:
        text = ideas._segments_text(Path(r["clip_dir"]), r["transcript"])
    except (OSError, ValueError, KeyError, TypeError):
        text = f"    {r['transcript']}"
    return text if len(text) <= TRANSCRIPT_CHARS else text[:TRANSCRIPT_CHARS].rsplit("\n", 1)[0] + "\n    [...]"


def clip_uses(api) -> dict[int, list[tuple]]:
    """{clip id: [(start, end, post title)]}: where the library's footage is already used in a
    planned post (not dismissed), from the planner's takes."""
    out: dict[int, list[tuple]] = {}
    for t in api.select("takes", {"select": "id,clip_id,start,end,shots(ideas(title,status))"}):
        idea = ((t.get("shots") or {}).get("ideas") or {})
        if idea.get("status") != "dismissed":
            out.setdefault(t["clip_id"], []).append((t.get("start") or 0, t.get("end") or 0, idea.get("title") or ""))
    return out


def transcripts(conn, now: datetime | None = None) -> list:
    """Clips where Bryan talks (tagged, usable, pushed to the planner), the most explanatory and
    newest first, as many as fit the budget."""
    since = ((now or datetime.now(timezone.utc)) - timedelta(days=TRANSCRIPT_DAYS)).isoformat(timespec="seconds")
    rows = conn.execute(
        """SELECT c.*, p.name AS project_name, p.remote_id AS project_ref, p.status AS project_status
           FROM clips c LEFT JOIN projects p ON p.id = c.project_id
           WHERE c.status = 'tagged' AND COALESCE(c.usable, 1) = 1 AND c.archived_at IS NULL AND c.cloud_hash IS NOT NULL
           AND LENGTH(COALESCE(c.transcript, '')) >= ? AND COALESCE(c.recorded_at, c.ingested_at) >= ?""",
        (MIN_SPEECH, since)).fetchall()
    def score(r):
        talking = 3 if r["format"] == "talking_head" else 0
        moments = sum(1 for m in json.loads(r["moments"] or "[]") if m.get("kind") in ("key_point", "quote"))
        return (_explains(r["transcript"]) + talking + moments, r["recorded_at"] or r["ingested_at"] or "")
    rows.sort(key=score, reverse=True)
    out, used = [], 0
    for r in rows:
        size = len(r["transcript"] or "") + 400
        if out and used + size > TRANSCRIPT_BUDGET:
            continue
        out.append(r)
        used += size
    return out


def _transcripts_text(rows: list, uses: dict) -> str:
    if not rows:
        return "(none: no footage of Bryan talking in the last few months)"
    out = []
    for r in rows:
        moments = json.loads(r["moments"] or "[]")
        used = uses.get(r["id"]) or []
        out.append("\n".join([
            f"Clip {r['id']}: {r['title'] or r['source_name']}",
            f"  project (internal): {r['project_name'] or 'none'} | recorded {_day(r['recorded_at'] or r['ingested_at'])} | "
            f"{r['duration'] or 0:.0f}s | {r['format'] or '?'}",
            f"  summary: {r['summary'] or '-'}",
            "  moments: " + ("; ".join(f"{m['start']:.1f}-{m['end']:.1f}s {m.get('kind', '')}: {m.get('description', '')}"
                                       for m in moments) or "-"),
            *([("  already used in: " + "; ".join(f"\"{t}\" ({a:.0f}-{b:.0f}s)" for a, b, t in used[:4]))] if used else []),
            "  transcript:", _segments(r)]))
    return "\n\n".join(out)


def _names(api) -> dict:
    try:
        return {p["id"]: p.get("name") or "" for p in api.select("profiles", {"select": "id,name"})}
    except Exception:
        return {}


def _questions(api) -> list[dict]:
    return api.select("client_questions", {"status": "eq.open", "select": "id,question,note,asked_by,created_by,created_at",
                                           "order": "id.desc"})[:QUESTIONS_MAX]


def _questions_text(questions: list[dict], names: dict) -> str:
    if not questions:
        return "(none open)"
    out = []
    for q in questions:
        who = names.get(q.get("created_by")) or "the team"
        out.append(f"Q{q['id']} (added by {who}, {_day(q.get('created_at'))}"
                   + (f"; asked by: {q['asked_by'].strip()[:120]}" if (q.get("asked_by") or "").strip() else "") + "): "
                   f"\"{q['question'].strip()[:500]}\"" + (f"\n  note: {q['note'].strip()[:600]}" if (q.get("note") or "").strip() else ""))
    return "\n".join(out)


def _references_text(api) -> str:
    try:
        refs = api.select("reference_videos", {"status": "eq.active", "brand": "eq.urban_makers",
                                               "select": "id,side,title,why", "order": "id.desc"})[:30]
    except Exception:
        return "(none)"
    lines = [f"- {'our own' if r['side'] == 'ours' else 'inspiration'}: {(r.get('title') or 'untitled')[:120]}"
             + (f" (why it works: {r['why'].strip()[:300]})" if (r.get("why") or "").strip() else "") for r in refs]
    return "\n".join(lines) or "(none)"


def _calendar(api, now: datetime | None = None) -> list[dict]:
    """Site days and phases starting in the next CALENDAR_DAYS days, active projects only."""
    today = _sgt(now).date()
    end = today + timedelta(days=CALENDAR_DAYS)
    try:
        days = api.select("site_days", {"day": f"gte.{today}", "and": f"(day.lte.{end})",
                                        "select": "day,note,project_id,projects(name,status),phases(name)", "order": "day"})
        phases = api.select("phases", {"starts_on": f"gte.{today}", "and": f"(starts_on.lte.{end})",
                                       "select": "name,starts_on,ends_on,project_id,projects(name,status)", "order": "starts_on"})
    except Exception as e:
        log.debug("No shoot calendar (%s)", e)
        return []
    out = [{"when": d["day"], "project": (d.get("projects") or {}).get("name") or "", "what": "site day",
            "phase": (d.get("phases") or {}).get("name") or "", "note": d.get("note") or ""}
           for d in days if (d.get("projects") or {}).get("status", "active") == "active"]
    out += [{"when": p["starts_on"], "project": (p.get("projects") or {}).get("name") or "", "what": "phase starts",
             "phase": p["name"], "note": ""} for p in phases if (p.get("projects") or {}).get("status", "active") == "active"]
    return sorted(out, key=lambda x: x["when"])[:40]


def _calendar_text(cal: list[dict]) -> str:
    if not cal:
        return "(nothing planned in the next four weeks)"
    def day(d):
        try:
            return datetime.strptime(d, "%Y-%m-%d").strftime("%a %d %b")
        except ValueError:
            return d
    return "\n".join(f"- {day(c['when'])}: {c['project']} (internal), {c['what']}" + (f": {c['phase']}" if c["phase"] else "")
                     + (f" ({c['note'][:150]})" if c["note"] else "") for c in cal)


def _norm(text: str) -> set[str]:
    """The words that carry the point (no numbers, no small words; "kitchens" = "kitchen")."""
    words = (w for w in _WORD.findall((text or "").lower()) if w not in _STOP and len(w) > 2)
    return {w[:-1] if len(w) > 4 and w.endswith("s") and not w.endswith("ss") else w for w in words}


def similar(a: str, b: str) -> bool:
    """Roughly the same point (word overlap), so a reworded repeat is caught too: at least
    three words in common, and most of the shorter one's."""
    x, y = _norm(a), _norm(b)
    shared = len(x & y)
    if shared < 3:
        return False
    return shared / min(len(x), len(y)) >= SIMILAR or shared / len(x | y) >= 0.6


def remembered(conn) -> list[dict]:
    """Every suggestion Studio made before: {id, title, hook} from the topic runs (so one that was
    dismissed and then deleted still counts as dismissed)."""
    out = []
    for r in conn.execute("SELECT result FROM idea_runs WHERE trigger LIKE 'topics:%' AND state = 'done' ORDER BY id DESC"):
        for s in json.loads(r["result"] or "{}").get("suggestions", []):
            out.append(s)
    return out


def taken(ideas: list[dict], memory: list[dict]) -> tuple[list[dict], list[dict]]:
    """(planned or made, dismissed): what a suggestion must never repeat."""
    live = {i["id"]: i for i in ideas}
    planned = [i for i in ideas if i.get("status") != "dismissed" and not is_open(i)]
    dismissed = [i for i in ideas if i.get("status") == "dismissed"]
    have = {i["id"] for i in dismissed}
    for s in memory:  # suggested before, then dismissed (or deleted): never again
        i = live.get(s.get("id"))
        if (i is None or i.get("status") == "dismissed") and s.get("id") not in have:
            dismissed.append({"id": s.get("id"), "title": s.get("title") or "", "hook": s.get("hook") or ""})
            have.add(s.get("id"))
    planned.sort(key=lambda i: -(i.get("id") or 0))
    return planned[:TAKEN_MAX], dismissed[:DISMISSED_MAX]


def _list_text(rows: list[dict], with_kind: bool = True) -> str:
    if not rows:
        return "(none)"
    out = []
    for i in rows:
        kind = content_plan.KINDS.get(i.get("post_type"), {}).get("label") if with_kind else None
        out.append(f"- \"{(i.get('title') or '').strip()[:160]}\"" + (f" ({kind})" if kind else "")
                   + (f": {(i.get('hook') or '').strip()[:140]}" if (i.get("hook") or "").strip() else ""))
    return "\n".join(out)


def _gaps_text(g: dict, month: dict, count: int) -> str:
    lines = []
    for k, v in g.items():
        label = content_plan.KINDS[k]["label"]
        if v["uncovered"]:
            lines.append(f"- {label} ({k}): {v['short']} short"
                         + (f", {v['open']} open suggestion{'s' if v['open'] != 1 else ''} already aim at it" if v["open"] else "")
                         + f": suggest up to {v['uncovered']}.")
        elif v["short"]:
            lines.append(f"- {label} ({k}): {v['short']} short, covered by the open suggestions: skip it.")
    if not lines:
        lines.append("- No gaps left in the kinds topics can fill. Suggest strong evergreen Know-how or Ideas posts "
                     "the team can bank for next month (fewer is fine).")
    return f"Suggest at most {count}. Gaps this month:\n" + "\n".join(lines)


def _schema() -> dict:
    shot = {"type": "object", "properties": {
        "clip_id": {"type": "integer"}, "start": {"type": "number"}, "end": {"type": "number"},
        "kind": {"type": "string", "enum": ["talking", "broll", "detail", "before_after", "reaction", "screen"]},
        "description": {"type": "string"}, "line": {"type": "string"}, "on_screen": {"type": "string"},
        "seconds": {"type": "number"}},
        "required": ["clip_id", "start", "end", "kind", "description", "line", "on_screen", "seconds"],
        "additionalProperties": False}
    slide = {"type": "object", "properties": {
        "role": {"type": "string", "enum": ["cover", "point", "ask"]}, "heading": {"type": "string"},
        "body": {"type": "string"}, "picture": {"type": "string"}},
        "required": ["role", "heading", "body", "picture"], "additionalProperties": False}
    formats = (["carousel"] if _carousels() else []) + ["talking_head"]
    return {"type": "object", "properties": {
        "suggestions": {"type": "array", "items": {"type": "object", "properties": {
            "title": {"type": "string"}, "hook": {"type": "string"},
            "hook_options": {"type": "array", "items": {
                "type": "object", "properties": {"text": {"type": "string"}, "formula": {"type": "string"}},
                "required": ["text", "formula"], "additionalProperties": False}},
            "post_type": {"type": "string", "enum": list(KINDS_OK)}, "format": {"type": "string", "enum": formats},
            "why_now": {"type": "string"}, "source": {"type": "string", "enum": list(SOURCES)},
            "clip_id": {"type": "integer"}, "start": {"type": "number"}, "end": {"type": "number"},
            "question_id": {"type": "integer"}, "wiki_slug": {"type": "string"},
            "purpose": {"type": "string"}, "cta": {"type": "string"}, "caption": {"type": "string"},
            "est_seconds": {"type": "number"},
            "slides": {"type": "array", "items": slide}, "shots": {"type": "array", "items": shot}},
            "required": ["title", "hook", "hook_options", "post_type", "format", "why_now", "source", "clip_id", "start", "end",
                         "question_id", "wiki_slug", "purpose", "cta", "caption", "est_seconds", "slides", "shots"],
            "additionalProperties": False}},
        "notes": {"type": "string"}},
        "required": ["suggestions", "notes"], "additionalProperties": False}


_SYSTEM = """You suggest Instagram posts for Urban Makers, a Singapore renovation company run by \
Bryan, to fill the gaps in this month's content plan. Every suggestion is a proposal: Bryan \
decides Go, Park or Dismiss, and Studio never posts anything.

{brands}

Bryan's playbook (his story rules, and the house style from his reference reels):

<playbook>
{playbook}
</playbook>

Where topics come from, best first:
1. Things Bryan said on site (the transcripts in the message). When he explains why or how \
something is done, that's a ready Know-how post in his own words. Cite it: clip_id, and start and \
end seconds from the transcript timestamps, on whole phrases.
2. Questions clients ask (the team's list). What people really ask: one question per post; give \
its question_id.
3. Urban Makers' wiki: the team's own trade knowledge (sizes, sequences, checks, mounting \
heights, rules of thumb), the least used articles first. Especially when there's little footage \
of Bryan explaining something: a carousel built on one article's facts (or a Reel whose shots say \
what to film). Use only the facts listed under that article, in plain words for a homeowner; give \
its wiki_slug (the text after "wiki:" in its tag). Never one already in the planner again: a \
second post from an article must make a different point.
4. The saved references and the style guide: for format and tone, not topics to copy.
5. The shoot calendar: a Know-how topic timed for when that work is filmed (a post on \
waterproofing the week the team films waterproofing).
6. Singapore renovation basics (HDB renovation rules and permits, the order of works, what to check \
before hacking or tiling) from your own knowledge, stated carefully: general, true for most flats, \
"check with HDB or your contractor" where rules vary or change.

What to return (suggestions: at most {count}):
- Aim each suggestion at one of the gaps the message lists (post_type). Skip a gap the open \
suggestions already cover. Never post_type finished: that needs real handover footage.
- format: {formats}
- title: short and specific (internal). hook: the cover line or the first 3 seconds. purpose: who \
it's for and what they should do after. cta: the kind's ask; an offer only from the listed offers, \
worded as listed. caption: ready to edit, in Bryan's voice, 3 to 5 hashtags (the caption rule below). \
est_seconds: a Reel's length (30 for a carousel).
- slides (carousels; empty for a Reel): the outline, 6 to 9 slides: one cover first (the hook), \
points (a heading and one or two plain sentences each), one ask last. picture: what picture would \
suit the slide, in a few words (a still from the cited clip, a project photo, or a concept of the idea).
- shots (Reels; empty for a carousel): the storyboard in playback order; a shot from the cited \
footage gives its clip_id with start and end seconds; a shot still to film has clip_id 0, start 0 \
and end 0, and says exactly what to film.
- clip_id, start, end: the moment Bryan explains it (0 when the topic doesn't come from footage). \
question_id: the client question it answers (0 when none). wiki_slug: the wiki article it's built \
on ("" when none).
- why_now: one short line for Bryan (under 100 characters): why this, why now. Concrete, e.g. \
"Fills October's Ideas gap", "You explained this on site last Tuesday", "Michelle logged this client \
question", "You film waterproofing on Thursday". It is internal: a project's name may appear there, \
never in a hook, caption, cta or slide.
- source: transcript, question, wiki, calendar, reference, basics or plan (the gap alone).
- Never repeat a post already planned, made or dismissed (listed in the message), even reworded or \
as another angle on the same point. Fewer, stronger suggestions beat filling the count.
- Never state a price, cost, fee, discount, penalty, duration or any other figure unless Bryan says \
it in a transcript, the team wrote it in a question's note, or the cited wiki article lists it: write \
[check] where a figure would go. Never name a supplier, brand or shop. \
No promotions or offers beyond the listed offers. Nothing about quotes, quotations or pricing.
- Never name a place (estate, street, condo, block, town, a client's address) or a client in a hook, \
caption, cta, slide or shot line.
- notes: one or two sentences for the team (anything you left out and why)."""

_FORMATS = ("carousel for most Know-how, Ideas and Work with us topics (no shoot needed); talking_head (a Reel) "
            "only where Bryan already has footage of himself explaining it, citing that clip. An On site "
            "suggestion must be a Reel from footage.")
_FORMATS_NO_CAROUSEL = ("talking_head: a Reel. Where Bryan already has footage of himself explaining it, cite "
                        "that clip; otherwise its shots say what to film. An On site suggestion must come from footage.")


def _carousels() -> bool:
    import cloud_sync
    return cloud_sync._carousels_online


def system_text() -> str:
    import wording
    return (_SYSTEM.replace("{brands}", settings.brands_text()).replace("{playbook}", playbook.text())
            .replace("{count}", str(max(settings.TOPICS_COUNT, 1)))
            .replace("{formats}", _FORMATS if _carousels() else _FORMATS_NO_CAROUSEL)
            + "\n\n" + wording.guide(caption=True, hooks=True))


def prompt(ctx: dict) -> str:
    """The message: today, the plan and its gaps, the sources, and what's taken."""
    local = _sgt(ctx.get("now"))
    month = ctx.get("month") or {}
    try:
        ends = datetime.strptime(month.get("ends_on") or "", "%Y-%m-%d").date()
        left = f" {(ends - local.date()).days} days left in {local.strftime('%B')}."
    except ValueError:
        left = ""
    open_ = [i for i in ctx["ideas"] if is_open(i)]
    return (f"Today: {local.strftime('%A %d %B %Y')} (Singapore).{left}\n\n"
            f"The content plan:\n<content_plan>\n{ctx['plan_text']}\n</content_plan>\n\n"
            f"{_gaps_text(ctx['gaps'], month, ctx['count'])}\n\n"
            f"Open suggestions (waiting for Bryan; they cover gaps already):\n{_list_text(open_)}\n\n"
            f"Things Bryan said on site (transcripts, the most explanatory first):\n\n{ctx['transcripts_text']}\n\n"
            f"Questions clients ask (the team's list, open):\n{ctx['questions_text']}\n\n"
            f"Urban Makers' wiki (the team's trade knowledge, the least used first; cite wiki_slug):\n"
            f"{ctx.get('wiki_text') or '(none)'}\n\n"
            f"Saved references (format and tone):\n{ctx['references_text']}\n\n"
            f"Shoot calendar (next four weeks):\n{ctx['calendar_text']}\n\n"
            f"Posts already planned or made (never repeat these):\n{_list_text(ctx['planned'])}\n\n"
            f"Dismissed before (never suggest these again, even reworded):\n{_list_text(ctx['dismissed'], False)}")


def _ask(user: str, ref: str, client=None) -> dict:
    """One structured Claude call (effort medium); cost is recorded as "topics"."""
    import llm
    import usage
    client = client or anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY or None)
    response = llm.final_message(
        client, model=settings.CLAUDE_MODEL, max_tokens=24000,
        betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": _schema()}},
        system=system_text(), messages=[{"role": "user", "content": user}])
    usage.record("topics", ref, response)
    if response.stop_reason == "refusal":
        raise RuntimeError("Claude declined to suggest topics this time")
    if response.stop_reason == "max_tokens":
        raise RuntimeError("Claude's answer was cut off (max_tokens)")
    return json.loads(next(b.text for b in response.content if b.type == "text"))


# ── Cleaning Claude's answer ──────────────────────────────────────────────────

def no_figures(text: str, allowed: str) -> str:
    """Belt and braces for "no prices unless Bryan wrote them": a money figure (or a "% off")
    that isn't in Bryan's own words or the team's notes becomes [check]."""
    def swap(m):
        return m.group(0) if m.group(0).strip().lower() in allowed else "[check]"
    return _MONEY.sub(swap, text or "")


def _public(text: str, names: list[str], allowed: str, limit: int) -> str:
    return no_figures(footage_ideas._private((text or "").strip(), names), allowed)[:limit]


def _slides(raw: list[dict], names, allowed, clip: dict | None) -> list[dict]:
    """The outline as valid slides: one cover first, the ask last, at most 12; text slides with
    a note on the picture (the cited clip's still on the cover)."""
    import uuid
    rows = [s for s in raw if (s.get("heading") or "").strip()]
    covers = [s for s in rows if s.get("role") == "cover"]
    asks = [s for s in rows if s.get("role") == "ask"]
    points = [s for s in rows if s.get("role") not in ("cover", "ask")]
    ordered = ([covers[0]] if covers else []) + points[:10] + ([asks[-1]] if asks else [])
    out = []
    for n, s in enumerate(ordered):
        role = s.get("role") if s.get("role") in ("cover", "ask") else "point"
        image = {"source": "text", "note": (s.get("picture") or "").strip()[:300]}
        if n == 0 and clip and role == "cover":
            image = {"source": "clip", "clip_id": clip["id"], "at": clip["at"], "note": image["note"],
                     "thumb_path": f"clips/{clip['id']}.jpg"}
        out.append({"key": uuid.uuid4().hex[:10], "role": role, "heading": _public(s.get("heading"), names, allowed, 200),
                    "body": _public(s.get("body"), names, allowed, 800), "image": image})
    return out


def with_facts(purpose: str, article: dict) -> str:
    """The purpose with the wiki article's key facts after it: the planner's Write slides reads the purpose,
    so the carousel it writes has the facts (sanitised, at most wiki.FACT_WORDS words)."""
    block = (f"From the wiki: {article['title']}. Key facts from Urban Makers' own wiki (the team's trade "
             f"knowledge; these figures may be used): {article['brief']}")
    room = PURPOSE_MAX - len(block) - 2
    purpose = purpose[:max(room, 0)].rstrip()
    return (f"{purpose}\n\n{block}" if purpose else block)[:PURPOSE_MAX]


def clean(answer: dict, ctx: dict) -> tuple[list[dict], list[dict]]:
    """(suggestions to write, skipped [{title, why}]). Repeats, made-up clips and questions,
    kinds and formats that don't fit are dropped or fixed; public text loses place names and
    figures Bryan didn't write."""
    clips = {r["id"]: r for r in ctx["clips"]}
    questions = {q["id"]: q for q in ctx["questions"]}
    names, allowed = ctx["names"], ctx["allowed"]
    taken_rows = [*ctx["planned"], *[i for i in ctx["ideas"] if is_open(i)]]
    keep, skipped = [], []
    for s in answer.get("suggestions", []):
        title = (s.get("title") or "").strip()[:300]
        hook = written = (s.get("hook") or "").strip()
        # Three hooks from the formulas, scored (wording.pick_hook): the strongest is the hook.
        hook, hook_options = wording.pick_hook(s.get("hook_options"), hook)
        def skip(why):
            skipped.append({"title": title or "(untitled)", "why": why})
        if not title:
            skip("no title")
            continue
        if len(keep) >= ctx["count"]:
            skip("over the count")
            continue
        kind = s.get("post_type") if s.get("post_type") in KINDS_OK else None
        if kind is None:
            skip("not a kind topics can fill")
            continue
        dup = next((i for i in ctx["dismissed"] if similar(title, i.get("title")) or (hook and similar(hook, i.get("hook")))), None)
        if dup:
            skip(f"like a dismissed suggestion (\"{dup.get('title')}\")")
            continue
        dup = next((i for i in [*taken_rows, *keep] if similar(title, i.get("title")) or (hook and similar(hook, i.get("hook")))), None)
        if dup:
            skip(f"already planned (\"{dup.get('title')}\")")
            continue
        clip = clips.get(s.get("clip_id"))
        at = None
        if clip:
            duration = clip["duration"] or 0
            start, end = footage_ideas._span(s.get("start"), s.get("end"), duration)
            at = start
            if any(a < end and start < b and min(end, b) - max(start, a) > 0.5 * (end - start)
                   for a, b, _ in ctx["uses"].get(clip["id"], [])):
                skip("that moment is already in a planned post")
                continue
        fmt = s.get("format")
        if fmt == "carousel" and not _carousels():
            fmt = "talking_head"
        if fmt != "carousel" and not clip and _carousels() and s.get("slides"):
            fmt = "carousel"  # a Reel needs Bryan's footage; without it, the outline makes a carousel
        if kind == "onsite" and not (clip and fmt != "carousel"):
            skip("an On site post needs footage")
            continue
        q = questions.get(s.get("question_id"))
        source = s.get("source") if s.get("source") in SOURCES else "plan"
        if source == "transcript" and not clip:
            source = "basics"
        if source == "question" and not q:
            source = "basics"
        # Built on a wiki article: the card says which, and the idea carries its facts for Write slides.
        w = (ctx.get("wiki") or {}).get((s.get("wiki_slug") or "").strip())
        if source == "wiki" and not w:
            source = "basics"
        if w and source in ("basics", "plan"):
            source = "wiki"
        why = no_figures((s.get("why_now") or "").strip(), allowed)[:300] or "Fills a gap in this month's mix"
        purpose = (s.get("purpose") or "").strip()[:1000]
        if w:
            why = f"From the wiki: {w['title']}. {why}"[:300]
            purpose = with_facts(purpose, w)
        fields = {
            "title": title, "post_type": kind, "format": fmt,
            "brand": settings.SINGLE_BRAND or "urban_makers",
            "hook": _public(hook, names, allowed, 600), "purpose": purpose,
            "hook_options": [{**o, "text": _public(o["text"], names, allowed, 600)} for o in hook_options],
            "cta": _public(s.get("cta"), names, allowed, 400), "caption": _public(s.get("caption"), names, allowed, 2200),
            "est_seconds": s.get("est_seconds") if isinstance(s.get("est_seconds"), (int, float))
            and 5 <= s["est_seconds"] <= 180 else 30,
            "suggestion": {"why": why, "source": source, "clip_id": clip["id"] if clip else None, "at": at,
                           "question_id": q["id"] if q else None, "suggested_at": catalog.now(),
                           **({"wiki": {"slug": w["slug"], "title": w["title"]}} if w else {})},
            "clip": clip, "question": q,
        }
        if fmt == "carousel":
            fields["slides"] = _slides(s.get("slides") or [], names, allowed, {"id": clip["id"], "at": at} if clip else None)
            if len(fields["slides"]) < 2:
                skip("no slide outline")
                continue
            cover = fields["slides"][0]
            if cover["role"] == "cover" and hook != written and _norm(cover["heading"]) == _norm(_public(written, names, allowed, 200)):
                cover["heading"] = fields["hook"][:200]  # the cover is the hook: the stronger one
        else:
            durations = {r["id"]: r["duration"] or 0 for r in ctx["clips"]}
            shots = [footage_ideas._clean_shot(x, durations) for x in s.get("shots") or []]
            if clip and not any(x["clip_id"] == clip["id"] for x in shots):
                shots.insert(0, footage_ideas._clean_shot({
                    "clip_id": clip["id"], "start": at, "end": s.get("end"), "kind": "talking",
                    "description": "Bryan explains it on site", "line": "", "on_screen": fields["hook"]}, durations))
            for x in shots:
                x["line"] = _public(x["line"], names, allowed, 1000)
                x["on_screen"] = _public(x["on_screen"], names, allowed, 300)
            if not shots:
                skip("no storyboard")
                continue
            fields["shots"] = shots
        keep.append(fields)
    return keep, skipped


# ── Where the ideas go ────────────────────────────────────────────────────────

def topics_project(store) -> dict:
    """The standing Topics project (made by migration 20261020000000; made again if missing)."""
    mine = [p for p in store.projects() if p.get("standing") == "topics"]
    mine = mine or [p for p in store.projects() if (p.get("name") or "").strip().lower() == "topics"]
    if mine:
        return sorted(mine, key=lambda p: (p.get("standing") != "topics", p["status"] != "active", p["id"]))[0]
    row = store.api.insert("projects", {"name": TOPICS, "folder": TOPICS, "brief": TOPICS_BRIEF, "standing": "topics",
                                        "brand": settings.SINGLE_BRAND or "urban_makers"})[0]
    log.info("Made the standing project \"%s\" in the planner", TOPICS)
    store.projects().append(row)
    return row


def _arc(store, ref) -> int:
    arcs = store.api.select("arcs", {"project_id": f"eq.{ref}", "select": "id,title,position"})
    mine = next((a for a in arcs if a["title"].strip().lower() == ARC_TITLE.lower()), None)
    if mine:
        return mine["id"]
    first = min((a["position"] for a in arcs), default=1)
    return store.api.insert("arcs", {"project_id": ref, "title": ARC_TITLE, "purpose": ARC_PURPOSE,
                                     "position": first - 1})[0]["id"]


def _home(store, clip) -> dict:
    """A Reel from footage lives with its footage: the clip's project (active), else Everyday posts."""
    ref = store.ref_of(clip["project_id"])
    p = store.project(ref) if ref else None
    return p if p and p.get("status") == "active" else store.everyday()


def write(store, picks: list[dict], run_id: int) -> list[dict]:
    """Write the suggestions to the planner. Returns [{id, title, hook, post_type, format,
    project, why, source, clip_id, question_id, wiki}]."""
    out = []
    topics = None
    for f in picks:
        f["suggestion"]["run"] = run_id
        if f["format"] != "carousel" and f["clip"]:
            project = _home(store, f["clip"])
            arc_id = store.arc(project["id"])
        else:
            topics = topics or topics_project(store)
            project, arc_id = topics, _arc(store, topics["id"])
        idea_id = store.add_idea(project["id"], arc_id, f)
        try:
            if f["format"] != "carousel":
                shots = f["shots"]
                shot_ids = store.add_shots(idea_id, shots)
                store.add_takes([(sid, s["clip_id"], s["start"], s["end"]) for sid, s in zip(shot_ids, shots) if s["clip_id"]],
                                note=TAKE_NOTE)
        except Exception:
            footage_ideas._forget(store, idea_id)
            raise
        if f["question"]:
            try:  # the question now points at its post (the planner shows "suggested")
                store.api.update("client_questions", {"id": f"eq.{f['question']['id']}", "status": "eq.open"},
                                 {"status": "suggested", "idea_id": idea_id})
            except Exception as e:
                log.warning("Couldn't link client question %s to idea %s: %s", f["question"]["id"], idea_id, e)
        out.append({"id": idea_id, "title": f["title"], "hook": f["hook"], "post_type": f["post_type"], "format": f["format"],
                    "project": project["name"], "why": f["suggestion"]["why"], "source": f["suggestion"]["source"],
                    "clip_id": f["suggestion"]["clip_id"], "question_id": f["suggestion"]["question_id"],
                    "wiki": (f["suggestion"].get("wiki") or {}).get("slug")})
    return out


# ── One run ───────────────────────────────────────────────────────────────────

def wiki_used(ideas: list[dict], memory: list[dict]) -> tuple[dict, set]:
    """({slug: [titles of posts made from it]}, {slugs with a suggestion still waiting}): which wiki articles
    were used already (in the planner, or suggested before and since deleted), so the least used go first."""
    used: dict[str, list[str]] = {}
    seen, waiting = set(), set()
    for i in ideas:
        slug = ((i.get("suggestion") or {}).get("wiki") or {}).get("slug") if isinstance(i.get("suggestion"), dict) else None
        if not slug:
            continue
        seen.add(i.get("id"))
        if is_open(i):
            waiting.add(slug)
        title = (i.get("title") or "").strip()[:120]
        used.setdefault(slug, []).append(title if i.get("status") != "dismissed" else f"{title} (dismissed)")
    for m in memory:
        if m.get("wiki") and m.get("id") not in seen:
            seen.add(m.get("id"))
            used.setdefault(m["wiki"], []).append((m.get("title") or "").strip()[:120])
    return used, waiting


def _ideas(api) -> list[dict]:
    return api.select("ideas", {"select": "id,title,hook,status,decision,post_type,format,project_id,suggestion,production",
                                "order": "id.desc"})


def context(api, conn, now: datetime | None = None) -> dict:
    """Everything one call needs, read fresh."""
    ideas = _ideas(api)
    month = content_plan.month(api)
    plan = content_plan.load(api)
    planned, dismissed = taken(ideas, remembered(conn))
    clips = transcripts(conn, now)
    questions = _questions(api)
    names = _names(api)
    count = max(settings.TOPICS_COUNT, 1)
    uses = clip_uses(api)
    projects = api.select("projects", {"select": "name,folder,standing"})
    project_names = [n for p in projects if not p.get("standing") for n in (p.get("name"), p.get("folder"))]
    used, waiting = wiki_used(ideas, remembered(conn))
    wiki_text, wiki_articles = wiki.digest(wiki.articles(), used, project_names, skip=waiting)
    allowed = " ".join([*(r["transcript"] or "" for r in clips),
                        *(f"{q.get('question', '')} {q.get('note', '')}" for q in questions),
                        *(f"{o.get('details', '')} {o.get('ask', '')}" for o in plan.get("offers") or [])]).lower()
    return {"now": now, "ideas": ideas, "month": month, "plan_text": content_plan.prompt_text(plan, month),
            "gaps": gaps(month, ideas), "count": count, "planned": planned, "dismissed": dismissed,
            "clips": clips, "uses": uses, "transcripts_text": _transcripts_text(clips, uses),
            "questions": questions, "questions_text": _questions_text(questions, names),
            "references_text": _references_text(api), "calendar_text": _calendar_text(_calendar(api, now)),
            "wiki_text": wiki_text, "wiki": wiki_articles,
            "names": project_names, "allowed": allowed}


def run(api, conn, trigger: str, client=None, now: datetime | None = None) -> dict:
    """One run: read, ask Claude once, write. Returns {new_ideas, idea_ids, suggestions, skipped,
    notes}. Raises RuntimeError (a plain reason) when it fails; the run is recorded either way."""
    stamp = catalog.now()
    run_id = conn.execute("""INSERT INTO idea_runs (trigger, place, project, state, started_at)
                             VALUES (?, 'cloud', ?, 'running', ?)""", (f"topics:{trigger}", TOPICS, stamp)).lastrowid
    conn.commit()
    try:
        ctx = context(api, conn, now)
        answer = _ask(prompt(ctx), f"topics:{run_id}", client)
        picks, skipped = clean(answer, ctx)
        # The words viewers read: safe fixes, and one rewrite of what still reads machine-written.
        wording.polish_ideas(picks, "topics", f"topics:{run_id}", client)
        written = write(footage_ideas._Cloud(api, conn), picks, run_id)
        result = {"new_ideas": [w["id"] for w in written], "suggestions": written, "skipped": skipped,
                  "gaps": ctx["gaps"], "notes": (answer.get("notes") or "")[:1000]}
        conn.execute("UPDATE idea_runs SET state = 'done', result = ?, clip_ids = ?, finished_at = ? WHERE id = ?",
                     (json.dumps(result), json.dumps(sorted({w["clip_id"] for w in written if w["clip_id"]})),
                      catalog.now(), run_id))
        conn.commit()
        log.info("Suggested topics (%s): %d new, %d left out", trigger, len(written), len(skipped))
        if written:
            import notify
            notify.digest.add("topics_new", len(written))
        return {"new_ideas": len(written), "idea_ids": result["new_ideas"], "suggestions": written,
                "skipped": skipped, "notes": result["notes"]}
    except Exception as e:
        import cloud_sync
        message = str(e) if isinstance(e, RuntimeError) else f"{type(e).__name__}: {e}"
        if isinstance(e, (RuntimeError, KeyError, ValueError, cloud_sync.CloudError)):
            log.warning("Suggested topics failed (%s): %s", trigger, message)
        else:
            log.exception("Suggested topics failed (%s)", trigger)
        conn.rollback()
        if isinstance(e, cloud_sync.CloudError) and "can't reach" in str(e):
            # Supabase unreachable: not a real try (the once-a-day cap doesn't count it).
            conn.execute("DELETE FROM idea_runs WHERE id = ?", (run_id,))
            conn.commit()
            raise
        conn.execute("UPDATE idea_runs SET state = 'failed', error = ?, finished_at = ? WHERE id = ?",
                     (message[:1000], catalog.now(), run_id))
        conn.commit()
        raise RuntimeError(message) from e


def run_due(api, conn, ideas: list[dict], client=None, now: datetime | None = None) -> int | None:
    """The automatic run, from the sync, when it's due and Studio isn't busy (None when nothing
    ran). ideas: the planner's ideas from this tick's pull (for the cheap checks)."""
    global _checked_at
    if settings.TOPICS_COUNT <= 0:
        return None
    local = _sgt(now)
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    if _runs(conn, midnight, triggers=("weekly", "behind")):
        return None
    slot = week_slot(now)
    weekly = bool(slot and not _runs(conn, slot - timedelta(hours=EARLY_HOURS), states=("done",)))
    behind = settings.TOPICS_BEHIND and local.day > 14
    if not weekly and not behind:
        return None
    if not weekly and time.time() - _checked_at < CHECK_SECONDS:
        return None  # the "falls behind" check asks the database every 10 minutes at most
    _checked_at = time.time()
    month = content_plan.month(api) if behind and not weekly else None
    trigger = due(conn, month, ideas, now)
    if not trigger or busy(conn):
        return None
    return run(api, conn, trigger, client, now)["new_ideas"]


def run_job(api, conn, client=None) -> dict:
    """The planner's "Suggest topics" (job kind "topics"). Returns the job result."""
    out = run(api, conn, "job", client)
    return {"new_ideas": out["new_ideas"], "idea_ids": out["idea_ids"], "skipped": len(out["skipped"]),
            "notes": out["notes"]}


def status(conn) -> dict:
    """For the planner (laptop_status.info.topics): when Studio suggests by itself, and the last run."""
    out = {"day": settings.TOPICS_DAY if settings.TOPICS_DAY in DAYS else "off", "hour": settings.TOPICS_HOUR,
           "count": settings.TOPICS_COUNT, "behind": settings.TOPICS_BEHIND, "last_run": None, "last_suggested": None,
           "wiki": wiki.info()}
    last = conn.execute("SELECT * FROM idea_runs WHERE trigger LIKE 'topics:%' ORDER BY id DESC LIMIT 1").fetchone()
    if last:
        result = json.loads(last["result"] or "{}")
        out["last_run"] = {"at": last["finished_at"] or last["started_at"], "state": last["state"],
                           "trigger": last["trigger"].split(":", 1)[-1], "new_ideas": len(result.get("new_ideas", [])),
                           "error": last["error"]}
    good = conn.execute("""SELECT finished_at FROM idea_runs WHERE trigger LIKE 'topics:%' AND state = 'done'
                           AND result NOT LIKE '%"new_ideas": []%' ORDER BY id DESC LIMIT 1""").fetchone()
    out["last_suggested"] = good["finished_at"] if good else None
    return out
