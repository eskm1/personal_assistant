"""
The planning board's API (see plan.py for the model). Everyone signed in can
plan: add and organise ideas, refine storyboards with Claude, shortlist music,
vote, comment and tick off shots. Bryan's call (Go / Park) and "Make this"
(which spends on an edit) are his alone.

Claude jobs (organise, refine, music) run in the background; their state comes
back with the board or idea they belong to.
"""
import json
import logging
import re
import threading
from pathlib import Path

from flask import Blueprint, abort, g, jsonify, request, send_file

import catalog
import plan
from webutil import body, db, is_owner, owner_only, text

log = logging.getLogger("studio.plan")
bp = Blueprint("plan", __name__)

BRANDS = ("personal", "urban_makers")
FORMATS = ("talking_head", "day_in_the_life", "compilation")
DECISIONS = ("proposed", "go", "parked")

# ── Background jobs ───────────────────────────────────────────────────────────

_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()


def _job(key: str) -> dict | None:
    return dict(_jobs[key]) if key in _jobs else None


def _start(key: str, fn, *args) -> tuple[dict, int] | None:
    """Run fn(conn, *args) on a thread unless that job is already running."""
    with _jobs_lock:
        if _jobs.get(key, {}).get("state") == "running":
            return {"error": "already working on this"}, 409
        _jobs[key] = {"state": "running", "error": None, "result": None, "started": catalog.now()}

    def run():
        conn = catalog.connect()
        try:
            result = fn(conn, *args)
            _jobs[key].update(state="done", result=result, finished=catalog.now())
        except Exception as e:  # shown on the board; details in worker.log
            log.exception("Planning job %s failed", key)
            _jobs[key].update(state="failed", error=f"{type(e).__name__}: {e}", finished=catalog.now())
        finally:
            conn.close()
    threading.Thread(target=run, name=key, daemon=True).start()
    return None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _row(table: str, row_id: int):
    row = db().execute(f"SELECT * FROM {table} WHERE id = ?", (row_id,)).fetchone()
    if row is None:
        abort(404)
    return row


def _plan_idea(idea_id: int):
    row = _row("ideas", idea_id)
    if row["origin"] != "plan":
        abort(404)
    return row


def _touch(idea_id: int) -> None:
    db().execute("UPDATE ideas SET updated_at = ? WHERE id = ?", (catalog.now(), idea_id))


def _names() -> dict[int, str]:
    return {r["id"]: r["name"] for r in db().execute("SELECT id, name FROM users")}


def _take_json(t: dict) -> dict:
    return {**t, "thumb": f"/media/take/{t['id']}/thumb", "video": f"/media/clip/{t['clip_id']}/video"}


def _shot_json(s: dict, names: dict[int, str]) -> dict:
    return {k: s[k] for k in ("id", "idea_id", "position", "kind", "framing", "camera", "description", "line",
                              "on_screen", "seconds", "why", "filmed_at", "state")} | {
        "filmed_by": names.get(s["filmed_by"]), "takes": [_take_json(t) for t in s["takes"]]}


def _votes(idea_ids: list[int]) -> dict[int, dict]:
    out: dict[int, dict] = {i: {"up": [], "down": [], "mine": 0} for i in idea_ids}
    if not idea_ids:
        return out
    names = _names()
    for r in db().execute(f"SELECT * FROM votes WHERE idea_id IN ({','.join('?' * len(idea_ids))})", idea_ids):
        v = out[r["idea_id"]]
        v["up" if r["vote"] > 0 else "down"].append(names.get(r["user_id"], "?"))
        if r["user_id"] == g.user["id"]:
            v["mine"] = r["vote"]
    return out


def _comment_counts(idea_ids: list[int]) -> dict[int, int]:
    if not idea_ids:
        return {}
    return {r["target_id"]: r["n"] for r in db().execute(
        f"""SELECT target_id, COUNT(*) AS n FROM feedback WHERE target = 'idea'
            AND target_id IN ({','.join('?' * len(idea_ids))}) GROUP BY target_id""", idea_ids)}


