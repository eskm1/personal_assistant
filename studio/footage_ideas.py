"""
Ideas from new footage. After a shoot day's uploads (new clips tagged, then no new
clip for STUDIO_IDEAS_AFTER_MINUTES), Claude reads each project's new clips with the
project's brief and the posts already planned for it, and:
  - adds the new footage to those posts: a take for the shot it films, or a new shot
    of that post when it fits the post but no shot covers it;
  - suggests up to 3 new posts built from the footage (shots it already has, plus the
    shots still to film), as proposed ideas in the project's "From your footage" arc,
    for Bryan to Go / Park in the planner.
Clips outside any project folder go to a standing project, "Everyday posts".

With online planning (cloud_sync.py) everything is written to the planner and the
local mirror picks it up on the next pull; this runs inside the sync, after the
clips are pushed, so takes can point at them. Without it, it goes into this
machine's plan. Every run is recorded (catalog table idea_runs, clips.ideas_run) so
a clip is read once; a failed run is logged and tried again at the next quiet
period (MAX_TRIES per clip), never stopping the worker. The planner can also ask
for it (job kind "ideas"): one project's footage of the last JOB_DAYS days.

When planning is online, ideas live only in the planner: the library ideas Review
used to show ("Suggest posts") are moved there once (migrate_library).
"""
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import anthropic

import catalog
import content_plan
import playbook
import settings

log = logging.getLogger("studio.footage_ideas")

ARC_TITLE = "From your footage"
ARC_PURPOSE = "Posts Studio suggested from footage the team uploaded."
EVERYDAY = "Everyday posts"
EVERYDAY_BRIEF = ("Everyday Urban Makers posts from footage that isn't part of one renovation project: "
                  "site visits, Bryan's views on renovating, the team at work, the business day to day.")
TAKE_NOTE = "Found in new footage"
LIBRARY_NOTE = "From Suggest posts"
MAX_NEW_IDEAS = 3
MAX_TRIES = 3              # failed runs per clip before it's left alone
JOB_DAYS = 14              # the planner's "ideas" job looks this far back
BASELINE_HOURS = 24        # on first use, older clips count as already read (no bill for the whole library)
NEW_CHAR_BUDGET = 120_000  # new clips per call; the rest wait for the next run (~30k tokens)
OLDER_CLIPS = 30           # the project's earlier clips Claude may also use (no transcripts)
OPEN_IDEAS_MAX = 40
TRANSCRIPT_CHARS = 2500
MIN_SPAN = 0.3

# idea_runs also records the suggested topics' runs (topics.py, trigger "topics:..."): not these.
_MINE = "trigger NOT LIKE 'topics:%'"

_last_library_failure: dict[int, float] = {}   # library idea id -> when migrating it failed
LIBRARY_RETRY_SECONDS = 3600


# ── What Claude returns ───────────────────────────────────────────────────────

def _schema() -> dict:
    import plan
    shot = {
        "type": "object",
        "properties": {
            "clip_id": {"type": "integer"}, "start": {"type": "number"}, "end": {"type": "number"},
            "kind": {"type": "string", "enum": plan.SHOT_KINDS},
            "framing": {"type": "string", "enum": plan.FRAMINGS},
            "camera": {"type": "string", "enum": list(plan.CAMERAS)},
            "description": {"type": "string"}, "line": {"type": "string"}, "on_screen": {"type": "string"},
            "seconds": {"type": "number"}, "why": {"type": "string"},
        },
        "required": ["clip_id", "start", "end", "kind", "framing", "camera", "description", "line", "on_screen",
                     "seconds", "why"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "attach": {"type": "array", "items": {
                "type": "object",
                "properties": {
                    "idea_id": {"type": "integer"}, "shot_id": {"type": "integer"},
                    "clip_id": {"type": "integer"}, "start": {"type": "number"}, "end": {"type": "number"},
                    "description": {"type": "string"}, "kind": {"type": "string", "enum": plan.SHOT_KINDS},
                    "why": {"type": "string"},
                },
                "required": ["idea_id", "shot_id", "clip_id", "start", "end", "description", "kind", "why"],
                "additionalProperties": False}},
            "new_ideas": {"type": "array", "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"}, "brand": {"type": "string", "enum": list(settings.BRANDS)},
                    "post_type": {"type": "string", "enum": list(content_plan.KINDS)},
                    "format": {"type": "string", "enum": ["talking_head", "day_in_the_life", "compilation"]
                               + (["carousel"] if _carousels() else [])},
                    "hook": {"type": "string"}, "purpose": {"type": "string"}, "cta": {"type": "string"},
                    "caption": {"type": "string"}, "est_seconds": {"type": "number"},
                    "shots": {"type": "array", "items": shot},
                },
                "required": ["title", "brand", "post_type", "format", "hook", "purpose", "cta", "caption", "est_seconds",
                             "shots"],
                "additionalProperties": False}},
            "notes": {"type": "string"},
        },
        "required": ["attach", "new_ideas", "notes"],
        "additionalProperties": False,
    }


