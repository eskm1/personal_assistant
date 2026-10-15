"""
Studio dashboard: a small review site served by the laptop, laid out like a
Reels/TikTok grid. Bryan sees every video by stage — still in the cloud inbox,
being ingested, in the library, being edited, ready for review, done — opens one,
leaves notes pinned to moments in it, and approves or sends it back for changes,
then moves straight on to the next while the editor works in the background.

Runs inside the worker process (worker.py starts it on a thread) or on its own:
  python dashboard.py

Media is only ever served for files the catalogue points at, never by raw path.
"""
import hmac
import json
import logging
import mimetypes
import sqlite3
from pathlib import Path

from flask import Flask, Response, abort, g, jsonify, request, send_file

import settings
from settings import DASHBOARD_HOST, DASHBOARD_PASSWORD, DASHBOARD_PORT, INBOX, PLAYBOOK, VIDEO_EXTS

log = logging.getLogger("studio.dashboard")

WEB = Path(__file__).resolve().parent / "web"
app = Flask(__name__, static_folder=str(WEB), static_url_path="/static")

BRANDS = ("personal", "urban_makers", "unsure")
FORMATS = ("talking_head", "day_in_the_life", "compilation", "other")
LOOPBACK = {"127.0.0.1", "localhost", "::1"}

# Where each catalogue status shows up on the board.
CLIP_STAGE = {"processing": "ingesting", "transcribed": "ingesting", "failed": "ingesting",
              "tagged": "library"}
PRODUCTION_STAGE = {"planned": "producing", "producing": "producing", "revising": "producing",
                    "ready": "review", "approved": "done", "posted": "done", "rejected": "done"}


# ── Plumbing ──────────────────────────────────────────────────────────────────

def db() -> sqlite3.Connection:
    if "db" not in g:
        import catalog
        g.db = catalog.connect()
    return g.db


@app.teardown_appcontext
def _close_db(_exc):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


@app.before_request
def _auth():
    """HTTP Basic auth (any username) whenever a password is set."""
    if not DASHBOARD_PASSWORD:
        return None
    given = request.authorization.password if request.authorization else ""
    if given and hmac.compare_digest(given.encode(), DASHBOARD_PASSWORD.encode()):
        return None
    return Response("Password required", 401, {"WWW-Authenticate": 'Basic realm="Studio"'})


def _file_response(path: str | None):
    if not path or not Path(path).is_file():
        abort(404)
    mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
    if mime == "video/quicktime":
        mime = "video/mp4"  # browsers play iPhone .MOV (H.264/HEVC) when it's labelled mp4
    # conditional=True answers Range requests, so the player can seek.
    return send_file(path, mimetype=mime, conditional=True, max_age=3600)