def _project_json(r) -> dict:
    return {k: r[k] for k in ("id", "name", "brand", "folder", "brief", "status", "created_at")}


# ── Projects ──────────────────────────────────────────────────────────────────

@bp.get("/api/projects")
def projects():
    out = []
    for r in db().execute("SELECT * FROM projects ORDER BY status = 'archived', id DESC"):
        ideas = [i["id"] for i in db().execute(
            "SELECT id FROM ideas WHERE project_id = ? AND origin = 'plan' AND status != 'dismissed'", (r["id"],))]
        shots = [s for lst in plan.shots_for(db(), ideas).values() for s in lst]
        out.append(_project_json(r) | {"ideas": len(ideas), "progress": plan.progress(shots)})
    return jsonify({"projects": out})


@bp.post("/api/projects")
def create_project():
    data = body()
    name = text(data, "name", 120)
    if not name:
        return jsonify({"error": "give the project a name"}), 400
    brand = data.get("brand") if data.get("brand") in BRANDS else "urban_makers"
    folder = re.sub(r'[<>:"/\\|?*]', "", text(data, "folder", 80) or name).strip()
    cur = db().execute(
        "INSERT INTO projects (name, brand, folder, brief, status, created_by, created_at) VALUES (?, ?, ?, ?, 'active', ?, ?)",
        (name, brand, folder, text(data, "brief", 4000), g.user["id"], catalog.now()))
    db().commit()
    _make_inbox_folder(folder)
    return jsonify({"id": cur.lastrowid})


def _make_inbox_folder(folder: str) -> None:
    """An inbox subfolder for the project, so uploads there get filed to it."""
    from settings import INBOX
    try:
        if folder and INBOX.exists():
            (INBOX / folder).mkdir(exist_ok=True)
    except OSError as e:
        log.warning("Couldn't make inbox folder %s: %s", folder, e)


@bp.post("/api/project/<int:project_id>")
def update_project(project_id: int):
    _row("projects", project_id)
    data, sets = body(), {}
    if "name" in data and text(data, "name", 120):
        sets["name"] = text(data, "name", 120)
    if data.get("brand") in BRANDS:
        sets["brand"] = data["brand"]
    if "brief" in data:
        sets["brief"] = text(data, "brief", 4000)
    if "folder" in data:
        sets["folder"] = re.sub(r'[<>:"/\\|?*]', "", text(data, "folder", 80)).strip()
        _make_inbox_folder(sets["folder"])
    if data.get("status") in ("active", "archived"):
        sets["status"] = data["status"]
    if sets:
        db().execute(f"UPDATE projects SET {', '.join(f'{k} = ?' for k in sets)} WHERE id = ?",
                     [*sets.values(), project_id])
        db().commit()
    return jsonify({"ok": True})


@bp.get("/api/plan/<int:project_id>")
def board(project_id: int):
    project = _row("projects", project_id)
    arcs = [dict(r) for r in db().execute("SELECT * FROM arcs WHERE project_id = ? ORDER BY position, id",
                                          (project_id,))]
    rows = [dict(r) for r in db().execute(
        """SELECT * FROM ideas WHERE project_id = ? AND origin = 'plan' AND status != 'dismissed'
           ORDER BY COALESCE(position, 0), id""", (project_id,))]
    ids = [r["id"] for r in rows]
    shots, votes, comments, names = plan.shots_for(db(), ids), _votes(ids), _comment_counts(ids), _names()
    ideas = []
    for r in rows:
        mine = shots.get(r["id"], [])
        first_take = next((t for s in mine for t in s["takes"]), None)
        music_plan = json.loads(r["music_plan"] or "{}")
        ideas.append({
            "id": r["id"], "arc_id": r["arc_id"], "title": r["title"], "brand": r["brand"], "format": r["format"],
            "hook": r["hook"], "decision": r["decision"] or "proposed", "status": r["status"],
            "production_id": r["production_id"], "est_seconds": r["est_seconds"], "position": r["position"],
            "progress": plan.progress(mine), "votes": votes[r["id"]], "comments": comments.get(r["id"], 0),
            "thumb": f"/media/take/{first_take['id']}/thumb" if first_take else None,
            "music": (music_plan.get("chosen") or {}).get("title") or music_plan.get("mood"),
            "by": names.get(r["created_by"]), "updated": r["updated_at"] or r["created_at"]})
    return jsonify({"project": _project_json(project), "arcs": arcs, "ideas": ideas,
                    "jobs": {"organise": _job(f"organise:{project_id}")}})


