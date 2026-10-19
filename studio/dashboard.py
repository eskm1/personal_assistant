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
import io
import json
import logging
import mimetypes
import os
import re
import shutil
import sqlite3
import threading
import time
import zipfile
from pathlib import Path

from datetime import timedelta

from flask import Flask, Response, abort, g, jsonify, redirect, render_template_string, request, send_file, session
from flask.sessions import SecureCookieSessionInterface

import auth
import cloud_sync
import settings
import playbook
import usage
from settings import DASHBOARD_HOST, DASHBOARD_PORT, INBOX, VIDEO_EXTS
from webutil import LOOPBACK, body, client_ip, db, is_local, is_owner, own_origin, owner_only, text

log = logging.getLogger("studio.dashboard")

WEB = Path(__file__).resolve().parent / "web"
app = Flask(__name__, static_folder=str(WEB), static_url_path="/static")


class _SessionInterface(SecureCookieSessionInterface):
    """Mark the cookie Secure when the page came over HTTPS (via the tunnel), so
    plain http://127.0.0.1 on the laptop keeps working too."""
    def get_cookie_secure(self, app):
        return (request.is_secure or request.headers.get("X-Forwarded-Proto") == "https"
                or (not is_local() and settings.DASHBOARD_URL.startswith("https://")))


app.session_interface = _SessionInterface()
# Flask opens the session before any request hook runs, so the key must be set up front.
app.secret_key = auth.secret_key()
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                  SESSION_COOKIE_NAME="studio_session", PERMANENT_SESSION_LIFETIME=timedelta(days=30),
                  MAX_CONTENT_LENGTH=2 * 1024 * 1024)

BRANDS = (*settings.BRANDS, "unsure") if len(settings.BRANDS) > 1 else settings.BRANDS
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


# The app icons are public too: a phone fetches them for the home screen, sometimes signed out.
_PUBLIC = ("/login", "/setup", "/invite/", "/healthz", "/static/", "/favicon.ico", "/apple-touch-icon",
           "/plan/favicon.ico", "/plan/apple-touch-icon.png", "/plan/icon-192.png", "/plan/icon-512.png")


@app.before_request
def _auth():
    """Signed-in users only, everywhere except the sign-in pages themselves."""
    if request.path.startswith("/api/uploads"):
        # The planner's upload API: a planner session (Bearer token) instead of the
        # dashboard cookie, from the planner's own address (CORS). uploads.guard()
        # refuses anything without a valid token.
        import uploads
        return uploads.guard()
    if request.method == "POST":
        # Refuse cross-site form posts: a browser always sends Origin on POST.
        origin = request.headers.get("Origin")
        if origin and not own_origin(origin):
            return jsonify({"error": "cross-site request refused"}), 403
    if request.path.startswith(_PUBLIC):
        return None
    g.user = auth.current(db(), session)
    if g.user:
        return None
    if session.get("uid"):
        session.clear()  # removed, password changed, or no longer allowed in: signed out
    if request.path.startswith(("/api/", "/media/")):
        return jsonify({"error": "signed out"}), 401
    if not auth.users_exist(db()):
        return redirect("/setup")
    if request.path == "/":  # Studio opens on Plan when the planner is here
        return redirect("/login?next=/plan/" if _plan_here() else "/login")
    return redirect("/login?next=" + request.full_path.rstrip("?"))


@app.after_request
def _headers(response):
    if request.path.startswith("/api/uploads"):
        import uploads
        uploads.cors(response)
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    return response


# ── Sign-in pages ─────────────────────────────────────────────────────────────

_FORM = (WEB / "login.html")


def _page(mode: str, error: str = "", **extra):
    return render_template_string(_FORM.read_text(encoding="utf-8"), mode=mode, error=error,
                                  planner=cloud_sync.enabled(), domain_hint=auth.domain_hint(), **extra)


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


