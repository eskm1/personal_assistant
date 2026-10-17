"""
The planning board: content planned before it's filmed.

  project (a job, e.g. "Kensington")
    └ arcs (content series within it, e.g. "Waterproofing", "The client's story")
        └ ideas (one post each: hook, purpose, call to action, music, decision)
            └ shots (the storyboard: one frame per shot to film, in playback order)
                └ takes (clips from the library that film the shot)

The team throws ideas in (one per line, or a messy paragraph). Claude organises
them into arcs and storyboards using Bryan's playbook, so every idea arrives
with a hook, a structure, placeholder frames and a music direction. The team
votes; Bryan marks ideas Go or Park. On site, the shot list is ticked off; when
the footage lands, the tagger matches clips to shots (classify.py shot_matches),
and each quality-checked take shows whether the shot is done or needs a retake.
When an idea's shots have footage, "Make this" turns the takes into the
segments the editor cuts from.
"""
import json
import logging

import anthropic

import catalog
import playbook
import settings
from settings import ANTHROPIC_API_KEY, CLAUDE_MODEL

log = logging.getLogger("studio.plan")

SHOT_KINDS = ["talking", "broll", "detail", "before_after", "reaction", "screen"]
FRAMINGS = ["wide", "medium", "close", "detail", "pov", "overhead"]
# The team's kit and what each camera is for, so footage from three cameras cuts together.
CAMERAS = {
    "iphone": "iPhone 16 Pro: talking to camera, walk-and-talks, anything that must be vertical",
    "zfc": "Nikon Z fc: detail and beauty B-roll (materials, finishes, reveals), 4K horizontal, subject centred",
    "gopro": "GoPro: POV and hands-on work, tight spaces, timelapses; never for talking",
    "any": "whichever camera is at hand",
}
MAX_SHOTS_IN_PROMPT = 150

_SHOT = {
    "type": "object",
    "properties": {
        "keep_id": {"type": "integer"},
        "kind": {"type": "string", "enum": SHOT_KINDS},
        "framing": {"type": "string", "enum": FRAMINGS},
        "camera": {"type": "string", "enum": list(CAMERAS)},
        "description": {"type": "string"},
        "line": {"type": "string"},
        "on_screen": {"type": "string"},
        "seconds": {"type": "number"},
        "why": {"type": "string"},
    },
    "required": ["keep_id", "kind", "framing", "camera", "description", "line", "on_screen", "seconds", "why"],
    "additionalProperties": False,
}

_IDEA = {
    "type": "object",
    "properties": {
        "arc": {"type": "string"},
        "title": {"type": "string"},
        "brand": {"type": "string", "enum": list(settings.BRANDS)},
        "format": {"type": "string", "enum": ["talking_head", "day_in_the_life", "compilation"]},
        "hook": {"type": "string"},
        "purpose": {"type": "string"},
        "cta": {"type": "string"},
        "caption": {"type": "string"},
        "est_seconds": {"type": "number"},
        "music": {
            "type": "object",
            "properties": {"mood": {"type": "string"}, "tempo_bpm": {"type": "number"},
                           "search": {"type": "string"}},
            "required": ["mood", "tempo_bpm", "search"],
            "additionalProperties": False,
        },
        "shots": {"type": "array", "items": _SHOT},
    },
    "required": ["arc", "title", "brand", "format", "hook", "purpose", "cta", "caption", "est_seconds",
                 "music", "shots"],
    "additionalProperties": False,
}

ORGANISE_SCHEMA = {
    "type": "object",
    "properties": {
        "arcs": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"key": {"type": "string"}, "title": {"type": "string"},
                               "purpose": {"type": "string"}},
                "required": ["key", "title", "purpose"],
                "additionalProperties": False,
            },
        },
        "ideas": {"type": "array", "items": _IDEA},
        "notes": {"type": "string"},
    },
    "required": ["arcs", "ideas", "notes"],
    "additionalProperties": False,
}

DEVELOP_SCHEMA = {
    "type": "object",
    "properties": {"idea": _IDEA, "changes": {"type": "string"}},
    "required": ["idea", "changes"],
    "additionalProperties": False,
}

