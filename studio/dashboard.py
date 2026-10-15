"""
Studio dashboard: a small review site served by the laptop, laid out like a
Reels/TikTok grid. Bryan sees every video by stage — still in the cloud inbox,
being ingested, in the library, being edited, ready for review, done — opens one,
leaves notes pinned to moments in it, and approves or sends it back for changes,
then moves straight on to the next while the editor works in the background.

Runs inside the worker process (worker.py starts it on a thread) or on its own:
  python dashboard.py

Everyone signs in (auth.py): Bryan as owner, colleagues by invite. Media is only
ever served for files the catalogue points at, never by raw path.
"""
import json
import logging
import mimetypes
import os
import re
import sqlite3
import threading
from pathlib import Path

from datetime import timedelta

from flask import Flask, abort, g, jsonify, redirect, render_template_string, request, send_file, session
from flask.sessions import SecureCookieSessionInterface

import auth
import settings
import playbook
import usage
from settings import DASHBOARD_HOST, DASHBOARD_PORT, INBOX, VIDEO_EXTS
from webutil import LOOPBACK, body, client_ip, db, is_local, is_owner, owner_only, text

log = logging.getLogger("studio.dashboard")

WEB = Path(__file__).resolve().parent / "web"
app = Flask(__name__, static_folder=str(WEB), static_url_path="/static")


class _SessionInterface(SecureCookieSessionInterface):
    """Mark the cookie Secure when the page came over HTTPS (via the tunnel), so
    plain http://127.0.0.1 on the laptop keeps working too."""
    def get_cookie_secure(self, app):
        return request.is_secure or request.headers.get("X-Forwarded-Proto") == "https"


app.session_interface = _SessionInterface()
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                  SESSION_COOKIE_NAME="studio_session", PERMANENT_SESSION_LIFETIME=timedelta(days=30),
                  MAX_CONTENT_LENGTH=2 * 1024 * 1024)

BRANDS = ("personal", "urban_makers", "unsure")
FORMATS = ("talking_head", "day_in_the_life", "compilation", "other")

# Where each catalogue status shows up on the board.
CLIP_STAGE = {"processing": "ingesting", "transcribed": "ingesting", "failed": "ingesting",
              "tagged": "library"}
PRODUCTION_STAGE = {"planned": "producing", "producing": "producing", "revising": "producing",
                    "failed": "producing", "ready": "review", "approved": "done", "posted": "done",
                    "rejected": "done"}


# ── Plumbing ──────────────────────────────────────────────────────────────────

@app.teardown_appcontext
def _close_db(_exc):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


_PUBLIC = ("/login", "/setup", "/invite/", "/healthz", "/static/")


@app.before_request
def _auth():
    """Signed-in users only, everywhere except the sign-in pages themselves."""
    if not app.secret_key:
        app.secret_key = auth.secret_key()
    if request.method == "POST":
        # Refuse cross-site form posts: a browser always sends Origin on POST.
        origin = request.headers.get("Origin")
        if origin and origin.split("://", 1)[-1] != request.host:
            return jsonify({"error": "cross-site request refused"}), 403
    if request.path.startswith(_PUBLIC):
        return None
    g.user = auth.current(db(), session)
    if g.user:
        return None
    if request.path.startswith(("/api/", "/media/")):
        return jsonify({"error": "signed out"}), 401
    if not auth.users_exist(db()):
        return redirect("/setup")
    return redirect("/login?next=" + request.full_path.rstrip("?") if request.path != "/" else "/login")


@app.after_request
def _headers(response):
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    return response


# ── Sign-in pages ─────────────────────────────────────────────────────────────

_FORM = (WEB / "login.html")


def _page(mode: str, error: str = "", **extra):
    return render_template_string(_FORM.read_text(encoding="utf-8"), mode=mode, error=error, **extra)


def _start_session(user_row: dict):
    session.clear()
    session.permanent = True
    session.update(uid=user_row["id"], sv=user_row["session_version"])


def _safe_next(target: str | None) -> str:
    # Same-site paths only: "//host" and "/\\host" are read by browsers as other sites.
    return target if target and target.startswith("/") and not target.startswith(("//", "/\\")) else "/"


@app.get("/healthz")
def healthz():
    return "ok"