@app.get("/favicon.ico")
@app.get("/apple-touch-icon.png")
@app.get("/apple-touch-icon-precomposed.png")
def app_icon():
    """Where browsers and iPhones look for the icon when a page doesn't say."""
    name = "studio.ico" if request.path.endswith(".ico") else "apple-touch-icon.png"
    return send_file(WEB / name, max_age=86400)


@app.route("/login", methods=["GET", "POST"])
def login_page():
    if not auth.users_exist(db()):
        return redirect("/setup")
    if request.method == "GET":
        return _page("login", next=_safe_next(request.args.get("next")))
    try:
        user_row = auth.login(db(), request.form.get("email", ""), request.form.get("password", ""), client_ip(),
                              planner=_planner_password if cloud_sync.enabled() else None)
    except ValueError as e:
        return _page("login", str(e), next=_safe_next(request.form.get("next"))), 401
    _start_session(user_row)
    log.info("%s signed in from %s", user_row["email"], client_ip())
    return redirect(_safe_next(request.form.get("next")))


def _planner_password(email: str, password: str) -> dict | None:
    """The planner's check of an email and password (one login for both)."""
    import cloud_auth
    try:
        return cloud_auth.password_login(email, password)
    except cloud_auth.AuthError as e:
        log.warning("Planner sign-in check failed: %s", e)
        raise ValueError("That email and password don't match here, and the planner can't be asked right now. "
                         "Try again in a minute.")


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
    return redirect("/plan/" if _plan_here() else "/#/tab/plan")


@app.post("/logout")
def logout():
    session.clear()
    return jsonify({"ok": True})


@app.get("/api/me")
def me():
    import updater
    u = g.user
    return jsonify({"id": u["id"], "name": u["name"], "email": u["email"], "role": u["role"],
                    "local": is_local(), "version": updater.version(), "brands": list(settings.BRANDS)})


@app.post("/api/update")
@owner_only
def update_now():
    """Check GitHub now (or with {"restart": true} just restart); the worker does it
    as soon as it's idle."""
    import updater
    if body().get("restart"):
        updater.restart_requested = True
    else:
        updater.requested = True
    return jsonify({"ok": True})


def busy() -> bool:
    """Background work started here that a restart would cut short."""
    import draft_review
    import plan_routes
    return (_ideas_job["running"] or draft_review.busy() or any(j.get("state") == "building" for j in _resolve_jobs.values())
            or any(j.get("state") == "running" for j in plan_routes._jobs.values()))


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
    domains = [d for d in settings.ALLOWED_EMAIL_DOMAINS if d != "*"]
    return jsonify({"users": users, "invites": invites,
                    "domains": [] if "*" in settings.ALLOWED_EMAIL_DOMAINS else domains})


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
            "title": " / ".join(p.relative_to(INBOX).parts),
            "status": "queued" if is_ready(p) else "uploading", "mtime": stat.st_mtime,
            "size_mb": round(stat.st_size / 1e6, 1), "thumb": None, "video": None,
        })
    return sorted(items, key=lambda i: i["mtime"], reverse=True)


def clip_flagged(quality: dict | None, usable) -> bool:
    """Only a clip that needs a retake (or isn't usable) is flagged; "check" is a note
    for the detail view, not a warning ("as long as videos are usable, don't flag")."""
    return (quality or {}).get("verdict") == "retake" or usable == 0


def _clip_item(row: sqlite3.Row, open_notes: dict[int, int]) -> dict:
    files = _clip_files(row)
    quality = json.loads(row["quality"]) if row["quality"] else None
    return {
        "kind": "clip", "id": row["id"], "stage": CLIP_STAGE.get(row["status"], "ingesting"),
        "status": row["status"], "title": row["title"] or row["source_name"],
        "brand": row["brand"], "format": row["format"], "duration": row["duration"],
        "orientation": row["orientation"], "usable": row["usable"], "reviewed": bool(row["reviewed_at"]),
        # verdict: good | check | retake (unchanged); flagged: show a warning only when true.
        "verdict": quality.get("verdict") if quality else None,
        "flagged": clip_flagged(quality, row["usable"]),
        "project_id": row["project_id"],
        "error": row["error"], "open_notes": open_notes.get(row["id"], 0),
        "thumb": f"/media/clip/{row['id']}/thumb" if files["thumb"] else None,
        "video": f"/media/clip/{row['id']}/video" if row["status"] != "processing" else None,
        "original": f"/media/clip/{row['id']}/original" if row["status"] != "processing" else None,
        "updated": row["ingested_at"],
    }