_SYSTEM = """You are the content producer for Bryan's Instagram Reels. The team has just \
uploaded new footage (most likely a shoot day). Fit it into the posts already planned for this \
project, and suggest a few new posts the footage supports.

{brands}

Bryan's playbook (his editing and story rules):

<playbook>
{playbook}
</playbook>

What to return:
- attach: each piece of NEW footage that fits a post already planned. shot_id is the planned \
shot it films, with start and end seconds inside the clip. When it fits the post but no shot \
covers it, use shot_id 0 and describe the new shot (description, kind, why) the post should gain. \
Only attach footage that really works for that shot; a clip may fit several posts, and footage \
that fits nothing is left out. Shots marked "has footage" already have a take: attach to them only \
when the new footage is clearly better. description, kind and why are ignored when shot_id is set.
- new_ideas: at most {count} new posts built mainly from the new footage (the project's earlier \
footage may help). Each is a complete post of 15 to 60 seconds with one clear point; don't repeat \
a post already planned. shots is the storyboard in playback order: a shot the footage already has \
gives its clip_id with start and end seconds (use the transcript and moment times so speech \
starts and ends on whole phrases); a shot still to film has clip_id 0, start 0 and end 0, and says \
exactly what to film. kind: talking, broll, detail, before_after, reaction, screen. framing: wide, \
medium, close, detail, pov, overhead. camera: {cameras}. Return no new ideas when the footage \
doesn't support a strong post: fewer, stronger posts beat stretching thin footage.
{carousel}- post_type: the kind of post, by the job it does (the content plan, with the message, lists the \
five). The footage decides what a post can be; among the posts it supports, prefer the kinds that \
are short this month.
- hook: the opening line or visual of the first 3 seconds, quoted from the transcript when possible. \
purpose: who it's for and what they should think, feel or do. cta: the call to action at the end, \
fitting the kind's ask; an offer only from the listed offers, worded as listed. \
caption: a ready-to-edit Instagram caption in Bryan's voice with 3 to 6 hashtags.
- Never invent facts, prices, results, timelines, promotions, offers or client details: use only \
what the footage shows, what Bryan says and the listed offers. Write [placeholder] where the team \
must fill something in.
- Never name a place (estate, street, condo, block, town, a client's address) in a hook, caption \
or on-screen text: where clients live stays private.
- Clips marked "retake" have a quality problem: prefer other footage, and use them only when \
nothing else shows that moment.
- notes: one or two sentences for the team (what the footage was, anything worth filming next)."""


def _ask(user: str, ref: str, client=None) -> dict:
    """One structured Claude call (effort medium); cost is recorded as "ideas"."""
    import llm
    import plan
    import usage
    client = client or anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY or None)
    system = (_SYSTEM.replace("{brands}", settings.brands_text()).replace("{playbook}", playbook.text())
              .replace("{count}", str(MAX_NEW_IDEAS)).replace("{carousel}", CAROUSEL_RULE if _carousels() else "")
              .replace("{cameras}", "; ".join(f"{k} = {v}" for k, v in plan.CAMERAS.items())))
    response = llm.final_message(
        client, model=settings.CLAUDE_MODEL, max_tokens=32000,
        betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": _schema()}},
        system=system, messages=[{"role": "user", "content": user}])
    usage.record("ideas", ref, response)
    if response.stop_reason == "refusal":
        raise RuntimeError("Claude declined to suggest ideas from this footage")
    if response.stop_reason == "max_tokens":
        raise RuntimeError("Claude's answer was cut off (max_tokens)")
    return json.loads(next(b.text for b in response.content if b.type == "text"))


CAROUSEL_RULE = """- format carousel (rarely): when the footage is a set of clear stills that teach or show \
something people would save (a finished detail from several angles, the steps of a fix), a carousel \
of slides can beat a Reel. Its shots are then the slides' pictures, in order (each with clip_id and \
the moment in start; end as start), on_screen the slide's heading and line its words; the hook \
is the cover and cta the last slide. Studio writes the slides properly next. Keep it to Know-how, \
Ideas or Finished homes posts, and only where it clearly suits: most posts stay Reels.
"""


def _carousels() -> bool:
    """Carousels are online (migration 20261021000000: ideas.slides)."""
    import cloud_sync
    return cloud_sync._carousels_online


def _carousel_slides(fields: dict, shots: list[dict]) -> list[dict]:
    """A carousel idea from footage: its shots as first slides (cover, pictures, ask), for
    "Write slides" to finish (the planner starts it; the pictures are kept)."""
    import uuid
    pictured = [s for s in shots if s.get("clip_id")]
    def image(s):
        return {"source": "clip", "clip_id": s["clip_id"], "at": round(float(s.get("start") or 0), 1)} if s else {"source": "text"}
    slides = [{"key": uuid.uuid4().hex[:10], "role": "cover", "heading": fields["hook"][:200] or fields["title"][:200],
               "body": "", "image": image(pictured[0] if pictured else None)}]
    for s in shots[:9]:
        slides.append({"key": uuid.uuid4().hex[:10], "role": "point",
                       "heading": (s.get("on_screen") or s["description"])[:200], "body": (s.get("line") or "")[:800],
                       "image": image(s if s.get("clip_id") else None)})
    if fields.get("cta"):
        slides.append({"key": uuid.uuid4().hex[:10], "role": "ask", "heading": fields["cta"][:200], "body": "",
                       "image": {"source": "text"}})
    return slides[:12]


# ── Where the ideas go: the online planner, or this machine's plan ───────────

def _is_everyday(p: dict) -> bool:
    return p.get("standing") == "everyday" or EVERYDAY.lower() in (
        (p.get("name") or "").strip().lower(), (p.get("folder") or "").strip().lower())


def _plan_online() -> bool:
    """The content-plan migration is online (ideas.post_type, projects.standing)."""
    import cloud_sync
    return cloud_sync._content_plan_online