_RULES = """How to plan a post:
- One clear point per post, 15 to 60 seconds. The hook (first 3 seconds) states the tension or \
payoff; spoken hooks should sound like Bryan talking, not an ad.
- purpose: who it's for and what they should think, feel or do after watching (one or two sentences).
- cta: the call to action at the end ("Save this for your reno", "DM us KENSINGTON", ...).
- caption: a ready-to-edit Instagram caption in Bryan's voice with 3 to 6 hashtags.
- shots: the storyboard, one entry per shot, in playback order, each something the team can \
actually film on site. kind: talking (to camera), broll (work happening), detail (close-up of a \
material, fixing, finish), before_after, reaction, screen (a drawing, render or phone screen). \
framing: wide / medium / close / detail / pov / overhead. camera: which of the team's cameras \
films it ({cameras}). description: what the camera sees, \
specific enough to film without asking ("Close-up: trowel spreading grey membrane into the \
wall-floor corner, slow pan left"). line: what's said over it ("" if nothing). on_screen: text on \
screen ("" if none). seconds: rough length in the final cut. why: what this shot does for the story. \
Plan B-roll and detail shots to cover talking, as the playbook says. Usually 4 to 10 shots.
- music: the mood, tempo and a short search phrase for the music bed. For personal posts this \
becomes a note on what to pick from Instagram's library.
- Never invent facts, prices, timelines or client details. Write [placeholder] where the team \
must fill something in.
- keep_id: 0 for a new shot."""

_ORGANISE_SYSTEM = """You are the content producer for Bryan's Instagram accounts. The team is \
planning posts before filming. Turn their ideas into organised, filmable plans.

{brands}

Bryan's playbook (his editing and story rules; plan shots and structure that let the editor follow it):

<playbook>
{playbook}
</playbook>

""" + _RULES + """

How to organise:
- Group ideas into arcs: content series a viewer would follow (a topic, a phase of the job, a \
recurring format). Reuse an existing arc by giving its key exactly ("arc:12"); make a new one \
with a short key of your own ("new:waterproofing") and list it in arcs. Don't list existing arcs.
- Keep every idea the team wrote (merge only true duplicates), sharpen it, and add a few strong \
ones the project obviously supports if the team asked for more. Don't repeat existing ideas.
- notes: anything the team should know (merged duplicates, ideas that need a fact checked, gaps \
in the series)."""

_DEVELOP_SYSTEM = """You are the content producer for Bryan's Instagram accounts, refining one \
planned post with the team before it's filmed.

{brands}

Bryan's playbook:

<playbook>
{playbook}
</playbook>

""" + _RULES + """

Refining:
- Apply the team's request. If there's none, make the plan complete and filmable: a sharp hook, \
a clear structure, B-roll covering the talking, a strong end.
- Shots that are already filmed or have footage are marked; keep those (keep_id = their id) \
unless the request says otherwise, and plan around them. Reusing an unfilmed shot keeps its \
comments and history, so give its id in keep_id too; new shots get keep_id 0.
- arc: return the idea's current arc key unchanged.
- changes: one or two sentences on what you changed, for the team."""


def _brands() -> str:
    return settings.brands_text()


def _ask(system: str, user: str, schema: dict, ref: str, client=None) -> dict:
    client = client or anthropic.Anthropic(api_key=ANTHROPIC_API_KEY or None)
    import llm
    response = llm.final_message(
        client, model=CLAUDE_MODEL, max_tokens=64000,
        betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        output_config={"effort": "high", "format": {"type": "json_schema", "schema": schema}},
        system=system.replace("{brands}", _brands()).replace("{playbook}", playbook.text())
                     .replace("{cameras}", "; ".join(f"{k} = {v}" for k, v in CAMERAS.items())),
        messages=[{"role": "user", "content": user}],
    )
    import usage
    usage.record("plan", ref, response)
    if response.stop_reason == "refusal":
        raise RuntimeError("Claude declined this request")
    if response.stop_reason == "max_tokens":
        raise RuntimeError("Claude's answer was cut off; try fewer ideas at once")
    return json.loads(next(b.text for b in response.content if b.type == "text"))