def _clip_files(row: sqlite3.Row) -> dict:
    clip_dir = Path(row["clip_dir"])
    frames = sorted((clip_dir / "frames").glob("*.jpg"))
    # Newest preview first; clips from before a recipe change fall back to the old one.
    preview = next((p for p in (clip_dir / "preview-1080.mp4", clip_dir / "preview.mp4") if p.exists()), None)
    return {
        "thumb": str(frames[len(frames) // 2]) if frames else None,
        "video": str(preview) if preview else row["video_path"],
        "original": row["video_path"],
    }


def _open_notes(target: str) -> dict[int, int]:
    rows = db().execute(
        "SELECT target_id, COUNT(*) AS n FROM feedback WHERE target = ? AND status = 'open' GROUP BY target_id",
        (target,),
    ).fetchall()
    return {r["target_id"]: r["n"] for r in rows}


# ── Board ─────────────────────────────────────────────────────────────────────

def _inbox_items() -> list[dict]:
    from worker import is_ready
    if not INBOX.exists():
        return []
    items = []
    for p in INBOX.iterdir():
        if not (p.suffix.lower() in VIDEO_EXTS) or p.name.startswith((".", "~")):
            continue
        try:  # the worker may move it out of the inbox while we look
            stat = p.stat()
        except OSError:
            continue
        items.append({
            "kind": "inbox", "id": p.name, "stage": "cloud", "title": p.name,
            "status": "queued" if is_ready(p) else "uploading", "mtime": stat.st_mtime,
            "size_mb": round(stat.st_size / 1e6, 1), "thumb": None, "video": None,
        })
    return sorted(items, key=lambda i: i["mtime"], reverse=True)


def _clip_item(row: sqlite3.Row, open_notes: dict[int, int]) -> dict:
    files = _clip_files(row)
    return {
        "kind": "clip", "id": row["id"], "stage": CLIP_STAGE.get(row["status"], "ingesting"),
        "status": row["status"], "title": row["title"] or row["source_name"],
        "brand": row["brand"], "format": row["format"], "duration": row["duration"],
        "orientation": row["orientation"], "usable": row["usable"], "reviewed": bool(row["reviewed_at"]),
        "error": row["error"], "open_notes": open_notes.get(row["id"], 0),
        "thumb": f"/media/clip/{row['id']}/thumb" if files["thumb"] else None,
        "video": f"/media/clip/{row['id']}/video" if row["status"] != "processing" else None,
        "original": f"/media/clip/{row['id']}/original" if row["status"] != "processing" else None,
        "updated": row["ingested_at"],
    }


def _production_item(row: sqlite3.Row, open_notes: dict[int, int]) -> dict:
    return {
        "kind": "production", "id": row["id"], "stage": PRODUCTION_STAGE.get(row["status"], "producing"),
        "status": row["status"], "title": row["title"], "brand": row["brand"], "format": row["format"],
        "version": row["version"], "duration": row["duration"], "open_notes": open_notes.get(row["id"], 0),
        # ?v= makes the browser fetch the new render after each revision.
        "thumb": f"/media/production/{row['id']}/thumb?v={row['version']}" if row["thumb_path"] else None,
        "video": f"/media/production/{row['id']}/video?v={row['version']}" if row["video_path"] else None,
        "updated": row["updated_at"],
    }


@app.get("/")
def index():
    return send_file(WEB / "index.html")


@app.get("/api/board")
def board():
    clip_notes, production_notes = _open_notes("clip"), _open_notes("production")
    clips = db().execute("SELECT * FROM clips ORDER BY ingested_at DESC").fetchall()
    productions = db().execute("SELECT * FROM productions ORDER BY updated_at").fetchall()
    return jsonify({
        "items": [*_inbox_items(),
                  *(_clip_item(r, clip_notes) for r in clips),
                  *(_production_item(r, production_notes) for r in productions)],
    })


# ── Detail ────────────────────────────────────────────────────────────────────

def _notes(target: str, target_id: int) -> list[dict]:
    import catalog
    return [dict(r) for r in catalog.feedback_for(db(), target, target_id)]


@app.get("/api/clip/<int:clip_id>")
def clip_detail(clip_id: int):
    row = db().execute("SELECT * FROM clips WHERE id = ?", (clip_id,)).fetchone()
    if row is None:
        abort(404)
    item = _clip_item(row, _open_notes("clip"))
    item.update({
        "summary": row["summary"], "brand_reason": row["brand_reason"], "setting": row["setting"],
        "topics": json.loads(row["topics"] or "[]"), "moments": json.loads(row["moments"] or "[]"),
        "suggested_hook": row["suggested_hook"], "quality_notes": row["quality_notes"],
        "transcript": row["transcript"], "source_name": row["source_name"],
        "notes": _notes("clip", clip_id), "brands": BRANDS, "formats": FORMATS,
    })
    return jsonify(item)


@app.get("/api/production/<int:production_id>")
def production_detail(production_id: int):
    row = db().execute("SELECT * FROM productions WHERE id = ?", (production_id,)).fetchone()
    if row is None:
        abort(404)
    item = _production_item(row, _open_notes("production"))
    clip_ids = json.loads(row["source_clips"] or "[]")
    sources = []
    if clip_ids:
        marks = ",".join("?" * len(clip_ids))
        sources = [{"id": r["id"], "title": r["title"] or r["source_name"]} for r in db().execute(
            f"SELECT id, title, source_name FROM clips WHERE id IN ({marks})", clip_ids)]
    item.update({"idea": row["idea"], "caption": row["caption"] or "", "sources": sources,
                 "notes": _notes("production", production_id)})
    return jsonify(item)


# ── Actions ───────────────────────────────────────────────────────────────────

def _remember(note: str, context: str) -> None:
    """Append a note to the playbook the editor reads, so it's never needed twice."""
    from datetime import date
    if not PLAYBOOK.exists():
        PLAYBOOK.write_text("# Studio playbook\n\nRules the editor follows on every edit. "
                            "Edit freely.\n\n## Notes from reviews\n", encoding="utf-8")
    with PLAYBOOK.open("a", encoding="utf-8") as f:
        f.write(f"- {note.strip()} ({context}, {date.today().isoformat()})\n")


@app.post("/api/<target>/<int:target_id>/notes")
def add_note(target: str, target_id: int):
    import catalog
    if target not in ("clip", "production"):
        abort(404)
    body = (request.json or {}).get("body", "").strip()
    if not body:
        return jsonify({"error": "empty note"}), 400
    at = (request.json or {}).get("at_seconds")
    remember = bool((request.json or {}).get("remember"))
    note_id = catalog.add_feedback(db(), target, target_id, body,
                                   None if at is None else round(float(at), 2), remember)
    if remember:
        table = "clips" if target == "clip" else "productions"
        row = db().execute(f"SELECT brand, format FROM {table} WHERE id = ?", (target_id,)).fetchone()
        _remember(body, " / ".join(filter(None, [row["brand"], row["format"]])) if row else target)
    return jsonify({"id": note_id})


@app.post("/api/production/<int:production_id>/decision")
def decide(production_id: int):
    import catalog
    data = request.json or {}
    decision, caption = data.get("decision"), data.get("caption")
    row = db().execute("SELECT * FROM productions WHERE id = ?", (production_id,)).fetchone()
    if row is None:
        abort(404)
    if decision == "changes":
        open_notes = db().execute(
            "SELECT COUNT(*) FROM feedback WHERE target = 'production' AND target_id = ? AND status = 'open'",
            (production_id,)).fetchone()[0]
        if not open_notes:
            return jsonify({"error": "add at least one note first"}), 400
        catalog.set_production_status(db(), production_id, "revising", caption)
    elif decision in ("approve", "reject"):
        catalog.set_production_status(db(), production_id,
                                      "approved" if decision == "approve" else "rejected", caption)
    elif decision == "caption":
        catalog.set_production_status(db(), production_id, row["status"], caption)
    else:
        return jsonify({"error": "unknown decision"}), 400
    log.info("Production %s: %s", production_id, decision)
    return jsonify({"ok": True})


@app.post("/api/clip/<int:clip_id>/review")
def review_clip(clip_id: int):
    import catalog
    data = request.json or {}
    if data.get("brand") not in BRANDS or data.get("format") not in FORMATS:
        return jsonify({"error": "unknown brand or format"}), 400
    catalog.update_clip_review(db(), clip_id, data["brand"], data["format"], bool(data.get("usable", True)))
    return jsonify({"ok": True})


# ── Media ─────────────────────────────────────────────────────────────────────

@app.get("/media/clip/<int:clip_id>/<what>")
def clip_media(clip_id: int, what: str):
    row = db().execute("SELECT * FROM clips WHERE id = ?", (clip_id,)).fetchone()
    if row is None or what not in ("thumb", "video", "original"):
        abort(404)
    return _file_response(_clip_files(row)[what])


@app.get("/media/production/<int:production_id>/<what>")
def production_media(production_id: int, what: str):
    row = db().execute("SELECT * FROM productions WHERE id = ?", (production_id,)).fetchone()
    if row is None or what not in ("thumb", "video"):
        abort(404)
    return _file_response(row["thumb_path"] if what == "thumb" else row["video_path"])


# ── Serve ─────────────────────────────────────────────────────────────────────

def run() -> None:
    if DASHBOARD_HOST not in LOOPBACK and not DASHBOARD_PASSWORD:
        log.error("Dashboard not started: STUDIO_DASHBOARD_HOST=%s needs STUDIO_DASHBOARD_PASSWORD "
                  "so nobody else on the network can open it.", DASHBOARD_HOST)
        return
    from waitress import serve
    log.info("Dashboard on http://%s:%s", DASHBOARD_HOST, DASHBOARD_PORT)
    serve(app, host=DASHBOARD_HOST, port=DASHBOARD_PORT, threads=8, _quiet=True)


if __name__ == "__main__":
    settings.ensure_folders()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    run()