class _Cloud:
    """The online planner (Supabase, service key). Ids are the planner's."""
    place = "cloud"

    def __init__(self, api, conn):
        self.api, self.conn = api, conn
        self._projects: list[dict] | None = None

    def projects(self) -> list[dict]:
        if self._projects is None:
            self._projects = self.api.select("projects", {
                "select": "id,name,folder,brief,status" + (",standing" if _plan_online() else "")})
        return self._projects

    def everyday(self) -> dict:
        mine = [p for p in self.projects() if _is_everyday(p)]
        if mine:
            return sorted(mine, key=lambda p: (p.get("standing") != "everyday", p["status"] != "active", p["id"]))[0]
        row = self.api.insert("projects", {"name": EVERYDAY, "folder": EVERYDAY, "brief": EVERYDAY_BRIEF,
                                           "brand": settings.SINGLE_BRAND or "urban_makers",
                                           **({"standing": "everyday"} if _plan_online() else {})})[0]
        log.info("Made the standing project \"%s\" in the planner", EVERYDAY)
        self.projects().append(row)
        return row

    def project(self, ref) -> dict | None:
        return next((p for p in self.projects() if p["id"] == ref), None)

    def ref_of(self, local_project_id) -> int | None:
        if not local_project_id:
            return None
        row = self.conn.execute("SELECT remote_id FROM projects WHERE id = ?", (local_project_id,)).fetchone()
        return row[0] if row and row[0] else None

    def local_projects(self, ref) -> list[int]:
        return [r[0] for r in self.conn.execute("SELECT id FROM projects WHERE remote_id = ?", (ref,))]

    def open_ideas(self, ref) -> list[dict]:
        rows = self.api.select("ideas", {
            "project_id": f"eq.{ref}", "status": "eq.new", "decision": "in.(proposed,go)",
            "select": "id,title,hook,purpose,format,decision," + ("post_type," if _plan_online() else "") +
                      "shots(id,position,kind,description,line,filmed_at,takes(clip_id,start,end))"})
        for r in rows:
            r["shots"] = sorted(r.get("shots") or [], key=lambda s: (s["position"], s["id"]))
        return rows

    def arc(self, ref) -> int:
        arcs = self.api.select("arcs", {"project_id": f"eq.{ref}", "select": "id,title,position"})
        mine = next((a for a in arcs if a["title"].strip().lower() == ARC_TITLE.lower()), None)
        if mine:
            return mine["id"]
        first = min((a["position"] for a in arcs), default=1)
        return self.api.insert("arcs", {"project_id": ref, "title": ARC_TITLE, "purpose": ARC_PURPOSE,
                                        "position": first - 1})[0]["id"]

    def add_idea(self, ref, arc_id, f: dict) -> int:
        taken = self.api.select("ideas", {"arc_id": f"eq.{arc_id}", "select": "id,position"}) if arc_id else []
        return self.api.insert("ideas", {
            "project_id": ref, "arc_id": arc_id, "title": f["title"], "brand": f["brand"], "format": f["format"],
            "hook": f["hook"], "purpose": f["purpose"], "cta": f["cta"], "caption": f["caption"],
            "est_seconds": f["est_seconds"], "decision": "proposed", "status": "new",
            **({"post_type": f["post_type"]} if f.get("post_type") and _plan_online() else {}),
            **({"slides": f["slides"]} if f.get("slides") else {}),
            # Why Studio suggested it (topics.py; migration 20261022000000), for the planner's card.
            **({"suggestion": f["suggestion"]} if f.get("suggestion") else {}),
            "position": max((i["position"] for i in taken), default=0) + 1})[0]["id"]

    def write_slides(self, ref, idea_id) -> None:
        """Queue "Write slides" for a carousel from footage (the planner starts it, like a storyboard)."""
        self.api.insert("jobs", {"kind": "develop", "runner": "cloud", "project_id": ref, "idea_id": idea_id,
                                 "state": "queued", "payload": {"request": "Write the slides from the footage: keep "
                                                                "the pictures the current slides have."}},
                        returning=False)

    def add_shots(self, idea_id, shots: list[dict]) -> list[int]:
        if not shots:
            return []
        have = self.api.select("shots", {"idea_id": f"eq.{idea_id}", "select": "id,position"})
        start = max((s["position"] for s in have), default=0)
        rows = [{"idea_id": idea_id, "position": start + n, "kind": s.get("kind"), "framing": s.get("framing"),
                 "camera": s.get("camera"), "description": s["description"], "line": s.get("line") or "",
                 "on_screen": s.get("on_screen") or "", "seconds": s.get("seconds"), "why": s.get("why") or ""}
                for n, s in enumerate(shots, 1)]
        created = {r["position"]: r["id"] for r in self.api.insert("shots", rows)}
        return [created[r["position"]] for r in rows]

    def add_takes(self, takes: list[tuple], note: str = TAKE_NOTE) -> int:
        if not takes:
            return 0
        created = self.api.insert("takes", [{"shot_id": s, "clip_id": c, "start": a, "end": b, "source": "auto",
                                             "note": note} for s, c, a, b in takes],
                                  on_conflict="shot_id,clip_id", resolution="ignore-duplicates")
        return len(created)

    def delete_idea(self, idea_id) -> None:
        self.api.request("DELETE", "/rest/v1/ideas", params={"id": f"eq.{idea_id}"})

    def clip_ready(self, clip_id) -> bool:
        row = self.conn.execute("SELECT cloud_hash FROM clips WHERE id = ?", (clip_id,)).fetchone()
        return bool(row and row[0])