def _production_item(row: sqlite3.Row, open_notes: dict[int, int]) -> dict:
    if row["format"] == "carousel":
        return _carousel_item(row, open_notes)
    return {
        "kind": "production", "id": row["id"], "stage": PRODUCTION_STAGE.get(row["status"], "producing"),
        "status": row["status"], "title": row["title"], "brand": row["brand"], "format": row["format"],
        "version": row["version"], "duration": row["duration"], "open_notes": open_notes.get(row["id"], 0),
        # ?v= makes the browser fetch the new render after each revision.
        "thumb": f"/media/production/{row['id']}/thumb?v={row['version']}" if row["thumb_path"] else None,
        "video": f"/media/production/{row['id']}/video?v={row['version']}" if row["video_path"] else None,
        "updated": row["updated_at"],
    }


def _carousel_item(row: sqlite3.Row, open_notes: dict[int, int]) -> dict:
    """A carousel draft on the board: its cover is the tile, there's no video."""
    import carousel
    count = len(carousel.files(row))
    return {
        "kind": "production", "id": row["id"], "stage": PRODUCTION_STAGE.get(row["status"], "producing"),
        "status": row["status"], "title": row["title"], "brand": row["brand"], "format": "carousel",
        "version": row["version"], "duration": None, "open_notes": open_notes.get(row["id"], 0),
        "thumb": f"/media/production/{row['id']}/thumb?v={row['version']}" if row["thumb_path"] else None,
        "video": None, "slides_count": count, "updated": row["updated_at"],
    }


def _carousel_detail(row: sqlite3.Row) -> dict:
    """What Review shows for a carousel: the slides (images, words, picture), the read,
    the caption and the notes (a note can name a slide)."""
    import carousel
    import draft_review
    import concepts
    data = carousel.current(row)
    pngs = carousel.files(row)
    missing = {p["n"]: p.get("missing") for p in data.get("pictures", []) if p.get("missing")}
    stamp = f"{row['version']}.{data.get('layouts') or 1}"
    finished = carousel.post_type(db(), row) == concepts.FINISHED

    def concept(n: int, image: dict) -> dict | None:
        """A concept slide's options, for Review's picker (pick one, or Redraw)."""
        if image.get("source") != "concept":
            return None
        options = image.get("options") or []
        return {"options": [{"option": o, "image": f"/media/production/{row['id']}/concept/{n}/{o}"} for o in options],
                "chosen": image.get("chosen") if image.get("chosen") in options else (options[0] if options else None),
                "note": image.get("note") or "", "why": missing.get(n) or image.get("why"),
                "redraw": None if finished else concepts.available()}

    slides = [{"n": n, "role": s["role"], "heading": s["heading"], "body": s["body"],
               "picture": carousel.picture_text(s["image"]), "missing": missing.get(n),
               "concept": concept(n, s["image"]),
               "image": f"/media/production/{row['id']}/slide/{n}?v={stamp}" if n <= len(pngs) else None}
              for n, s in enumerate(data["slides"], 1)]
    item = _carousel_item(row, _open_notes("production"))
    item.update({"review": draft_review.current(row), "review_state": draft_review.state(row),
                 "review_error": draft_review.errors.get(row["id"]) if is_owner() else None,
                 "shot_list": False, "edit": None, "transcript": "", "words": 0, "languages": ["en"],
                 "idea": row["idea"], "caption": row["caption"] or "", "sources": [],
                 "notes": _notes("production", row["id"]), "error": row["error"],
                 "edit_summary": data.get("summary"), "missing_shots": [], "graphics": 0, "wishlist": [],
                 "cost": usage.total_for(db(), f"production:{row['id']}") if is_owner() else None,
                 "cost_parts": usage.parts_for(db(), f"production:{row['id']}") if is_owner() else None,
                 **_learning_detail(row, "carousel"),
                 "concepts": {"finished": finished, "per_image_usd": settings.IMAGE_COST_USD,
                              "options": len(concepts.OPTIONS), "why_not": concepts.available()},
                 "music": None, "qa": None, "resolve": None, "slides": slides,
                 "download": f"/api/production/{row['id']}/download" if pngs else None})
    clip_ids = json.loads(row["source_clips"] or "[]")
    if clip_ids:
        marks = ",".join("?" * len(clip_ids))
        item["sources"] = [{"id": r["id"], "title": r["title"] or r["source_name"]} for r in db().execute(
            f"SELECT id, title, source_name FROM clips WHERE id IN ({marks})", clip_ids)]
    return item


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