@bp.get("/api/project/<int:project_id>/checklist")
def checklist(project_id: int):
    """Every shot still in play, grouped by arc and idea, for ticking off on site."""
    project = _row("projects", project_id)
    arcs = {r["id"]: r["title"] for r in db().execute("SELECT id, title FROM arcs WHERE project_id = ?", (project_id,))}
    order = {r["id"]: i for i, r in enumerate(db().execute(
        "SELECT id FROM arcs WHERE project_id = ? ORDER BY position, id", (project_id,)))}
    rows = [dict(r) for r in db().execute(
        """SELECT * FROM ideas WHERE project_id = ? AND origin = 'plan' AND status = 'new'
           AND COALESCE(decision, 'proposed') != 'parked'
           ORDER BY decision = 'go' DESC, COALESCE(position, 0), id""", (project_id,))]
    shots, names = plan.shots_for(db(), [r["id"] for r in rows]), _names()
    groups = []
    for r in sorted(rows, key=lambda r: order.get(r["arc_id"], 1e9)):
        mine = shots.get(r["id"], [])
        if mine:
            groups.append({"arc": arcs.get(r["arc_id"], "Unsorted"), "idea_id": r["id"], "title": r["title"],
                           "decision": r["decision"] or "proposed", "progress": plan.progress(mine),
                           "shots": [_shot_json(s, names) for s in mine]})
    return jsonify({"project": _project_json(project), "groups": groups})


@bp.get("/api/project/<int:project_id>/clips")
def project_clips(project_id: int):
    """Clips to attach to a shot: this project's first, then everything recent."""
    q = f"%{request.args.get('q', '').strip()}%"
    rows = db().execute(
        """SELECT * FROM clips WHERE status IN ('tagged', 'transcribed')
           AND (title LIKE ? OR source_name LIKE ? OR COALESCE(summary, '') LIKE ?)
           ORDER BY project_id = ? DESC, COALESCE(recorded_at, ingested_at) DESC LIMIT 60""",
        (q, q, q, project_id)).fetchall()
    return jsonify({"clips": [{
        "id": r["id"], "title": r["title"] or r["source_name"], "duration": r["duration"],
        "recorded_at": r["recorded_at"], "in_project": r["project_id"] == project_id,
        "verdict": json.loads(r["quality"]).get("verdict") if r["quality"] else None,
        "thumb": f"/media/clip/{r['id']}/thumb", "video": f"/media/clip/{r['id']}/video"} for r in rows]})


@bp.post("/api/clip/<int:clip_id>/project")
def set_clip_project(clip_id: int):
    _row("clips", clip_id)
    pid = body().get("project_id")
    if pid is not None:
        _row("projects", int(pid))
    db().execute("UPDATE clips SET project_id = ? WHERE id = ?", (pid, clip_id))
    db().commit()
    return jsonify({"ok": True})


# ── Arcs ──────────────────────────────────────────────────────────────────────

@bp.post("/api/project/<int:project_id>/arcs")
def create_arc(project_id: int):
    _row("projects", project_id)
    title = text(body(), "title", 200)
    if not title:
        return jsonify({"error": "give the arc a name"}), 400
    return jsonify({"id": plan.add_arc(db(), project_id, title, text(body(), "purpose", 1000))})