class _Local:
    """This machine's plan (planning not online). Ids are local."""
    place = "local"

    def __init__(self, conn):
        self.conn = conn

    def everyday(self) -> dict:
        row = self.conn.execute(
            """SELECT * FROM projects WHERE LOWER(name) = LOWER(?) OR LOWER(COALESCE(folder, '')) = LOWER(?)
               ORDER BY status != 'active', id LIMIT 1""", (EVERYDAY, EVERYDAY)).fetchone()
        if row is None:
            cur = self.conn.execute(
                "INSERT INTO projects (name, brand, folder, brief, status, created_at) VALUES (?, ?, ?, ?, 'active', ?)",
                (EVERYDAY, settings.SINGLE_BRAND or "urban_makers", EVERYDAY, EVERYDAY_BRIEF, catalog.now()))
            self.conn.commit()
            log.info("Made the standing project \"%s\"", EVERYDAY)
            import cloud_sync
            cloud_sync._make_inbox_folder(EVERYDAY)
            row = self.conn.execute("SELECT * FROM projects WHERE id = ?", (cur.lastrowid,)).fetchone()
        return dict(row)

    def project(self, ref) -> dict | None:
        row = self.conn.execute("SELECT * FROM projects WHERE id = ?", (ref,)).fetchone()
        return dict(row) if row else None

    def ref_of(self, local_project_id) -> int | None:
        return local_project_id or None

    def local_projects(self, ref) -> list[int]:
        return [ref]

    def open_ideas(self, ref) -> list[dict]:
        import plan
        rows = [dict(r) for r in self.conn.execute(
            """SELECT id, title, hook, purpose, format, post_type, COALESCE(decision, 'proposed') AS decision FROM ideas
               WHERE project_id = ? AND origin = 'plan' AND status = 'new'
               AND COALESCE(decision, 'proposed') IN ('proposed', 'go') ORDER BY id""", (ref,))]
        shots = plan.shots_for(self.conn, [r["id"] for r in rows])
        for r in rows:
            r["shots"] = [{"id": s["id"], "position": s["position"], "kind": s["kind"], "description": s["description"],
                           "line": s["line"], "filmed_at": s["filmed_at"],
                           "takes": [{"clip_id": t["clip_id"], "start": t["start"], "end": t["end"]} for t in s["takes"]]}
                          for s in shots.get(r["id"], [])]
        return rows

    def arc(self, ref) -> int:
        import plan
        row = self.conn.execute("SELECT id FROM arcs WHERE project_id = ? AND LOWER(title) = LOWER(?)",
                                (ref, ARC_TITLE)).fetchone()
        if row:
            return row[0]
        first = self.conn.execute("SELECT MIN(position) FROM arcs WHERE project_id = ?", (ref,)).fetchone()[0]
        arc_id = plan.add_arc(self.conn, ref, ARC_TITLE, ARC_PURPOSE)
        self.conn.execute("UPDATE arcs SET position = ? WHERE id = ?", ((first if first is not None else 1) - 1, arc_id))
        self.conn.commit()
        return arc_id

    def add_idea(self, ref, arc_id, f: dict) -> int:
        import plan
        return plan.add_idea(self.conn, ref, arc_id, f)

    def add_shots(self, idea_id, shots: list[dict]) -> list[int]:
        import plan
        ids = [plan.add_shot(self.conn, idea_id, s, commit=False) for s in shots]
        self.conn.commit()
        return ids

    def add_takes(self, takes: list[tuple], note: str = TAKE_NOTE) -> int:
        n = 0
        for s, c, a, b in takes:
            n += self.conn.execute("""INSERT OR IGNORE INTO takes (shot_id, clip_id, start, end, source, note, created_at)
                                      VALUES (?, ?, ?, ?, 'auto', ?, ?)""", (s, c, a, b, note, catalog.now())).rowcount
        self.conn.commit()
        return n

    def delete_idea(self, idea_id) -> None:
        import cloud_sync
        cloud_sync._delete_ideas(self.conn, [idea_id])
        self.conn.commit()

    def clip_ready(self, clip_id) -> bool:
        return True


def _store(conn, api=None):
    return _Cloud(api, conn) if api is not None else _Local(conn)


# ── What Claude reads ─────────────────────────────────────────────────────────

def _transcript(r) -> str:
    import ideas
    try:
        text = ideas._segments_text(Path(r["clip_dir"]), r["transcript"])
    except (OSError, ValueError, KeyError, TypeError):
        text = f"    {r['transcript']}" if r["transcript"] else "    (no speech)"
    return text if len(text) <= TRANSCRIPT_CHARS else text[:TRANSCRIPT_CHARS].rsplit("\n", 1)[0] + "\n    [...]"


def _verdict(r) -> str:
    q = json.loads(r["quality"] or "{}")
    v = q.get("verdict")
    if v == "retake":
        return "retake (" + "; ".join(f.get("text", "") for f in q.get("flags", [])) + ")"
    return "usable" if v else "not checked"


def clip_text(r, transcript: bool = True) -> str:
    moments = json.loads(r["moments"] or "[]")
    lines = [
        f"Clip {r['id']}: {r['title'] or r['source_name']}",
        f"  {r['duration'] or 0:.1f}s | {r['orientation'] or '?'} | recorded {r['recorded_at'] or 'unknown'} | "
        f"quality: {_verdict(r)}",
        f"  summary: {r['summary'] or '-'}",
        "  moments: " + ("; ".join(f"{m['start']:.1f}-{m['end']:.1f}s {m.get('kind', '')}: {m.get('description', '')}"
                                   for m in moments) or "-"),
    ]
    if transcript:
        lines += ["  transcript:", _transcript(r)]
    return "\n".join(lines)


def _ideas_text(ideas: list[dict]) -> str:
    if not ideas:
        return "(none planned yet)"
    out = []
    for i in ideas:
        kind = content_plan.KINDS.get(i.get("post_type"), {}).get("label")
        out.append(f"Post {i['id']} \"{i['title']}\" ({i['decision']}, {i['format'] or 'talking_head'}"
                   + (f", {kind}" if kind else "") + "): "
                   f"hook: {i['hook'] or '-'}; purpose: {i['purpose'] or '-'}")
        for s in i["shots"]:
            takes = s.get("takes") or []
            state = ("has footage (" + ", ".join(f"clip {t['clip_id']}" for t in takes) + ")" if takes
                     else "filmed, footage not in yet" if s.get("filmed_at") else "to film")
            out.append(f"  shot {s['id']} [{state}] ({s.get('kind') or '-'}) {s['description']}"
                       + (f" | says: {s['line']}" if s.get("line") else ""))
    return "\n".join(out)


def _prompt(project: dict, ideas: list[dict], new: list, older: list, plan_text: str = "") -> str:
    """plan_text: the content plan (content_plan.context_text): the kinds, this month's gaps, the offers."""
    return (f"Project: {project['name']}\nBrief: {project.get('brief') or '(none yet)'}\n\n"
            f"The content plan:\n<content_plan>\n{plan_text or content_plan.prompt_text()}\n</content_plan>\n\n"
            f"Posts already planned (open):\n{_ideas_text(ideas)}\n\n"
            f"NEW footage ({len(new)} clips):\n\n" + "\n\n".join(clip_text(r) for r in new) + "\n\n"
            + ("Earlier footage of this project (may be used in new posts; no transcripts):\n\n"
               + "\n\n".join(clip_text(r, transcript=False) for r in older) if older else
               "Earlier footage of this project: none."))


# ── Cleaning Claude's answer ──────────────────────────────────────────────────