def _plan_here() -> bool:
    import planner_site
    return planner_site.here()


@app.get("/")
def index():
    """Review. With the planner here too, the page is marked so app.js opens Plan when
    the address has no Review route (#/tab/..., #/production/12 and so on)."""
    page = (WEB / "index.html").read_text(encoding="utf-8")
    if _plan_here():
        page = page.replace("<html lang=\"en\">", '<html lang="en" data-plan-here="/plan/">', 1)
    response = Response(page, mimetype="text/html")
    response.headers["Cache-Control"] = "no-cache"
    return response


@app.get("/api/nav")
def nav():
    """For the top bar on Plan: drafts waiting for review, and whether Studio is on
    (it answered, so it is; last_sync is the planner sync)."""
    waiting = db().execute("SELECT COUNT(*) FROM productions WHERE status = 'ready'").fetchone()[0]
    response = jsonify({"review": waiting, "studio": {"on": True, "last_sync": cloud_sync.status.get("last_ok")}})
    response.headers["Cache-Control"] = "no-store"
    return response


def _version_commit() -> str:
    import updater
    v = updater.version()
    return f"{v['commit']}|{v['subject']}"


@app.get("/api/board")
def board():
    clip_notes, production_notes = _open_notes("clip"), _open_notes("production")
    clips = db().execute("SELECT * FROM clips ORDER BY ingested_at DESC").fetchall()
    productions = db().execute("SELECT * FROM productions ORDER BY updated_at").fetchall()
    # One home for ideas: with planning online they live in the planner, not Review.
    online = cloud_sync.enabled()
    ideas = [] if online else db().execute("""SELECT * FROM ideas WHERE status = 'new'
                                              AND COALESCE(origin, 'library') = 'library' ORDER BY id DESC""").fetchall()
    out = {
        "items": [*_inbox_items(),
                  *(_clip_item(r, clip_notes) for r in clips),
                  *(_production_item(r, production_notes) for r in productions),
                  *(_idea_item(r) for r in ideas)],
        "ideas_online": online,
        "version": _version_commit(),
        "costs": usage.month_summary(db()) if is_owner() else None,
        # Planning online (cloud_sync.py): the Plan tab links to the planner instead.
        "cloud": cloud_sync.dashboard_info(),
        "machine": _machine_info(),
    }
    if not online:
        out["ideas_job"] = dict(_ideas_job)
    return jsonify(out)


def _machine_info() -> dict:
    """Free disk and, on the server, the OneDrive link: shown on the In the cloud tab."""
    import onedrive
    free = shutil.disk_usage(settings.ROOT).free
    info = {"disk_free_gb": round(free / 1e9, 1)}
    if onedrive.configured():
        st = onedrive.status()
        info["onedrive"] = {k: st.get(k) for k in ("inbox", "last_pull", "waiting", "uploading", "pulling", "last_error")}
    return info


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
        "transcript": row["transcript"], "source_name": row["source_name"], "recorded_at": row["recorded_at"],
        "notes": _notes("clip", clip_id), "brands": BRANDS, "formats": FORMATS,
        "cost": usage.total_for(db(), f"clip:{clip_id}") if is_owner() else None,
        "quality": json.loads(row["quality"]) if row["quality"] else None,
        "shots": [dict(r) for r in db().execute(
            """SELECT t.id AS take_id, t.shot_id, t.source, t.start, t.end, s.description, i.id AS idea_id, i.remote_id AS idea_remote_id,
                      i.title AS idea_title FROM takes t JOIN shots s ON s.id = t.shot_id
                      JOIN ideas i ON i.id = s.idea_id WHERE t.clip_id = ? ORDER BY t.id""", (clip_id,))],
    })
    return jsonify(item)