@bp.post("/api/arc/<int:arc_id>")
def update_arc(arc_id: int):
    arc = _row("arcs", arc_id)
    data = body()
    if data.get("delete"):
        db().execute("UPDATE ideas SET arc_id = NULL WHERE arc_id = ?", (arc_id,))
        db().execute("DELETE FROM arcs WHERE id = ?", (arc_id,))
    else:
        if text(data, "title", 200):
            db().execute("UPDATE arcs SET title = ? WHERE id = ?", (text(data, "title", 200), arc_id))
        if "purpose" in data:
            db().execute("UPDATE arcs SET purpose = ? WHERE id = ?", (text(data, "purpose", 1000), arc_id))
        if data.get("move") in (-1, 1):
            arcs = [r["id"] for r in db().execute("SELECT id FROM arcs WHERE project_id = ? ORDER BY position, id",
                                                  (arc["project_id"],))]
            i = arcs.index(arc_id)
            j = i + data["move"]
            if 0 <= j < len(arcs):
                arcs[i], arcs[j] = arcs[j], arcs[i]
                for pos, aid in enumerate(arcs, 1):
                    db().execute("UPDATE arcs SET position = ? WHERE id = ?", (pos, aid))
    db().commit()
    return jsonify({"ok": True})


# ── Ideas ─────────────────────────────────────────────────────────────────────

@bp.post("/api/project/<int:project_id>/ideas")
def quick_add(project_id: int):
    _row("projects", project_id)
    data = body()
    arc_id = data.get("arc_id")
    if arc_id is not None:
        arc_id = int(arc_id)
        _row("arcs", arc_id)
    n = plan.quick_add(db(), project_id, arc_id, text(data, "text", 20000), g.user["id"])
    if not n:
        return jsonify({"error": "write at least one idea"}), 400
    return jsonify({"added": n})


@bp.post("/api/project/<int:project_id>/organise")
def organise(project_id: int):
    _row("projects", project_id)
    dump = text(body(), "text", 20000)
    if not dump:
        return jsonify({"error": "write or paste some ideas first"}), 400
    busy = _start(f"organise:{project_id}", plan.organise, project_id, dump, g.user["id"])
    return jsonify(busy[0]) if busy else jsonify({"ok": True}), busy[1] if busy else 200


@bp.get("/api/plan-idea/<int:idea_id>")
def idea_detail(idea_id: int):
    r = _plan_idea(idea_id)
    names = _names()
    shots = plan.shots_for(db(), [idea_id]).get(idea_id, [])
    arcs = [{"id": a["id"], "title": a["title"]} for a in db().execute(
        "SELECT id, title FROM arcs WHERE project_id = ? ORDER BY position, id", (r["project_id"],))]
    notes = [dict(n) for n in catalog.feedback_for(db(), "idea", idea_id)]
    first_take = next((t for s in shots for t in s["takes"]), None)
    return jsonify({
        "kind": "plan", "id": idea_id, "project_id": r["project_id"], "arc_id": r["arc_id"], "arcs": arcs,
        "title": r["title"], "brand": r["brand"], "format": r["format"], "hook": r["hook"] or "",
        "purpose": r["purpose"] or "", "cta": r["cta"] or "", "caption": r["caption"] or "",
        "est_seconds": r["est_seconds"], "decision": r["decision"] or "proposed", "status": r["status"],
        "production_id": r["production_id"], "music": json.loads(r["music_plan"] or "{}"),
        "shots": [_shot_json(s, names) for s in shots], "progress": plan.progress(shots),
        "votes": _votes([idea_id])[idea_id], "notes": notes, "by": names.get(r["created_by"]),
        "video": first_take and f"/media/clip/{first_take['clip_id']}/video#t={first_take['start'] or 0}",
        "thumb": first_take and f"/media/take/{first_take['id']}/thumb",
        "can_make": bool(plan.segments_for(db(), idea_id)) and r["status"] == "new",
        "jobs": {"develop": _job(f"develop:{idea_id}"), "music": _job(f"music:{idea_id}")},
        "updated": r["updated_at"] or r["created_at"]})