def _span(start, end, duration: float) -> tuple[float, float]:
    start, end = max(0.0, float(start or 0)), float(end or 0)
    if duration:
        end = min(duration, end)
    if end - start < MIN_SPAN:  # no usable span given: the whole clip
        start, end = 0.0, duration or 0.0
    return round(start, 2), round(end, 2)


def _private(text: str, names: list[str]) -> str:
    """Belt and braces for "place names stay internal": the project's name or folder
    (often an estate or street) never reaches a hook or caption."""
    for n in names:
        n = (n or "").strip()
        if len(n) >= 4 and n.lower() != EVERYDAY.lower():
            text = re.sub(rf"(?i)\b{re.escape(n)}\b", "this home", text)
    return text


def _clean_shot(s: dict, durations: dict[int, float]) -> dict:
    import plan
    out = {"kind": s.get("kind") if s.get("kind") in plan.SHOT_KINDS else None,
           "framing": s.get("framing") if s.get("framing") in plan.FRAMINGS else None,
           "camera": s.get("camera") if s.get("camera") in plan.CAMERAS else None,
           "description": (s.get("description") or "").strip()[:1000] or "Shot",
           "line": (s.get("line") or "")[:1000], "on_screen": (s.get("on_screen") or "")[:300],
           "seconds": s.get("seconds") if isinstance(s.get("seconds"), (int, float)) and 0 < s["seconds"] <= 120 else None,
           "why": (s.get("why") or "")[:500], "clip_id": None}
    clip = s.get("clip_id")
    if clip and clip in durations:
        out["clip_id"] = clip
        out["start"], out["end"] = _span(s.get("start"), s.get("end"), durations[clip])
    return out


# ── One project's run ─────────────────────────────────────────────────────────

def _apply(store, ref, project: dict, ideas: list[dict], answer: dict, new_ids: set[int],
           durations: dict[int, float]) -> dict:
    """Write Claude's answer. Returns what was done."""
    names = [project.get("name"), project.get("folder")]
    result = {"new_ideas": [], "attached": [], "notes": (answer.get("notes") or "")[:1000]}
    # (a) new footage into the posts already planned
    by_idea = {i["id"]: i for i in ideas}
    seen: set[tuple] = set()
    for a in answer.get("attach", []):
        idea, clip = by_idea.get(a.get("idea_id")), a.get("clip_id")
        if idea is None or clip not in new_ids:
            continue
        start, end = _span(a.get("start"), a.get("end"), durations[clip])
        shot_ids = {s["id"] for s in idea["shots"]}
        if a.get("shot_id") in shot_ids:
            shot_id, new_shot = a["shot_id"], False
            if (shot_id, clip) in seen or any(t["clip_id"] == clip for s in idea["shots"] if s["id"] == shot_id
                                              for t in s.get("takes") or []):
                continue
        elif not a.get("shot_id"):
            if (idea["id"], clip, "new") in seen:
                continue
            seen.add((idea["id"], clip, "new"))
            shot = _clean_shot({**a, "clip_id": None}, durations)
            shot_id = store.add_shots(idea["id"], [shot])[0]
            idea["shots"].append({"id": shot_id, "description": shot["description"], "takes": []})
            new_shot = True
        else:
            continue  # a shot of another post, or made up
        seen.add((shot_id, clip))
        if store.add_takes([(shot_id, clip, start, end)]):
            result["attached"].append({"idea_id": idea["id"], "shot_id": shot_id, "clip_id": clip,
                                       "start": start, "end": end, "new_shot": new_shot})
    # (b) new posts
    arc_id = None
    for idea in answer.get("new_ideas", [])[:MAX_NEW_IDEAS]:
        shots = [_clean_shot(s, durations) for s in idea.get("shots", [])]
        if not any(s["clip_id"] for s in shots):
            log.info("Left out the idea %r: none of its shots use footage Studio has", idea.get("title"))
            continue
        if arc_id is None:
            arc_id = store.arc(ref)
        fields = {
            "title": (idea.get("title") or "Untitled idea").strip()[:300],
            "brand": settings.SINGLE_BRAND or (idea.get("brand") if idea.get("brand") in settings.BRANDS else "urban_makers"),
            "post_type": content_plan.clean_type(idea.get("post_type")),
            "format": idea.get("format") if idea.get("format") in ("talking_head", "day_in_the_life", "compilation")
            or (idea.get("format") == "carousel" and _carousels() and store.place == "cloud") else "talking_head",
            "hook": _private(idea.get("hook") or "", names), "purpose": idea.get("purpose") or "",
            "cta": _private(idea.get("cta") or "", names), "caption": _private(idea.get("caption") or "", names),
            "est_seconds": idea.get("est_seconds") if isinstance(idea.get("est_seconds"), (int, float))
            and 5 <= idea["est_seconds"] <= 180 else 30}
        for s in shots:
            s["on_screen"] = _private(s["on_screen"], names)
            s["line"] = _private(s["line"], names)
        if fields["format"] == "carousel":  # slides, not shots (nothing to film)
            fields["slides"] = _carousel_slides(fields, shots)
            idea_id = store.add_idea(ref, arc_id, fields)
            try:
                store.write_slides(ref, idea_id)
            except Exception:
                log.warning("Couldn't queue Write slides for carousel %s; the planner's button still works", idea_id)
            result["new_ideas"].append(idea_id)
            continue
        idea_id = store.add_idea(ref, arc_id, fields)
        try:
            shot_ids = store.add_shots(idea_id, shots)
            store.add_takes([(sid, s["clip_id"], s["start"], s["end"])
                             for sid, s in zip(shot_ids, shots) if s["clip_id"]])
        except Exception:
            _forget(store, idea_id)  # no half-written idea; the retry writes it whole
            raise
        result["new_ideas"].append(idea_id)
    return result


def _forget(store, idea_id) -> None:
    try:
        store.delete_idea(idea_id)
    except Exception as e:
        log.warning("Couldn't remove the half-written idea %s: %s", idea_id, e)