# ── Reading the board ─────────────────────────────────────────────────────────

def shot_state(shot: dict, takes: list[dict]) -> str:
    """needed | filmed (ticked, footage not in yet) | got (a usable take) | retake."""
    if any(t.get("verdict") != "retake" for t in takes):
        return "got"
    if takes:
        return "retake"
    return "filmed" if shot.get("filmed_at") else "needed"


def takes_for(conn, shot_ids: list[int]) -> dict[int, list[dict]]:
    if not shot_ids:
        return {}
    rows = conn.execute(
        f"""SELECT t.*, c.title AS clip_title, c.source_name, c.quality, c.duration AS clip_duration,
                   c.clip_dir, c.status AS clip_status
            FROM takes t JOIN clips c ON c.id = t.clip_id
            WHERE t.shot_id IN ({','.join('?' * len(shot_ids))}) ORDER BY t.id""", shot_ids).fetchall()
    out: dict[int, list[dict]] = {}
    for r in rows:
        q = json.loads(r["quality"] or "{}")
        out.setdefault(r["shot_id"], []).append({
            "id": r["id"], "clip_id": r["clip_id"], "start": r["start"], "end": r["end"], "source": r["source"],
            "note": r["note"], "title": r["clip_title"] or r["source_name"], "verdict": q.get("verdict"),
            "flags": [f["text"] for f in q.get("flags", [])]})
    return out


def shots_for(conn, idea_ids: list[int]) -> dict[int, list[dict]]:
    if not idea_ids:
        return {}
    rows = [dict(r) for r in conn.execute(
        f"SELECT * FROM shots WHERE idea_id IN ({','.join('?' * len(idea_ids))}) ORDER BY position, id",
        idea_ids)]
    takes = takes_for(conn, [r["id"] for r in rows])
    out: dict[int, list[dict]] = {}
    for r in rows:
        r["takes"] = takes.get(r["id"], [])
        r["state"] = shot_state(r, r["takes"])
        out.setdefault(r["idea_id"], []).append(r)
    return out


def progress(shots: list[dict]) -> dict:
    counts = {"needed": 0, "filmed": 0, "got": 0, "retake": 0}
    for s in shots:
        counts[s["state"]] += 1
    counts["total"] = len(shots)
    return counts


def project_for_folder(conn, folder: str) -> int | None:
    row = conn.execute(
        """SELECT id FROM projects WHERE status = 'active'
           AND (LOWER(COALESCE(folder, '')) = LOWER(?) OR LOWER(name) = LOWER(?)) ORDER BY id DESC LIMIT 1""",
        (folder, folder)).fetchone()
    return row["id"] if row else None


def open_shots_text(conn, project_id: int | None = None) -> tuple[str, set[int]]:
    """The shot list the tagger matches new clips against: shots without a usable
    take, in ideas still being planned, in active projects (just the clip's
    project when its folder says which)."""
    params: list = []
    where = "p.status = 'active' AND i.status = 'new' AND COALESCE(i.decision, 'proposed') != 'parked'"
    if project_id:
        where += " AND p.id = ?"
        params.append(project_id)
    rows = conn.execute(
        f"""SELECT s.*, i.title AS idea_title, a.title AS arc_title, p.name AS project_name
            FROM shots s JOIN ideas i ON i.id = s.idea_id JOIN projects p ON p.id = i.project_id
            LEFT JOIN arcs a ON a.id = i.arc_id
            WHERE {where} ORDER BY p.id DESC, i.decision = 'go' DESC, s.id""", params).fetchall()
    takes = takes_for(conn, [r["id"] for r in rows])
    lines, ids = [], set()
    for r in rows:
        if shot_state(dict(r), takes.get(r["id"], [])) == "got":
            continue
        if len(ids) >= MAX_SHOTS_IN_PROMPT:
            break
        ids.add(r["id"])
        how = ", ".join(filter(None, (r["framing"], r["kind"], r["camera"])))
        lines.append(f"Shot {r['id']} [{r['project_name']} > {r['arc_title'] or 'Unsorted'} > {r['idea_title']}]: "
                     + (f"({how}) " if how else "") + r["description"]
                     + (f" Says: \"{r['line']}\"" if r["line"] else ""))
    return "\n".join(lines), ids