@app.get("/api/production/<int:production_id>")
def production_detail(production_id: int):
    row = db().execute("SELECT * FROM productions WHERE id = ?", (production_id,)).fetchone()
    if row is None:
        abort(404)
    if row["format"] == "carousel":
        return jsonify(_carousel_detail(row))
    item = _production_item(row, _open_notes("production"))
    clip_ids = json.loads(row["source_clips"] or "[]")
    sources = []
    if clip_ids:
        marks = ",".join("?" * len(clip_ids))
        sources = [{"id": r["id"], "title": r["title"] or r["source_name"]} for r in db().execute(
            f"SELECT id, title, source_name FROM clips WHERE id IN ({marks})", clip_ids)]
    edl = json.loads(row["edl"]) if row["edl"] else None
    import draft_review
    words = draft_review.draft_words(db(), dict(row)) if edl else []
    langs = []
    if clip_ids:
        langs = [r[0] for r in db().execute(
            f"SELECT DISTINCT language FROM clips WHERE id IN ({marks}) AND language IS NOT NULL", clip_ids)]
    cuts = (edl or {}).get("cuts", [])
    item.update({"review": draft_review.current(row), "review_state": draft_review.state(row),
                 "review_error": draft_review.errors.get(production_id) if is_owner() else None,
                 "shot_list": _shot_list_target(row) is not None,
                 "edit": {"cuts": len(cuts), "clips": len({c["clip_id"] for c in cuts}),
                          "hook_text": (edl or {}).get("hook_text", "")} if edl else None,
                 "transcript": " ".join(w["word"] for w in words), "words": len(words), "languages": langs})
    item.update({"idea": row["idea"], "caption": row["caption"] or "", "sources": sources,
                 "notes": _notes("production", production_id), "error": row["error"],
                 "edit_summary": edl["summary"] if edl else None,
                 "missing_shots": (edl or {}).get("missing_shots", []),
                 "graphics": len((edl or {}).get("graphics", [])),
                 "wishlist": json.loads(row["plan"] or "{}").get("wishlist", []),
                 "cost": usage.total_for(db(), f"production:{production_id}") if is_owner() else None,
                 **_learning_detail(row, "reel"),
                 "music": json.loads(row["music"]) if row["music"] else None,
                 "qa": json.loads(row["qa"]) if row["qa"] else None,
                 "resolve": _resolve_state(dict(row)) if edl else None})
    return jsonify(item)


def _learning_detail(row: sqlite3.Row, scope: str) -> dict:
    """What Studio fixed itself before Bryan saw this version, its cost, the trend of problems
    it catches itself, and the rules it taught itself (learning.py, self_fix.py)."""
    import learning
    import self_fix
    return {"self_fix": self_fix.summary(row),
            "self_fix_cost": usage.total_for(db(), self_fix.ref(row["id"])) if is_owner() else None,
            "self_fix_trend": learning.trend(db(), scope),
            "rules": {"learned": [{"id": r["id"], "text": r["text"], "count": r["count"], "last": r["last"]}
                                  for r in learning.active(scope)],
                      "code": learning.CODE_CHECKS.get(scope, [])}}


@app.get("/api/playbook")
def get_playbook():
    """The playbook: Bryan's part (to edit) and the rules Studio keeps itself (read-only)."""
    import learning
    return jsonify({"text": learning.bryan_text(), "editable": is_owner(),
                    "rules": {scope: [{"id": r["id"], "text": r["text"], "count": r["count"], "last": r["last"]}
                                      for r in learning.active(scope)] for scope in learning.SCOPES},
                    "code": learning.CODE_CHECKS})