def _older(conn, local_projects: list[int], everyday: bool, exclude: set[int]) -> list:
    if everyday:
        rows = conn.execute(
            """SELECT * FROM clips WHERE status = 'tagged' AND COALESCE(usable, 1) = 1
               AND (project_id IS NULL OR project_id IN (SELECT id FROM projects WHERE LOWER(name) = LOWER(?)
                    OR LOWER(COALESCE(folder, '')) = LOWER(?)))
               AND ingested_at >= ? ORDER BY ingested_at DESC LIMIT ?""",
            (EVERYDAY, EVERYDAY, (datetime.now(timezone.utc) - timedelta(days=30)).isoformat(), OLDER_CLIPS * 2)).fetchall()
    elif local_projects:
        rows = conn.execute(
            f"""SELECT * FROM clips WHERE status = 'tagged' AND COALESCE(usable, 1) = 1
                AND project_id IN ({','.join('?' * len(local_projects))}) ORDER BY ingested_at DESC LIMIT ?""",
            [*local_projects, OLDER_CLIPS * 2]).fetchall()
    else:
        rows = []
    return [r for r in rows if r["id"] not in exclude][:OLDER_CLIPS]


def _run_project(conn, store, ref, project: dict, clips: list, trigger: str, client=None) -> tuple[int, dict | None, str | None]:
    """One Claude call for one project's new clips, then write the answer.
    Returns (run id, result, error)."""
    stamp = catalog.now()
    run_id = conn.execute(
        """INSERT INTO idea_runs (trigger, place, project_ref, project, clip_ids, state, started_at)
           VALUES (?, ?, ?, ?, ?, 'running', ?)""",
        (trigger, store.place, ref, project["name"], json.dumps([c["id"] for c in clips]), stamp)).lastrowid
    conn.commit()
    ids = [c["id"] for c in clips]
    marks = ",".join("?" * len(ids))
    try:
        ideas = store.open_ideas(ref)
        ideas.sort(key=lambda i: (i["decision"] != "go", -i["id"]))
        ideas = ideas[:OPEN_IDEAS_MAX]
        older = _older(conn, store.local_projects(ref), _is_everyday(project), set(ids))
        durations = {r["id"]: r["duration"] or 0 for r in [*clips, *older]}
        plan_text = content_plan.context_text(store.api if store.place == "cloud" and _plan_online() else None)
        answer = _ask(_prompt(project, ideas, clips, older, plan_text), f"ideas:footage:{run_id}", client)
        result = _apply(store, ref, project, ideas, answer, set(ids), durations)
        result["clips"] = len(ids)
        conn.execute("UPDATE idea_runs SET state = 'done', result = ?, finished_at = ? WHERE id = ?",
                     (json.dumps(result), catalog.now(), run_id))
        conn.execute(f"UPDATE clips SET ideas_run = ? WHERE id IN ({marks})", [run_id, *ids])
        conn.commit()
        log.info("Ideas from new footage, %s: %d clips -> %d new ideas, %d takes added to planned posts",
                 project["name"], len(ids), len(result["new_ideas"]), len(result["attached"]))
        return run_id, result, None
    except Exception as e:
        import cloud_sync
        message = str(e) if isinstance(e, RuntimeError) else f"{type(e).__name__}: {e}"
        offline = isinstance(e, cloud_sync.CloudError) and "can't reach" in str(e)
        if offline:  # Supabase unreachable: not the clips' fault, so no try is counted
            conn.rollback()
            conn.execute("UPDATE idea_runs SET state = 'failed', error = ?, finished_at = ? WHERE id = ?",
                         (message[:1000], catalog.now(), run_id))
            conn.commit()
            raise
        if isinstance(e, (RuntimeError, KeyError, ValueError)):
            log.warning("Ideas from new footage failed for %s (will try again): %s", project["name"], message)
        else:
            log.exception("Ideas from new footage failed for %s (will try again)", project["name"])
        conn.rollback()
        conn.execute("UPDATE idea_runs SET state = 'failed', error = ?, finished_at = ? WHERE id = ?",
                     (message[:1000], catalog.now(), run_id))
        conn.execute(f"UPDATE clips SET ideas_tries = COALESCE(ideas_tries, 0) + 1 WHERE id IN ({marks})", ids)
        # Tried enough: leave these clips alone (the planner's button can still ask again).
        conn.execute(f"UPDATE clips SET ideas_run = ? WHERE id IN ({marks}) AND ideas_tries >= ?",
                     [run_id, *ids, MAX_TRIES])
        conn.commit()
        return run_id, None, message


def _budgeted(clips: list) -> list:
    """As many clips as fit one request (at least one); the rest wait for the next run."""
    out, used = [], 0
    for c in clips:
        size = len(clip_text(c))
        if out and used + size > NEW_CHAR_BUDGET:
            break
        out.append(c)
        used += size
    return out


def _group(store, clips: list) -> dict:
    """{project ref: [clips]}; clips outside any project go to Everyday posts."""
    groups: dict = {}
    everyday = None
    for c in clips:
        ref = store.ref_of(c["project_id"])
        if ref is None or store.project(ref) is None:
            everyday = everyday or store.everyday()
            ref = everyday["id"]
        groups.setdefault(ref, []).append(c)
    return groups