def save_matches(conn, clip_id: int, matches: list[dict], allowed: set[int], duration: float) -> int:
    """Store the tagger's clip-to-shot matches as takes. Returns how many shots got one."""
    n = 0
    for m in matches:
        if m["shot_id"] not in allowed:
            continue
        start, end = max(0.0, m["start"]), min(duration or m["end"], m["end"])
        if end - start < 0.3:
            start, end = 0.0, duration
        cur = conn.execute(
            """INSERT OR IGNORE INTO takes (shot_id, clip_id, start, end, source, note, created_at)
               VALUES (?, ?, ?, ?, 'auto', ?, ?)""",
            (m["shot_id"], clip_id, round(start, 2), round(end, 2), m.get("note", "")[:500], catalog.now()))
        n += cur.rowcount
    conn.commit()
    return n


# ── Writing the board ─────────────────────────────────────────────────────────

def _next_position(conn, table: str, where: str, params: tuple) -> float:
    row = conn.execute(f"SELECT MAX(position) FROM {table} WHERE {where}", params).fetchone()
    return (row[0] or 0) + 1


def add_arc(conn, project_id: int, title: str, purpose: str = "") -> int:
    cur = conn.execute("INSERT INTO arcs (project_id, title, purpose, position, created_at) VALUES (?, ?, ?, ?, ?)",
                       (project_id, title.strip()[:200], purpose.strip()[:1000],
                        _next_position(conn, "arcs", "project_id = ?", (project_id,)), catalog.now()))
    conn.commit()
    return cur.lastrowid


def add_idea(conn, project_id: int, arc_id: int | None, fields: dict, user_id: int | None = None) -> int:
    project = conn.execute("SELECT brand FROM projects WHERE id = ?", (project_id,)).fetchone()
    stamp = catalog.now()
    music_plan = fields.get("music_plan") or (
        {k: fields["music"][k] for k in ("mood", "tempo_bpm", "search")} if fields.get("music") else {})
    cur = conn.execute(
        """INSERT INTO ideas (batch, status, origin, project_id, arc_id, brand, format, title, hook, angle,
               purpose, cta, beats, segments, caption, est_seconds, wishlist, music_plan, decision, position,
               created_by, created_at, updated_at)
           VALUES ('plan', 'new', 'plan', ?, ?, ?, ?, ?, ?, '', ?, ?, '[]', '[]', ?, ?, '[]', ?, 'proposed', ?, ?, ?, ?)""",
        (project_id, arc_id, fields.get("brand") or (project["brand"] if project else None) or "urban_makers",
         fields.get("format") or "talking_head", (fields.get("title") or "Untitled idea").strip()[:300],
         fields.get("hook", ""), fields.get("purpose", ""), fields.get("cta", ""), fields.get("caption", ""),
         fields.get("est_seconds") or 30, json.dumps(music_plan),
         _next_position(conn, "ideas", "COALESCE(arc_id, 0) = ? AND project_id = ?", (arc_id or 0, project_id)),
         user_id, stamp, stamp))
    idea_id = cur.lastrowid
    for s in fields.get("shots", []):
        add_shot(conn, idea_id, s, commit=False)
    conn.commit()
    return idea_id