@bp.post("/api/plan-idea/<int:idea_id>")
def update_idea(idea_id: int):
    r = _plan_idea(idea_id)
    data, sets = body(), {}
    for key, limit in (("title", 300), ("hook", 1000), ("purpose", 2000), ("cta", 500), ("caption", 5000)):
        if key in data:
            sets[key] = text(data, key, limit)
    if sets.get("title") == "":
        del sets["title"]
    if data.get("brand") in BRANDS:
        sets["brand"] = data["brand"]
    if data.get("format") in FORMATS:
        sets["format"] = data["format"]
    if isinstance(data.get("est_seconds"), (int, float)) and 3 <= data["est_seconds"] <= 180:
        sets["est_seconds"] = float(data["est_seconds"])
    if "arc_id" in data:
        arc_id = data["arc_id"]
        if arc_id is not None and _row("arcs", int(arc_id))["project_id"] != r["project_id"]:
            abort(400)
        sets["arc_id"] = arc_id
        sets["position"] = plan._next_position(db(), "ideas", "COALESCE(arc_id, 0) = ? AND project_id = ?",
                                               (arc_id or 0, r["project_id"]))
    if isinstance(data.get("position"), (int, float)):
        sets["position"] = float(data["position"])
    if sets:
        sets["updated_at"] = catalog.now()
        db().execute(f"UPDATE ideas SET {', '.join(f'{k} = ?' for k in sets)} WHERE id = ?",
                     [*sets.values(), idea_id])
        db().commit()
    return jsonify({"ok": True})


@bp.post("/api/plan-idea/<int:idea_id>/develop")
def develop(idea_id: int):
    _plan_idea(idea_id)
    busy = _start(f"develop:{idea_id}", plan.develop, idea_id, text(body(), "request", 4000))
    return jsonify(busy[0]) if busy else jsonify({"ok": True}), busy[1] if busy else 200


@bp.post("/api/plan-idea/<int:idea_id>/music")
def shortlist_music(idea_id: int):
    _plan_idea(idea_id)
    busy = _start(f"music:{idea_id}", plan.shortlist_music, idea_id)
    return jsonify(busy[0]) if busy else jsonify({"ok": True}), busy[1] if busy else 200


@bp.post("/api/plan-idea/<int:idea_id>/music/choose")
def choose_music(idea_id: int):
    _plan_idea(idea_id)
    plan.choose_music(db(), idea_id, body().get("recording_id"))
    return jsonify({"ok": True})


@bp.post("/api/plan-idea/<int:idea_id>/vote")
def vote(idea_id: int):
    _plan_idea(idea_id)
    v = body().get("vote")
    if v in (1, -1):
        db().execute("""INSERT INTO votes (idea_id, user_id, vote, created_at) VALUES (?, ?, ?, ?)
                        ON CONFLICT (idea_id, user_id) DO UPDATE SET vote = excluded.vote""",
                     (idea_id, g.user["id"], v, catalog.now()))
    else:
        db().execute("DELETE FROM votes WHERE idea_id = ? AND user_id = ?", (idea_id, g.user["id"]))
    db().commit()
    return jsonify({"ok": True})


@bp.post("/api/plan-idea/<int:idea_id>/decision")
@owner_only
def decide(idea_id: int):
    _plan_idea(idea_id)
    decision = body().get("decision")
    if decision in DECISIONS:
        db().execute("UPDATE ideas SET decision = ?, updated_at = ? WHERE id = ?", (decision, catalog.now(), idea_id))
    elif decision == "dismissed":
        db().execute("UPDATE ideas SET status = 'dismissed', updated_at = ? WHERE id = ?", (catalog.now(), idea_id))
    else:
        return jsonify({"error": "unknown decision"}), 400
    db().commit()
    return jsonify({"ok": True})


@bp.post("/api/idea/<int:idea_id>/adopt")
def adopt(idea_id: int):
    """Move a library idea (from footage) onto a project's board."""
    _row("ideas", idea_id)
    data = body()
    project_id = int(data.get("project_id") or 0)
    _row("projects", project_id)
    arc_id = data.get("arc_id")
    if arc_id is not None and _row("arcs", int(arc_id))["project_id"] != project_id:
        abort(400)
    plan.adopt(db(), idea_id, project_id, arc_id)
    return jsonify({"ok": True})


# ── Shots and takes ───────────────────────────────────────────────────────────