def run(conn, clips: list, trigger: str, api=None, client=None) -> dict:
    """Ideas for these clips, one Claude call per project. Returns a summary:
    {new_ideas, attached (clips added to planned posts), clips, projects: [...], failed: [...]}."""
    store = _store(conn, api)
    unusable = [c["id"] for c in clips if c["usable"] == 0]
    if unusable:  # "as long as videos are usable": unusable ones aren't read
        conn.execute(f"UPDATE clips SET ideas_run = 0 WHERE id IN ({','.join('?' * len(unusable))}) "
                     "AND ideas_run IS NULL", unusable)
        conn.commit()
    clips = [c for c in clips if c["usable"] != 0 and store.clip_ready(c["id"])]
    out = {"new_ideas": 0, "attached": 0, "clips": 0, "projects": [], "failed": [], "idea_ids": []}
    if not clips:
        return out
    for ref, group in _group(store, clips).items():
        group = _budgeted(group)
        project = store.project(ref)
        _, result, error = _run_project(conn, store, ref, project, group, trigger, client)
        if error:
            out["failed"].append(f"{project['name']}: {error[:200]}")
            continue
        attached_clips = len({a["clip_id"] for a in result["attached"]})
        out["new_ideas"] += len(result["new_ideas"])
        out["attached"] += attached_clips
        out["clips"] += len(group)
        out["idea_ids"] += result["new_ideas"]
        out["projects"].append({"id": ref, "name": project["name"], "new_ideas": len(result["new_ideas"]),
                                "attached": attached_clips, "clips": len(group)})
    if out["new_ideas"] or out["attached"]:
        import notify
        today = datetime.now().astimezone().date().isoformat()
        all_today = all(_local_day(c["ingested_at"]) == today for c in clips)
        notify.digest.add("ideas_new", out["new_ideas"])
        notify.digest.add("ideas_attached", out["attached"])
        notify.digest.note("ideas_when", "today's footage" if all_today else "the new footage")
    return out


def _local_day(stamp: str | None) -> str:
    try:
        return datetime.fromisoformat(str(stamp).replace("Z", "+00:00")).astimezone().date().isoformat()
    except ValueError:
        return ""


# ── When: after a burst of uploads, once things are quiet ─────────────────────

def _parse(stamp) -> datetime | None:
    if not stamp:
        return None
    try:
        t = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _baseline(conn) -> None:
    """The first time, clips from before this feature count as read (except the last
    day's), so turning it on doesn't bill a run over the whole library."""
    if conn.execute(f"SELECT 1 FROM idea_runs WHERE {_MINE} LIMIT 1").fetchone():
        return
    stamp = catalog.now()
    run_id = conn.execute("""INSERT INTO idea_runs (trigger, state, result, started_at, finished_at)
                             VALUES ('baseline', 'done', '{}', ?, ?)""", (stamp, stamp)).lastrowid
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=BASELINE_HOURS)).isoformat(timespec="seconds")
    n = conn.execute("UPDATE clips SET ideas_run = ? WHERE ideas_run IS NULL AND ingested_at < ?",
                     (run_id, cutoff)).rowcount
    conn.execute("UPDATE idea_runs SET result = ? WHERE id = ?", (json.dumps({"clips": n}), run_id))
    conn.commit()
    if n:
        log.info("Ideas from new footage is on: %d earlier clips count as already read", n)


def pending(conn, cloud: bool = False) -> list:
    """Tagged clips no run has read yet (in cloud mode: only those already pushed)."""
    return conn.execute(
        "SELECT * FROM clips WHERE status = 'tagged' AND ideas_run IS NULL AND COALESCE(ideas_tries, 0) < ?"
        + (" AND cloud_hash IS NOT NULL" if cloud else "") + " ORDER BY ingested_at, id", (MAX_TRIES,)).fetchall()


def _inbox_busy() -> bool:
    """Footage still arriving: a recent file in the inbox (one sitting there for hours is
    stuck, not arriving) or a OneDrive copy running."""
    try:
        import time
        import onedrive
        import worker
        if onedrive.busy():
            return True
        for f in worker.inbox_files():
            try:
                if time.time() - f.stat().st_mtime < 6 * 3600:
                    return True
            except OSError:
                continue
        return False
    except Exception:  # never let a look at the inbox stop the run
        return False


def quiet(conn, now: datetime | None = None) -> bool:
    """No clip has arrived (and no run failed) for STUDIO_IDEAS_AFTER_MINUTES, nothing is
    being processed and the inbox is empty: the shoot's uploads are done."""
    now = now or datetime.now(timezone.utc)
    wait = timedelta(minutes=settings.IDEAS_AFTER_MINUTES)
    stamps = [_parse(r[0]) for r in conn.execute("SELECT ingested_at FROM clips ORDER BY ingested_at DESC LIMIT 20")]
    newest = next((t for t in stamps if t), None)
    if stamps and newest is None:
        return False  # can't tell when the last clip came: wait
    failed = _parse(conn.execute(f"SELECT MAX(finished_at) FROM idea_runs WHERE state = 'failed' AND {_MINE}").fetchone()[0])
    if any(t and now - t < wait for t in (newest, failed)):
        return False
    recent = (now - timedelta(hours=6)).isoformat(timespec="seconds")
    if conn.execute("SELECT 1 FROM clips WHERE status = 'processing' AND ingested_at > ? LIMIT 1", (recent,)).fetchone():
        return False
    return not _inbox_busy()


def run_due(conn, api=None, client=None) -> dict | None:
    """The automatic run, when it's due (None when it isn't). With api, writes to the
    online planner; call it after the clips were pushed."""
    if settings.IDEAS_AFTER_MINUTES <= 0:
        return None
    _baseline(conn)
    clips = pending(conn, cloud=api is not None)
    if not clips or not quiet(conn):
        return None
    return run(conn, clips, "auto", api, client)


def status(conn) -> dict:
    """For the planner (laptop_status.info.footage_ideas) and the dashboard."""
    last = conn.execute(f"""SELECT * FROM idea_runs WHERE trigger != 'baseline' AND {_MINE} ORDER BY id DESC LIMIT 1""").fetchone()
    waiting = conn.execute("SELECT COUNT(*) FROM clips WHERE status = 'tagged' AND ideas_run IS NULL "
                           "AND COALESCE(ideas_tries, 0) < ?", (MAX_TRIES,)).fetchone()[0]
    out = {"after_minutes": settings.IDEAS_AFTER_MINUTES, "waiting_clips": waiting, "last_run": None}
    if last:
        result = json.loads(last["result"] or "{}")
        out["last_run"] = {"at": last["finished_at"] or last["started_at"], "state": last["state"],
                           "project": last["project"], "trigger": last["trigger"],
                           "new_ideas": len(result.get("new_ideas", [])), "attached": len(result.get("attached", [])),
                           "error": last["error"]}
    return out