@app.route("/login", methods=["GET", "POST"])
def login_page():
    if not auth.users_exist(db()):
        return redirect("/setup")
    if request.method == "GET":
        return _page("login", next=_safe_next(request.args.get("next")))
    try:
        user_row = auth.login(db(), request.form.get("email", ""), request.form.get("password", ""), client_ip())
    except ValueError as e:
        return _page("login", str(e), next=_safe_next(request.form.get("next"))), 401
    _start_session(user_row)
    log.info("%s signed in from %s", user_row["email"], client_ip())
    return redirect(_safe_next(request.form.get("next")))


@app.route("/setup", methods=["GET", "POST"])
def setup_page():
    """The owner's account, made once, from the laptop itself."""
    if auth.users_exist(db()):
        return redirect("/login")
    if not is_local():
        return _page("blocked"), 403
    if request.method == "GET":
        return _page("setup")
    try:
        uid = auth.create_user(db(), request.form.get("email", ""), request.form.get("name", ""),
                               request.form.get("password", ""), auth.OWNER)
    except ValueError as e:
        return _page("setup", str(e)), 400
    _start_session(dict(db().execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()))
    return redirect("/")


@app.route("/invite/<token>", methods=["GET", "POST"])
def invite_page(token: str):
    if auth.open_invite(db(), token) is None:
        return _page("expired"), 404
    if request.method == "GET":
        return _page("invite", token=token)
    try:
        uid = auth.accept_invite(db(), token, request.form.get("email", ""), request.form.get("name", ""),
                                 request.form.get("password", ""))
    except ValueError as e:
        return _page("invite", str(e), token=token), 400
    _start_session(dict(db().execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()))
    return redirect("/#/tab/plan")


@app.post("/logout")
def logout():
    session.clear()
    return jsonify({"ok": True})


@app.get("/api/me")
def me():
    u = g.user
    return jsonify({"id": u["id"], "name": u["name"], "email": u["email"], "role": u["role"],
                    "local": is_local()})


@app.post("/api/me/password")
def change_password():
    data = body()
    u = db().execute("SELECT * FROM users WHERE id = ?", (g.user["id"],)).fetchone()
    from werkzeug.security import check_password_hash
    if not check_password_hash(u["password_hash"], str(data.get("current", ""))):
        return jsonify({"error": "your current password is wrong"}), 400
    try:
        auth.set_password(db(), u["id"], str(data.get("new", "")))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    _start_session(dict(db().execute("SELECT * FROM users WHERE id = ?", (u["id"],)).fetchone()))
    return jsonify({"ok": True})


@app.get("/api/team")
@owner_only
def team():
    users = [{k: r[k] for k in ("id", "name", "email", "role", "created_at", "last_login_at")}
             for r in db().execute("SELECT * FROM users WHERE disabled = 0 ORDER BY id")]
    invites = [{"id": r["id"], "note": r["note"], "expires_at": r["expires_at"]} for r in db().execute(
        "SELECT * FROM invites WHERE used_at IS NULL AND expires_at > ? ORDER BY id DESC", (_now(),))]
    return jsonify({"users": users, "invites": invites})


@app.post("/api/team/invite")
@owner_only
def invite():
    token = auth.create_invite(db(), g.user["id"], auth.TEAM, text(body(), "note", 200))
    base = settings.DASHBOARD_URL or request.host_url.rstrip("/")
    return jsonify({"link": f"{base}/invite/{token}", "days": auth.INVITE_DAYS})


@app.post("/api/team/invite/<int:invite_id>/cancel")
@owner_only
def cancel_invite(invite_id: int):
    db().execute("UPDATE invites SET expires_at = ? WHERE id = ? AND used_at IS NULL", (_now(), invite_id))
    db().commit()
    return jsonify({"ok": True})


@app.post("/api/team/<int:user_id>/remove")
@owner_only
def remove_member(user_id: int):
    if user_id == g.user["id"]:
        return jsonify({"error": "you can't remove yourself"}), 400
    auth.remove_user(db(), user_id)
    return jsonify({"ok": True})