def _shot_fields(data: dict) -> dict:
    out = {}
    for key, limit in (("description", 1000), ("line", 1000), ("on_screen", 300), ("why", 500)):
        if key in data:
            out[key] = text(data, key, limit)
    for key, allowed in (("kind", plan.SHOT_KINDS), ("framing", plan.FRAMINGS), ("camera", list(plan.CAMERAS))):
        if key in data and (data[key] in allowed or data[key] is None):
            out[key] = data[key]
    if isinstance(data.get("seconds"), (int, float)) and 0 < data["seconds"] <= 120:
        out["seconds"] = float(data["seconds"])
    return out


@bp.post("/api/plan-idea/<int:idea_id>/shots")
def add_shot(idea_id: int):
    _plan_idea(idea_id)
    fields = _shot_fields(body())
    if not fields.get("description"):
        return jsonify({"error": "describe the shot"}), 400
    sid = plan.add_shot(db(), idea_id, fields)
    _touch(idea_id)
    db().commit()
    return jsonify({"id": sid})


@bp.post("/api/shot/<int:shot_id>")
def update_shot(shot_id: int):
    shot = _row("shots", shot_id)
    data = body()
    if data.get("delete"):
        db().execute("DELETE FROM takes WHERE shot_id = ?", (shot_id,))
        db().execute("DELETE FROM shots WHERE id = ?", (shot_id,))
    else:
        sets = _shot_fields(data)
        if "filmed" in data:
            sets["filmed_at"] = catalog.now() if data["filmed"] else None
            sets["filmed_by"] = g.user["id"] if data["filmed"] else None
        if sets:
            db().execute(f"UPDATE shots SET {', '.join(f'{k} = ?' for k in sets)} WHERE id = ?",
                         [*sets.values(), shot_id])
        if data.get("move") in (-1, 1):
            ids = [r["id"] for r in db().execute("SELECT id FROM shots WHERE idea_id = ? ORDER BY position, id",
                                                 (shot["idea_id"],))]
            i = ids.index(shot_id)
            j = i + data["move"]
            if 0 <= j < len(ids):
                ids[i], ids[j] = ids[j], ids[i]
                for pos, sid in enumerate(ids, 1):
                    db().execute("UPDATE shots SET position = ? WHERE id = ?", (pos, sid))
    _touch(shot["idea_id"])
    db().commit()
    return jsonify({"ok": True})


@bp.post("/api/shot/<int:shot_id>/takes")
def add_take(shot_id: int):
    _row("shots", shot_id)
    data = body()
    clip = _row("clips", int(data.get("clip_id") or 0))
    start = float(data["start"]) if isinstance(data.get("start"), (int, float)) else 0.0
    end = float(data["end"]) if isinstance(data.get("end"), (int, float)) else (clip["duration"] or 0)
    db().execute("""INSERT INTO takes (shot_id, clip_id, start, end, source, note, created_at)
                    VALUES (?, ?, ?, ?, 'manual', ?, ?)
                    ON CONFLICT (shot_id, clip_id) DO UPDATE SET start = excluded.start, end = excluded.end,
                        source = 'manual'""",
                 (shot_id, clip["id"], max(0.0, start), end, f"added by {g.user['name']}", catalog.now()))
    db().commit()
    return jsonify({"ok": True})


@bp.post("/api/take/<int:take_id>/remove")
def remove_take(take_id: int):
    _row("takes", take_id)
    db().execute("DELETE FROM takes WHERE id = ?", (take_id,))
    db().commit()
    return jsonify({"ok": True})


@bp.get("/media/take/<int:take_id>/thumb")
def take_thumb(take_id: int):
    take = _row("takes", take_id)
    clip = _row("clips", take["clip_id"])
    best = None
    for f in (Path(clip["clip_dir"]) / "frames").glob("*.jpg"):
        m = re.search(r"_(\d+\.\d+)s\.jpg$", f.name)
        if m:
            d = abs(float(m.group(1)) - (take["start"] or 0))
            if best is None or d < best[0]:
                best = (d, f)
    if not best:
        abort(404)
    return send_file(best[1], mimetype="image/jpeg", max_age=3600)