def tick(conn) -> dict | None:
    """From the worker loop when planning isn't online. Never raises."""
    try:
        return run_due(conn)
    except Exception:
        log.exception("Ideas from new footage failed (will try again)")
        return None


# ── The planner's "ideas" job ─────────────────────────────────────────────────

def run_job(api, conn, project_ref, client=None) -> dict:
    """Ideas for one project from its footage of the last JOB_DAYS days: the clips no
    run has read yet, or (when there are none) all of them. Returns the job result
    {new_ideas, attached, clips, idea_ids}."""
    if not project_ref:
        raise RuntimeError("the job doesn't say which project")
    store = _store(conn, api)
    project = store.project(project_ref)
    if project is None:
        raise RuntimeError("that project isn't in the planner any more")
    cutoff = (datetime.now(timezone.utc) - timedelta(days=JOB_DAYS)).isoformat(timespec="seconds")
    local = store.local_projects(project_ref)
    where = f"project_id IN ({','.join('?' * len(local))})" if local else "0"
    if _is_everyday(project):
        where = f"({where} OR project_id IS NULL OR project_id NOT IN (SELECT id FROM projects WHERE remote_id IS NOT NULL))"
    recent = conn.execute(
        f"""SELECT * FROM clips WHERE status = 'tagged' AND COALESCE(usable, 1) = 1 AND cloud_hash IS NOT NULL
            AND ingested_at >= ? AND {where} ORDER BY ingested_at, id""", [cutoff, *local]).fetchall()
    if not recent:
        raise RuntimeError(f"No usable footage from the last {JOB_DAYS} days in this project yet")
    fresh = [c for c in recent if c["ideas_run"] is None]
    clips = _budgeted(fresh or list(reversed(recent)))
    _, result, error = _run_project(conn, store, project_ref, project, clips, "job", client)
    if error:
        raise RuntimeError(error)
    attached = len({a["clip_id"] for a in result["attached"]})
    if result["new_ideas"] or attached:
        import notify
        notify.digest.add("ideas_new", len(result["new_ideas"]))
        notify.digest.add("ideas_attached", attached)
        notify.digest.note("ideas_when", "the footage")
    return {"new_ideas": len(result["new_ideas"]), "attached": attached, "clips": len(clips),
            "idea_ids": result["new_ideas"], "notes": result["notes"]}


# ── One home for ideas: library ideas move to the planner (once) ─────────────

def migrate_library(api, conn) -> int:
    """Library ideas still waiting in Review ("Suggest posts", status new) move to the
    planner: into the project of their first clip (or Everyday posts), arc "From your
    footage", their segments as shots with takes and their wishlist as shots to film.
    Each is then marked as the planner's here (origin plan + remote_id), so it moves
    once. Returns how many moved."""
    import time
    import cloud_sync
    rows = conn.execute("""SELECT * FROM ideas WHERE COALESCE(origin, 'library') = 'library' AND status = 'new'
                           AND remote_id IS NULL ORDER BY id""").fetchall()
    rows = [r for r in rows if time.time() - _last_library_failure.get(r["id"], 0) > LIBRARY_RETRY_SECONDS]
    if not rows:
        return 0
    store = _Cloud(api, conn)
    moved = 0
    for idea in rows:
        segments = json.loads(idea["segments"] or "[]")
        wishlist = json.loads(idea["wishlist"] or "[]")
        first = conn.execute("SELECT project_id FROM clips WHERE id = ?", (segments[0]["clip_id"],)).fetchone() \
            if segments else None
        ref = store.ref_of(first["project_id"]) if first else None
        if ref is None or store.project(ref) is None:
            ref = store.everyday()["id"]
        remote_id = None
        try:
            arc_id = store.arc(ref)
            remote_id = store.add_idea(ref, arc_id, {
                "title": (idea["title"] or "Untitled idea")[:300], "brand": settings.SINGLE_BRAND or (
                    idea["brand"] if idea["brand"] in settings.BRANDS else "urban_makers"),
                "format": idea["format"] if idea["format"] in ("talking_head", "day_in_the_life", "compilation")
                else "talking_head",
                "hook": idea["hook"] or "", "purpose": idea["purpose"] or idea["angle"] or "", "cta": idea["cta"] or "",
                "caption": idea["caption"] or "", "est_seconds": idea["est_seconds"] or 30})
            shots = ([{"description": (s.get("purpose") or "Footage")[:1000]} for s in segments]
                     + [{"description": (w.get("shot") or "Shot to film")[:1000], "why": (w.get("why") or "")[:500]}
                        for w in wishlist])
            shot_ids = store.add_shots(remote_id, shots)
            store.add_takes([(sid, s["clip_id"], s.get("start"), s.get("end"))
                             for sid, s in zip(shot_ids, segments) if store.clip_ready(s["clip_id"])], note=LIBRARY_NOTE)
        except Exception as e:
            _last_library_failure[idea["id"]] = time.time()
            if remote_id is not None:  # don't leave half an idea behind
                _forget(store, remote_id)
            if isinstance(e, cloud_sync.CloudError) and "can't reach" in str(e):
                raise
            log.warning("Couldn't move library idea %s (%s) to the planner: %s", idea["id"], idea["title"], e)
            continue
        local_project = conn.execute("SELECT id FROM projects WHERE remote_id = ?", (ref,)).fetchone()
        conn.execute("""UPDATE ideas SET origin = 'plan', remote_id = ?, project_id = ?, arc_id = NULL,
                            purpose = COALESCE(NULLIF(purpose, ''), angle), decision = 'proposed', updated_at = ?
                        WHERE id = ?""",
                     (remote_id, local_project[0] if local_project else None, catalog.now(), idea["id"]))
        conn.commit()
        moved += 1
    if moved:
        done = cloud_sync._state().get("library_ideas_moved") or {}
        cloud_sync._save_state({"library_ideas_moved": {"count": (done.get("count") or 0) + moved,
                                                        "at": catalog.now()}})
        log.info("Moved %d library ideas from Review to the planner (\"%s\")", moved, ARC_TITLE)
    return moved