def _now() -> str:
    import catalog
    return catalog.now()


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
    from worker import inbox_files, is_ready
    items = []
    for p in inbox_files():
        try:  # the worker may move it out of the inbox while we look
            stat = p.stat()
        except OSError:
            continue
        items.append({
            "kind": "inbox", "id": p.name, "stage": "cloud",
            "title": p.name if p.parent == INBOX else f"{p.parent.name} / {p.name}",
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
        "verdict": json.loads(row["quality"]).get("verdict") if row["quality"] else None,
        "project_id": row["project_id"],
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


def _frame_near(clip_dir: Path, t: float) -> Path | None:
    """The extracted frame closest to t seconds (frame names carry their time)."""
    best = None
    for f in (clip_dir / "frames").glob("*.jpg"):
        m = re.search(r"_(\d+\.\d+)s\.jpg$", f.name)
        if m and (best is None or abs(float(m.group(1)) - t) < best[0]):
            best = (abs(float(m.group(1)) - t), f)
    return best[1] if best else None


def _idea_item(row: sqlite3.Row) -> dict:
    segments = json.loads(row["segments"] or "[]")
    first = segments[0] if segments else None
    return {
        "kind": "idea", "id": row["id"], "stage": "ideas", "status": row["status"],
        "title": row["title"], "brand": row["brand"], "format": row["format"],
        "duration": row["est_seconds"], "segments_count": len(segments),
        "thumb": f"/media/idea/{row['id']}/thumb" if first else None,
        # #t= starts the hover preview at the idea's first segment.
        "video": f"/media/clip/{first['clip_id']}/video#t={first['start']}" if first else None,
        "updated": row["created_at"],
    }


@app.get("/")
def index():
    return send_file(WEB / "index.html")


@app.get("/api/board")
def board():
    clip_notes, production_notes = _open_notes("clip"), _open_notes("production")
    clips = db().execute("SELECT * FROM clips ORDER BY ingested_at DESC").fetchall()
    productions = db().execute("SELECT * FROM productions ORDER BY updated_at").fetchall()
    ideas = db().execute("""SELECT * FROM ideas WHERE status = 'new' AND COALESCE(origin, 'library') = 'library'
                            ORDER BY id DESC""").fetchall()
    return jsonify({
        "items": [*_inbox_items(),
                  *(_clip_item(r, clip_notes) for r in clips),
                  *(_production_item(r, production_notes) for r in productions),
                  *(_idea_item(r) for r in ideas)],
        "ideas_job": dict(_ideas_job),
        "costs": usage.month_summary(db()) if is_owner() else None,
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
        "cost": usage.total_for(db(), f"clip:{clip_id}") if is_owner() else None,
        "quality": json.loads(row["quality"]) if row["quality"] else None,
        "shots": [dict(r) for r in db().execute(
            """SELECT t.id AS take_id, t.shot_id, t.source, t.start, t.end, s.description, i.id AS idea_id,
                      i.title AS idea_title FROM takes t JOIN shots s ON s.id = t.shot_id
                      JOIN ideas i ON i.id = s.idea_id WHERE t.clip_id = ? ORDER BY t.id""", (clip_id,))],
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
    edl = json.loads(row["edl"]) if row["edl"] else None
    item.update({"idea": row["idea"], "caption": row["caption"] or "", "sources": sources,
                 "notes": _notes("production", production_id), "error": row["error"],
                 "edit_summary": edl["summary"] if edl else None,
                 "missing_shots": (edl or {}).get("missing_shots", []),
                 "graphics": len((edl or {}).get("graphics", [])),
                 "wishlist": json.loads(row["plan"] or "{}").get("wishlist", []),
                 "cost": usage.total_for(db(), f"production:{production_id}") if is_owner() else None,
                 "music": json.loads(row["music"]) if row["music"] else None,
                 "qa": json.loads(row["qa"]) if row["qa"] else None,
                 "resolve": _resolve_state(dict(row)) if edl else None})
    return jsonify(item)


@app.get("/api/idea/<int:idea_id>")
def idea_detail(idea_id: int):
    row = db().execute("SELECT * FROM ideas WHERE id = ?", (idea_id,)).fetchone()
    if row is None:
        abort(404)
    item = _idea_item(row)
    titles = {r["id"]: r["title"] or r["source_name"]
              for r in db().execute("SELECT id, title, source_name FROM clips")}
    segments = [{**seg, "clip_title": titles.get(seg["clip_id"], f"Clip {seg['clip_id']}"),
                 "video": f"/media/clip/{seg['clip_id']}/video"}
                for seg in json.loads(row["segments"] or "[]")]
    item.update({"hook": row["hook"], "angle": row["angle"], "beats": json.loads(row["beats"] or "[]"),
                 "segments": segments, "caption": row["caption"] or "", "production_id": row["production_id"],
                 "wishlist": json.loads(row["wishlist"] or "[]")})
    return jsonify(item)


# ── Actions ───────────────────────────────────────────────────────────────────

# One "Suggest posts" run at a time, in the background; the board reports progress.
_ideas_job = {"running": False, "error": None, "count": None, "finished": None}
_ideas_lock = threading.Lock()


def _run_ideas(focus: str) -> None:
    import catalog
    import ideas
    conn = catalog.connect()
    try:
        _, count = ideas.generate(conn, focus)
        _ideas_job.update(count=count, error=None)
    except Exception as e:  # shown on the dashboard; details in worker.log
        log.exception("Idea generation failed")
        _ideas_job.update(error=f"{type(e).__name__}: {e}", count=None)
    finally:
        conn.close()
        _ideas_job.update(running=False, finished=catalog.now())


@app.post("/api/ideas/generate")
@owner_only
def generate_ideas():
    focus = str((request.json or {}).get("focus", ""))[:500]
    with _ideas_lock:
        if _ideas_job["running"]:
            return jsonify({"error": "already suggesting ideas"}), 409
        _ideas_job.update(running=True, error=None, count=None)
    threading.Thread(target=_run_ideas, args=(focus,), name="ideas", daemon=True).start()
    return jsonify({"ok": True})


@app.post("/api/idea/<int:idea_id>/decision")
@owner_only
def decide_idea(idea_id: int):
    import catalog
    import plan
    data = request.json or {}
    row = db().execute("SELECT * FROM ideas WHERE id = ?", (idea_id,)).fetchone()
    if row is None:
        abort(404)
    if data.get("decision") == "make":
        try:
            production_id = (plan.make(db(), idea_id, data.get("caption")) if row["origin"] == "plan"
                             else catalog.make_production(db(), idea_id, data.get("caption")))
        except RuntimeError as e:
            return jsonify({"error": str(e)}), 400
        return jsonify({"ok": True, "production_id": production_id})
    if data.get("decision") == "dismiss":
        db().execute("UPDATE ideas SET status = 'dismissed' WHERE id = ?", (idea_id,))
        db().commit()
        return jsonify({"ok": True})
    return jsonify({"error": "unknown decision"}), 400


@app.post("/api/<target>/<int:target_id>/notes")
def add_note(target: str, target_id: int):
    import catalog
    if target not in ("clip", "production", "idea"):
        abort(404)
    body = (request.json or {}).get("body", "").strip()
    if not body:
        return jsonify({"error": "empty note"}), 400
    at = (request.json or {}).get("at_seconds")
    remember = bool((request.json or {}).get("remember")) and is_owner()
    note_id = catalog.add_feedback(db(), target, target_id, body,
                                   None if at is None else round(float(at), 2), remember, g.user["name"])
    if remember:
        table = {"clip": "clips", "production": "productions", "idea": "ideas"}[target]
        row = db().execute(f"SELECT brand, format FROM {table} WHERE id = ?", (target_id,)).fetchone()
        playbook.remember(body, " / ".join(filter(None, [row["brand"], row["format"]])) if row else target)
    return jsonify({"id": note_id})


# "Open in DaVinci Resolve": built on request, in the background (it renders the
# caption and graphics layers again, a minute or two), one at a time.
_resolve_jobs: dict[int, dict] = {}
_resolve_lock = threading.Lock()


def _resolve_state(production: dict) -> dict:
    import resolve_export
    job = _resolve_jobs.get(production["id"])
    if job and job["version"] == production["version"] and job["state"] in ("building", "failed"):
        return {"state": job["state"], "error": job.get("error")}
    folder = resolve_export.existing(production)
    return {"state": "ready", "folder": str(folder)} if folder else {"state": "none"}


def _run_resolve(production_id: int) -> None:
    import catalog
    import resolve_export
    conn = catalog.connect()
    try:
        with _resolve_lock:
            resolve_export.build(conn, production_id)
        _resolve_jobs.pop(production_id, None)
    except Exception as e:  # shown on the dashboard; details in worker.log
        log.exception("Resolve export failed for production %s", production_id)
        _resolve_jobs[production_id].update(state="failed", error=f"{type(e).__name__}: {e}")
    finally:
        conn.close()


@app.post("/api/production/<int:production_id>/resolve")
@owner_only
def build_resolve(production_id: int):
    row = db().execute("SELECT * FROM productions WHERE id = ?", (production_id,)).fetchone()
    if row is None:
        abort(404)
    if not row["edl"]:
        return jsonify({"error": "there's no finished draft to export yet"}), 400
    job = _resolve_jobs.get(production_id)
    if job and job["state"] == "building":
        return jsonify({"error": "already building"}), 409
    _resolve_jobs[production_id] = {"state": "building", "version": row["version"]}
    threading.Thread(target=_run_resolve, args=(production_id,), name="resolve", daemon=True).start()
    return jsonify({"ok": True})


@app.post("/api/production/<int:production_id>/resolve/open")
@owner_only
def open_resolve(production_id: int):
    import resolve_export
    row = db().execute("SELECT * FROM productions WHERE id = ?", (production_id,)).fetchone()
    folder = resolve_export.existing(dict(row)) if row else None
    if folder is None:
        abort(404)
    # Explorer can only open on the laptop itself, i.e. when the browser is on it too.
    opened = os.name == "nt" and is_local()
    if opened:
        os.startfile(folder)  # noqa: S606 - a folder Studio wrote, opened in Explorer
    return jsonify({"opened": opened, "folder": str(folder)})


@app.post("/api/production/<int:production_id>/decision")
def decide(production_id: int):
    import catalog
    data = request.json or {}
    decision, caption = data.get("decision"), data.get("caption")
    row = db().execute("SELECT * FROM productions WHERE id = ?", (production_id,)).fetchone()
    if row is None:
        abort(404)
    if decision != "caption" and not is_owner():
        return jsonify({"error": "only Bryan can do this"}), 403
    if decision == "reopen":  # undo an approve or reject by mistake
        if row["status"] not in ("approved", "rejected") or not row["video_path"]:
            return jsonify({"error": "only an approved or rejected draft can go back to review"}), 400
        catalog.set_production_status(db(), production_id, "ready", caption)
    elif decision == "changes":
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
    elif decision == "retry":
        if row["status"] != "failed":
            return jsonify({"error": "only a failed edit can be retried"}), 400
        catalog.set_production_status(db(), production_id, "planned", caption)
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


@app.get("/media/idea/<int:idea_id>/thumb")
def idea_thumb(idea_id: int):
    row = db().execute("SELECT segments FROM ideas WHERE id = ?", (idea_id,)).fetchone()
    segments = json.loads(row["segments"] or "[]") if row else []
    if not segments:
        abort(404)
    clip = db().execute("SELECT clip_dir FROM clips WHERE id = ?", (segments[0]["clip_id"],)).fetchone()
    frame = _frame_near(Path(clip["clip_dir"]), segments[0]["start"]) if clip else None
    return _file_response(str(frame) if frame else None)


@app.get("/media/production/<int:production_id>/<what>")
def production_media(production_id: int, what: str):
    row = db().execute("SELECT * FROM productions WHERE id = ?", (production_id,)).fetchone()
    if row is None or what not in ("thumb", "video"):
        abort(404)
    return _file_response(row["thumb_path"] if what == "thumb" else row["video_path"])


# ── Planning board ────────────────────────────────────────────────────────────

from plan_routes import bp as _plan_bp  # noqa: E402 - its own module; shares webutil, not this file

app.register_blueprint(_plan_bp)


# ── Serve ─────────────────────────────────────────────────────────────────────

def run() -> None:
    import catalog
    conn = catalog.connect()
    try:
        no_users = not auth.users_exist(conn)
    finally:
        conn.close()
    if DASHBOARD_HOST not in LOOPBACK and no_users:
        log.error("Dashboard not started on %s: make your account first (open it on the laptop at "
                  "http://127.0.0.1:%s with STUDIO_DASHBOARD_HOST unset, or run worker.py --add-user).",
                  DASHBOARD_HOST, DASHBOARD_PORT)
        return
    app.secret_key = auth.secret_key()
    from waitress import serve
    log.info("Dashboard on http://%s:%s", DASHBOARD_HOST, DASHBOARD_PORT)
    serve(app, host=DASHBOARD_HOST, port=DASHBOARD_PORT, threads=8, _quiet=True)


if __name__ == "__main__":
    settings.ensure_folders()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    run()