@app.post("/api/playbook")
@owner_only
def save_playbook():
    import learning
    text = (request.json or {}).get("text")
    if not isinstance(text, str) or not text.strip():
        return jsonify({"error": "the playbook can't be empty"}), 400
    if len(text) > 100_000:
        return jsonify({"error": "that's too long for the playbook"}), 400
    learning.save_bryan_text(text)
    log.info("Playbook edited by %s", g.user["name"])
    return jsonify({"ok": True})


@app.post("/api/playbook/rule/<int:rule_id>/forget")
@owner_only
def forget_rule(rule_id: int):
    import learning
    if not learning.forget(rule_id):
        abort(404)
    return jsonify({"ok": True})


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


def _shot_list_target(row) -> tuple[str, int] | None:
    """Where a next-shoot suggestion goes: the planner idea the draft was made from, online
    ("cloud", its id there) or on this machine ("local", its id). None for library ideas."""
    plan_ = json.loads(row["plan"] or "{}")
    idea = db().execute("SELECT id, origin, remote_id FROM ideas WHERE id = ?",
                        (plan_.get("idea_id"),)).fetchone() if plan_.get("idea_id") else None
    if idea is None or idea["origin"] != "plan":
        return None
    if cloud_sync.enabled():
        return ("cloud", idea["remote_id"]) if idea["remote_id"] else None
    return "local", idea["id"]


# ── Actions ───────────────────────────────────────────────────────────────────

# One "Suggest posts" run at a time, in the background; the board reports progress.
# With planning online, ideas come from new footage in the planner instead (footage_ideas.py).
IDEAS_MOVED = "Ideas live in the planner now"
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
    if cloud_sync.enabled():
        return jsonify({"error": IDEAS_MOVED}), 410
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
    slide = (request.json or {}).get("slide")
    if slide is not None:  # a carousel note about one slide
        row = db().execute("SELECT format, slides, plan FROM productions WHERE id = ?", (target_id,)).fetchone() \
            if target == "production" else None
        import carousel
        count = len(carousel.current(row)["slides"]) if row and row["format"] == "carousel" else 0
        if not isinstance(slide, int) or isinstance(slide, bool) or not 1 <= slide <= count:
            return jsonify({"error": "that slide isn't in this carousel"}), 400
    note_id = catalog.add_feedback(db(), target, target_id, body,
                                   None if at is None else round(float(at), 2), remember, g.user["name"], slide)
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
    # Explorer can only open the folder when the browser is on the Windows laptop itself;
    # everywhere else (the server, a phone) it's a zip download.
    return ({"state": "ready", "folder": str(folder), "can_open": os.name == "nt" and is_local()}
            if folder else {"state": "none"})


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


@app.post("/api/production/<int:production_id>/review")
@owner_only
def get_review(production_id: int):
    """"Get Studio's read": review the current draft again, in the background."""
    import draft_review
    row = db().execute("SELECT * FROM productions WHERE id = ?", (production_id,)).fetchone()
    if row is None:
        abort(404)
    laid_out = row["slides"] if row["format"] == "carousel" else row["edl"] and row["video_path"]
    if not laid_out:
        return jsonify({"error": "there's no finished draft to review yet"}), 400
    if not draft_review.start(production_id):
        return jsonify({"error": "Studio is already reading this draft"}), 409
    return jsonify({"ok": True})