def add_shot(conn, idea_id: int, s: dict, commit: bool = True) -> int:
    cur = conn.execute(
        """INSERT INTO shots (idea_id, position, kind, framing, camera, description, line, on_screen, seconds, why,
               created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (idea_id, _next_position(conn, "shots", "idea_id = ?", (idea_id,)),
         s.get("kind") if s.get("kind") in SHOT_KINDS else None,
         s.get("framing") if s.get("framing") in FRAMINGS else None,
         s.get("camera") if s.get("camera") in CAMERAS else None,
         (s.get("description") or "").strip()[:1000] or "New shot", (s.get("line") or "")[:1000],
         (s.get("on_screen") or "")[:300], s.get("seconds"), (s.get("why") or "")[:500], catalog.now()))
    if commit:
        conn.commit()
    return cur.lastrowid


def quick_add(conn, project_id: int, arc_id: int | None, text: str, user_id: int | None = None) -> int:
    """One idea per non-empty line, no AI. Returns how many."""
    n = 0
    for line in text.splitlines():
        title = line.strip().lstrip("-*•0123456789.) ").strip()
        if title:
            add_idea(conn, project_id, arc_id, {"title": title}, user_id)
            n += 1
    return n


def _board_text(conn, project_id: int) -> str:
    arcs = conn.execute("SELECT * FROM arcs WHERE project_id = ? ORDER BY position", (project_id,)).fetchall()
    lines = []
    for a in arcs:
        titles = [r["title"] for r in conn.execute(
            "SELECT title FROM ideas WHERE arc_id = ? AND origin = 'plan' AND status != 'dismissed'", (a["id"],))]
        lines.append(f"- arc:{a['id']} \"{a['title']}\" ({a['purpose'] or 'no purpose yet'}); ideas: "
                     + ("; ".join(titles) or "none"))
    unsorted = [r["title"] for r in conn.execute(
        """SELECT title FROM ideas WHERE project_id = ? AND arc_id IS NULL AND origin = 'plan'
           AND status != 'dismissed'""", (project_id,))]
    if unsorted:
        lines.append("- (no arc yet): " + "; ".join(unsorted))
    return "\n".join(lines) or "(empty so far)"


def organise(conn, project_id: int, dump: str, user_id: int | None = None, client=None) -> dict:
    """Claude turns a brain-dump into arcs and storyboarded ideas on the board."""
    project = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    if project is None:
        raise KeyError(project_id)
    user = (f"Project: {project['name']} (mostly for {project['brand'] or 'urban_makers'})\n"
            f"Brief: {project['brief'] or '(none yet)'}\n\nThe board so far:\n{_board_text(conn, project_id)}\n\n"
            f"The team's new ideas:\n<ideas>\n{dump.strip()}\n</ideas>")
    result = _ask(_ORGANISE_SYSTEM, user, ORGANISE_SCHEMA, f"project:{project_id}", client)
    existing = {f"arc:{r['id']}": r["id"] for r in conn.execute("SELECT id FROM arcs WHERE project_id = ?",
                                                                  (project_id,))}
    keys = dict(existing)
    for a in result["arcs"]:
        if a["key"] not in keys:
            keys[a["key"]] = add_arc(conn, project_id, a["title"], a["purpose"])
    for idea in result["ideas"]:
        add_idea(conn, project_id, keys.get(idea["arc"]), idea, user_id)
    log.info("Organised %d ideas into project %s", len(result["ideas"]), project_id)
    return {"ideas": len(result["ideas"]), "arcs": len(keys) - len(existing), "notes": result["notes"]}


def develop(conn, idea_id: int, request: str = "", client=None) -> str:
    """Claude refines one idea's plan and storyboard; filmed shots are kept."""
    idea = dict(conn.execute("SELECT * FROM ideas WHERE id = ?", (idea_id,)).fetchone())
    shots = shots_for(conn, [idea_id]).get(idea_id, [])
    project = conn.execute("SELECT * FROM projects WHERE id = ?", (idea["project_id"],)).fetchone()
    comments = conn.execute("""SELECT author, body FROM feedback WHERE target = 'idea' AND target_id = ?
                               ORDER BY id DESC LIMIT 20""", (idea_id,)).fetchall()
    music_plan = json.loads(idea["music_plan"] or "{}")
    shot_lines = [f"  id {s['id']} [{s['state']}] {s['camera'] or ''} {s['framing'] or ''} {s['kind'] or ''}: "
                  f"{s['description']}"
                  + (f" | says: {s['line']}" if s["line"] else "") + (f" | on screen: {s['on_screen']}" if s["on_screen"] else "")
                  for s in shots]
    user = (f"Project: {project['name'] if project else '-'}. Brief: {project['brief'] if project else '-'}\n\n"
            f"The idea now (arc key: arc:{idea['arc_id'] or 0}):\n"
            f"  title: {idea['title']}\n  account: {idea['brand']}, format: {idea['format']}, ~{idea['est_seconds'] or 30:.0f}s\n"
            f"  hook: {idea['hook'] or '-'}\n  purpose: {idea['purpose'] or '-'}\n  cta: {idea['cta'] or '-'}\n"
            f"  caption: {idea['caption'] or '-'}\n  music: {music_plan.get('mood', '-')}\n"
            "  shots:\n" + ("\n".join(shot_lines) or "  (none yet)") + "\n\n"
            + ("Team comments (newest first):\n" + "\n".join(f"  {c['author'] or 'someone'}: {c['body']}" for c in comments)
               + "\n\n" if comments else "")
            + f"The team's request: {request.strip() or '(none: make the plan complete and filmable)'}")
    result = _ask(_DEVELOP_SYSTEM, user, DEVELOP_SCHEMA, f"idea:{idea_id}", client)
    new = result["idea"]
    music_plan.update({k: new["music"][k] for k in ("mood", "tempo_bpm", "search")})
    conn.execute("""UPDATE ideas SET title = ?, brand = ?, format = ?, hook = ?, purpose = ?, cta = ?, caption = ?,
                        est_seconds = ?, music_plan = ?, updated_at = ? WHERE id = ?""",
                 (new["title"], new["brand"], new["format"], new["hook"], new["purpose"], new["cta"],
                  new["caption"], new["est_seconds"], json.dumps(music_plan), catalog.now(), idea_id))
    by_id = {s["id"]: s for s in shots}
    kept, position = set(), 0
    for s in new["shots"]:
        position += 1
        if s["keep_id"] in by_id and s["keep_id"] not in kept:
            kept.add(s["keep_id"])
            conn.execute("""UPDATE shots SET position = ?, kind = ?, framing = ?, camera = ?, description = ?,
                                line = ?, on_screen = ?, seconds = ?, why = ? WHERE id = ?""",
                         (position, s["kind"], s["framing"], s["camera"], s["description"], s["line"],
                          s["on_screen"], s["seconds"], s["why"], s["keep_id"]))
        else:
            sid = add_shot(conn, idea_id, s, commit=False)
            conn.execute("UPDATE shots SET position = ? WHERE id = ?", (position, sid))
    for s in shots:  # never lose a shot that's been filmed or has footage
        if s["id"] in kept:
            continue
        if s["state"] != "needed":
            position += 1
            conn.execute("UPDATE shots SET position = ? WHERE id = ?", (position, s["id"]))
        else:
            conn.execute("DELETE FROM shots WHERE id = ?", (s["id"],))
    conn.commit()
    return result["changes"]


def shortlist_music(conn, idea_id: int, client=None) -> dict:
    """Candidate tracks for an idea: Epidemic Sound for accounts licensed for it,
    otherwise a brief for picking from Instagram's own library."""
    import music
    import settings
    idea = conn.execute("SELECT * FROM ideas WHERE id = ?", (idea_id,)).fetchone()
    plan_ = json.loads(idea["music_plan"] or "{}")
    mood = plan_.get("mood") or "upbeat, warm, works under speech"
    if music.allowed_for(idea["brand"]) and settings.EPIDEMIC_SOUND_API_KEY:
        brief = (f"{mood}; search: {plan_.get('search') or mood}; about {plan_.get('tempo_bpm') or 100:.0f} BPM. "
                 f"Post: {idea['title']}. {idea['purpose'] or ''}")
        found = music.shortlist(brief, idea["est_seconds"] or 30, client=client, ref=f"idea:{idea_id}")
        plan_.update(source="epidemic", shortlist=found["tracks"], note=found["reason"] if not found["tracks"] else "")
    else:
        plan_.update(source="instagram", shortlist=[],
                     note=f"Pick in the Instagram app when posting: {mood}, around "
                          f"{plan_.get('tempo_bpm') or 100:.0f} BPM. Search ideas: {plan_.get('search') or mood}.")
    conn.execute("UPDATE ideas SET music_plan = ?, updated_at = ? WHERE id = ?",
                 (json.dumps(plan_), catalog.now(), idea_id))
    conn.commit()
    return plan_


def choose_music(conn, idea_id: int, recording_id: str | None) -> None:
    idea = conn.execute("SELECT music_plan FROM ideas WHERE id = ?", (idea_id,)).fetchone()
    plan_ = json.loads(idea["music_plan"] or "{}")
    pick = next((t for t in plan_.get("shortlist", []) if t["recording_id"] == recording_id), None)
    plan_["chosen"] = pick
    conn.execute("UPDATE ideas SET music_plan = ?, updated_at = ? WHERE id = ?",
                 (json.dumps(plan_), catalog.now(), idea_id))
    conn.commit()


def adopt(conn, idea_id: int, project_id: int, arc_id: int | None) -> None:
    """Move a library idea onto the planning board: its footage segments become
    shots that already have takes, its wishlist becomes shots still to film."""
    idea = conn.execute("SELECT * FROM ideas WHERE id = ?", (idea_id,)).fetchone()
    if idea["origin"] == "plan":
        conn.execute("UPDATE ideas SET project_id = ?, arc_id = ? WHERE id = ?", (project_id, arc_id, idea_id))
        conn.commit()
        return
    for seg in json.loads(idea["segments"] or "[]"):
        sid = add_shot(conn, idea_id, {"kind": "talking", "description": seg["purpose"]}, commit=False)
        conn.execute("""INSERT OR IGNORE INTO takes (shot_id, clip_id, start, end, source, note, created_at)
                        VALUES (?, ?, ?, ?, 'manual', '', ?)""",
                     (sid, seg["clip_id"], seg["start"], seg["end"], catalog.now()))
    for wish in json.loads(idea["wishlist"] or "[]"):
        add_shot(conn, idea_id, {"description": wish["shot"], "why": wish["why"]}, commit=False)
    conn.execute("""UPDATE ideas SET origin = 'plan', project_id = ?, arc_id = ?, purpose = COALESCE(purpose, angle),
                        decision = 'proposed', updated_at = ? WHERE id = ?""",
                 (project_id, arc_id, catalog.now(), idea_id))
    conn.commit()


def segments_for(conn, idea_id: int) -> list[dict]:
    """The editor's segments: each shot's best take, in storyboard order."""
    out = []
    for s in shots_for(conn, [idea_id]).get(idea_id, []):
        usable = [t for t in s["takes"] if t["verdict"] != "retake"] or s["takes"]
        if not usable:
            continue
        # A take the team attached by hand beats an automatic match.
        best = sorted(usable, key=lambda t: (t["source"] != "manual", -t["id"]))[0]
        end = best["end"] if best["end"] is not None else 0
        out.append({"clip_id": best["clip_id"], "start": best["start"] or 0, "end": end,
                    "purpose": s["description"] + (f" (says: {s['line']})" if s["line"] else "")})
    return out


def make(conn, idea_id: int, caption: str | None = None) -> int:
    """Send a planned idea to the editor, cut from its shots' takes."""
    segments = segments_for(conn, idea_id)
    if not segments:
        raise RuntimeError("none of this idea's shots have footage yet")
    shots = shots_for(conn, [idea_id]).get(idea_id, [])
    durations = {r["id"]: r["duration"] or 0 for r in conn.execute(
        f"SELECT id, duration FROM clips WHERE id IN ({','.join('?' * len(segments))})",
        [s["clip_id"] for s in segments])}
    for s in segments:  # whole-clip takes have no end yet
        if not s["end"]:
            s["end"] = durations.get(s["clip_id"], 0)
    conn.execute("UPDATE ideas SET segments = ?, beats = ? WHERE id = ?",
                 (json.dumps(segments), json.dumps([s["description"] for s in shots]), idea_id))
    conn.commit()
    production_id = catalog.make_production(conn, idea_id, caption)
    row = conn.execute("SELECT plan FROM productions WHERE id = ?", (production_id,)).fetchone()
    plan_ = json.loads(row["plan"])
    plan_["storyboard"] = [{"description": s["description"], "line": s["line"], "on_screen": s["on_screen"]}
                           for s in shots]
    conn.execute("UPDATE productions SET plan = ? WHERE id = ?", (json.dumps(plan_), production_id))
    conn.commit()
    return production_id