@app.post("/api/production/<int:production_id>/review/shot")
def review_shot(production_id: int):
    """Put a next-shoot suggestion from Studio's read on the idea's shot list."""
    import draft_review
    row = db().execute("SELECT * FROM productions WHERE id = ?", (production_id,)).fetchone()
    if row is None:
        abort(404)
    review = draft_review.current(row)
    try:
        index = int(body().get("fix"))
        fix = review["fixes"][index] if review and index >= 0 else None
    except (TypeError, ValueError, IndexError):
        fix = None
    if not fix or fix.get("when") != "next_shoot":
        return jsonify({"error": "that suggestion isn't there any more"}), 400
    if fix.get("shot_added"):
        return jsonify({"ok": True})
    target = _shot_list_target(row)
    if target is None:
        return jsonify({"error": "this draft wasn't made from a planned idea"}), 400
    shot = {"kind": fix.get("kind"), "description": fix["what"],
            "why": f"Suggested by the review: {fix['why']}"}
    try:
        if target[0] == "cloud":
            api = cloud_sync.Client()
            last = api.select("shots", {"idea_id": f"eq.{int(target[1])}", "select": "position"})
            position = max((r["position"] or 0 for r in last), default=0) + 1
            api.insert("shots", {"idea_id": int(target[1]), "position": position, **shot}, returning=False)
        else:
            import plan
            plan.add_shot(db(), target[1], shot)
    except cloud_sync.CloudError as e:
        return jsonify({"error": f"the planner couldn't be reached ({e})"}), 502
    review["fixes"][index]["shot_added"] = True
    db().execute("UPDATE productions SET review = ? WHERE id = ?", (json.dumps(review, ensure_ascii=False), production_id))
    db().commit()
    log.info("Production %s: review suggestion %d added to the shot list (%s)", production_id, index, target[0])
    return jsonify({"ok": True})


class _ZipSink(io.RawIOBase):
    """Where zipfile writes; the download hands each piece on as soon as it's written."""
    def __init__(self):
        self.parts = []

    def writable(self):
        return True

    def write(self, b):
        self.parts.append(bytes(b))
        return len(b)

    def take(self) -> bytes:
        out, self.parts = b"".join(self.parts), []
        return out


def _zip_stream(files: list[tuple[str, Path]]):
    """A zip of the files, made while it downloads (no copy on disk). Video doesn't
    compress, so the files are stored as they are."""
    sink = _ZipSink()
    with zipfile.ZipFile(sink, "w", zipfile.ZIP_STORED, allowZip64=True) as z:
        for name, path in files:
            info = zipfile.ZipInfo.from_file(path, name)
            with open(path, "rb") as src, z.open(info, "w", force_zip64=True) as dst:
                while chunk := src.read(1 << 20):
                    dst.write(chunk)
                    yield sink.take()
    yield sink.take()


@app.get("/api/production/<int:production_id>/resolve/download")
@owner_only
def download_resolve(production_id: int):
    import resolve_export
    row = db().execute("SELECT * FROM productions WHERE id = ?", (production_id,)).fetchone()
    files = resolve_export.download_files(db(), dict(row)) if row else []
    if not files:
        abort(404)
    name = f"{files[0][0].split('/')[0]}.zip"
    return Response(_zip_stream(files), mimetype="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.post("/api/production/<int:production_id>/resolve/open")
@owner_only
def open_resolve(production_id: int):
    import resolve_export
    row = db().execute("SELECT * FROM productions WHERE id = ?", (production_id,)).fetchone()
    folder = resolve_export.existing(dict(row)) if row else None
    if folder is None:
        abort(404)
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
        if row["status"] not in ("approved", "rejected") or not (row["video_path"] or row["slides"]):
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


@app.get("/media/production/<int:production_id>/slide/<int:n>")
def production_slide(production_id: int, n: int):
    """One slide of a carousel draft (PNG)."""
    import carousel
    row = db().execute("SELECT * FROM productions WHERE id = ?", (production_id,)).fetchone()
    pngs = carousel.files(row) if row is not None and row["format"] == "carousel" else []
    if not 1 <= n <= len(pngs):
        abort(404)
    return _file_response(str(pngs[n - 1]))


@app.get("/media/production/<int:production_id>/concept/<int:n>/<option>")
def production_concept(production_id: int, n: int, option: str):
    """One of a concept slide's options (JPEG), for Review's picker."""
    import carousel
    import concepts
    row = db().execute("SELECT * FROM productions WHERE id = ?", (production_id,)).fetchone()
    slides = carousel.current(row)["slides"] if row is not None and row["format"] == "carousel" else []
    image = slides[n - 1]["image"] if 1 <= n <= len(slides) else {}
    if image.get("source") != "concept" or option not in (image.get("options") or []):
        abort(404)
    return _file_response(str(concepts.option_path(image["concept"], option)))


def _concept_change(production_id: int, n: int, act):
    try:
        act()
    except KeyError:
        abort(404)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    log.info("Production %s slide %s: concept %s", production_id, n, request.path.rsplit("/", 1)[-1])
    return jsonify({"ok": True})


@app.post("/api/production/<int:production_id>/slide/<int:n>/choose")
@owner_only
def choose_concept(production_id: int, n: int):
    """Bryan picks one of a concept slide's two options: laid out again, nothing drawn."""
    import carousel
    option = str(body().get("option") or "")
    return _concept_change(production_id, n, lambda: carousel.choose(db(), production_id, n, option))


@app.post("/api/production/<int:production_id>/slide/<int:n>/redraw")
@owner_only
def redraw_concept(production_id: int, n: int):
    """Redraw (Bryan): two new options for one concept slide, as the next version. It spends,
    like Make this, so only the owner may."""
    import carousel
    change = str(body().get("change") or "")[:300]
    return _concept_change(production_id, n, lambda: carousel.redraw(db(), production_id, n, change))


@app.get("/api/production/<int:production_id>/download")
def download_carousel(production_id: int):
    """A carousel's slides and caption as a .zip (01.png, 02.png ... caption.txt)."""
    import carousel
    row = db().execute("SELECT * FROM productions WHERE id = ?", (production_id,)).fetchone()
    files = carousel.download_files(row) if row is not None and row["format"] == "carousel" else []
    if not files:
        abort(404)
    name = re.sub(r"[^\w\- ]+", "", row["title"] or "Carousel").strip()[:60] or "Carousel"
    folder = f"{name} v{row['version']}"
    return Response(_zip_stream([(f"{folder}/{n}", p) for n, p in files]), mimetype="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{folder}.zip"'})


@app.get("/media/production/<int:production_id>/<what>")
def production_media(production_id: int, what: str):
    row = db().execute("SELECT * FROM productions WHERE id = ?", (production_id,)).fetchone()
    if row is None or what not in ("thumb", "video"):
        abort(404)
    if what == "video" and request.args.get("download"):
        if not row["video_path"] or not Path(row["video_path"]).is_file():
            abort(404)
        name = re.sub(r"[^\w\- ]+", "", row["title"] or "Studio post").strip()[:60] or "Studio post"
        return send_file(row["video_path"], as_attachment=True,
                         download_name=f"{name} v{row['version']}{Path(row['video_path']).suffix}")
    return _file_response(row["thumb_path"] if what == "thumb" else row["video_path"])


# ── Planning board ────────────────────────────────────────────────────────────

from plan_routes import bp as _plan_bp  # noqa: E402 - its own module; shares webutil, not this file
from planner_site import bp as _planner_bp  # noqa: E402 - the online planner at /plan/
from uploads import bp as _uploads_bp  # noqa: E402 - footage uploaded from the planner to a shot
from ig_signin import bp as _signin_bp  # noqa: E402 - the owner's Instagram sign-in for reference downloads

app.register_blueprint(_plan_bp)
app.register_blueprint(_planner_bp)
app.register_blueprint(_uploads_bp)
app.register_blueprint(_signin_bp)


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
    from waitress import serve
    log.info("Dashboard on http://%s:%s", DASHBOARD_HOST, DASHBOARD_PORT)
    for attempt in range(30):  # after an update restart, the old worker may still hold the port briefly
        try:
            serve(app, host=DASHBOARD_HOST, port=DASHBOARD_PORT, threads=8, _quiet=True)
            return
        except OSError as e:
            if attempt == 29:
                log.error("Dashboard couldn't start: %s", e)
                return
            time.sleep(1)


if __name__ == "__main__":
    settings.ensure_folders()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    run()
