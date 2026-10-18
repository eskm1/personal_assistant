"""
Footage uploaded from the planner straight to a shot (cloud/CONTRACT.md, "Uploading
footage to a shot").

The planner sends a video in 8 MB pieces, so a phone on site can upload a long clip
and carry on after a dropped connection:

  POST   /api/uploads               {shot_id, name, size, type} -> 201 {id, offset, chunk}
  PUT    /api/uploads/<id>?offset=n raw bytes (16 MB at most)   -> {offset} (409 {offset} if n is off)
  GET    /api/uploads/<id>                                      -> {offset, size, state}
  DELETE /api/uploads/<id>                                      cancels an unfinished one

Carousels use the same pieces for photos (cloud/CONTRACT.md, "Carousels"):

  POST   /api/uploads               {idea_id, name, size, type} (a .jpg/.png/.heic photo for a
                                     carousel slide) -> 201 {id, offset, chunk}; the PUTs as above;
                                     once complete it's kept as a JPEG (HEIC converted) and the answer
                                     (and GET) carry photo: {id, thumb_path, width, height}
  POST   /api/uploads/stills        {clip_id, at} -> {clip_id, at, thumb_path}: the sharpest frame
                                     near that moment of a library clip, for a slide
  GET    /api/uploads/clips/<id>/moments -> {id, title, duration, moments, frames}: the clip's
                                     best moments and a small frame every few seconds, to pick from

Every request carries the planner session (Authorization: Bearer <access token>),
checked with Supabase (cloud_auth.verify). Pieces collect in ROOT/Uploads/<id>.part
next to a small <id>.json. A finished file moves to Inbox/<project>/<post folder>/
and is pinned to its shot (catalogue table upload_pins): the worker ingests it like
any other clip, then makes a manual take for that shot (attach()). Each upload is
mirrored in the planner's shot_uploads table (uploading -> received -> ingested |
failed); sync() (from cloud_sync, every tick) catches up whatever couldn't be
written at the time and clears out uploads abandoned for 3 days.
"""
import json
import logging
import re
import shutil
import threading
import time
import uuid
from collections import deque
from pathlib import Path

from flask import Blueprint, g, jsonify, request

import auth
import catalog
import cloud_auth
import cloud_sync
import settings
import slide_images
from webutil import body, client_ip, db, own_origin

log = logging.getLogger("studio.uploads")
bp = Blueprint("uploads", __name__)

CHUNK = 8 << 20             # what the planner sends per PUT
MAX_PUT = 16 << 20          # the most one PUT may carry
MAX_SIZE = 8 << 30          # biggest upload
MIN_FREE = 5 << 30          # disk that must stay free after an upload
STALE_SECONDS = 3 * 86400   # an unfinished upload untouched this long is removed
MAX_OPEN = 20               # unfinished uploads per person
MAX_PHOTO = 60 << 20        # biggest photo for a carousel slide
# Requests per person per minute (a PUT is 8 MB, so 600 is ~80 MB/s) and new uploads per 10 minutes.
RATE = {"any": (600, 60), "new": (60, 600)}
_rate: dict[tuple[str, str], deque] = {}
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()
_last_clean = 0.0


# ── Guard: the planner session and CORS ──────────────────────────────────────

def allowed_origins() -> set[str]:
    out = set(settings.UPLOAD_ORIGINS)
    if settings.PLANNER_URL:
        out.add("/".join(settings.PLANNER_URL.split("/")[:3]))
    return out


def _cors_ok(origin: str | None) -> bool:
    return bool(origin) and (origin.rstrip("/") in allowed_origins() or own_origin(origin))


def cors(response):
    """CORS headers for the planner's own address (dashboard.py adds these to every
    /api/uploads answer)."""
    origin = request.headers.get("Origin")
    if origin and origin.rstrip("/") in allowed_origins():
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE"
        response.headers["Access-Control-Max-Age"] = "600"
        response.headers.add("Vary", "Origin")
    return response


def _error(status: int, message: str, **extra):
    return jsonify({"error": message, **extra}), status


def _limited(user_id: str, kind: str) -> bool:
    limit, window = RATE[kind]
    now = time.time()
    q = _rate.setdefault((user_id, kind), deque())
    while q and now - q[0] > window:
        q.popleft()
    if len(q) >= limit:
        return True
    q.append(now)
    return False


def guard():
    """Run before every /api/uploads request (dashboard.py). None lets it through to
    the route with g.planner_user set; anything else is the answer."""
    origin = request.headers.get("Origin")
    if request.method == "OPTIONS":   # CORS preflight: no token on these
        return ("", 204) if _cors_ok(origin) else _error(403, "this address may not upload to Studio")
    if origin and not _cors_ok(origin):
        return _error(403, "this address may not upload to Studio")
    header = request.headers.get("Authorization", "")
    if not header.lower().startswith("bearer "):
        return _error(401, "Sign in to the planner again")
    if auth._throttled(f"token-ip:{client_ip()}"):
        return _error(429, "Too many failed sign-ins. Try again in 15 minutes.")
    try:
        user = cloud_auth.verify(header[7:])
    except cloud_auth.AuthError as e:
        if e.status == 401:
            auth._failed(f"token-ip:{client_ip()}")
        return _error(e.status, str(e))
    if not auth.email_allowed(user["email"], user["role"]):
        return _error(403, auth.domain_hint())
    if _limited(user["id"], "any"):
        response, code = _error(429, "Too many requests: slow down a little")
        response.headers["Retry-After"] = "30"
        return response, code
    g.planner_user = user
    if request.method == "PUT":
        request.max_content_length = MAX_PUT
    return None


# ── Upload state (ROOT/Uploads/<id>.json + <id>.part) ────────────────────────

def _folder() -> Path:
    settings.UPLOADS.mkdir(parents=True, exist_ok=True)
    return settings.UPLOADS


def _valid_id(upload_id: str) -> bool:
    try:
        return str(uuid.UUID(upload_id)) == upload_id
    except ValueError:
        return False


def _load(upload_id: str) -> dict | None:
    if not _valid_id(upload_id):
        return None
    try:
        return json.loads((_folder() / f"{upload_id}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _save(state: dict) -> None:
    path = _folder() / f"{state['id']}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state), encoding="utf-8")
    tmp.replace(path)


def _part(upload_id: str) -> Path:
    return _folder() / f"{upload_id}.part"


def _received(state: dict) -> int:
    try:
        return _part(state["id"]).stat().st_size
    except OSError:
        return 0


def _lock(upload_id: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(upload_id, threading.Lock())


def _all() -> list[dict]:
    out = []
    for f in _folder().glob("*.json"):
        state = _load(f.stem)
        if state:
            out.append(state)
    return out


def safe_name(name: str) -> str:
    """A file name every OS and OneDrive accepts, that the inbox doesn't skip
    (no leading dot or ~), keeping the extension."""
    name = Path(str(name).replace("\\", "/")).name
    name = re.sub(r'[<>:"/\\|?*#%\x00-\x1f]+', " ", name)
    stem, ext = Path(name).stem, Path(name).suffix
    stem = re.sub(r"\s+", " ", stem).strip(" .~")[:100].strip(" .") or "Upload"
    return stem + ext


def _shot(shot_id: int) -> dict | None:
    """The shot with its idea and project (from the planner), or None."""
    api = cloud_sync.Client()
    rows = api.select("shots", {"id": f"eq.{shot_id}",
                                "select": "id,idea_id,ideas(id,title,folder,project_id,projects(id,name,folder))"})
    return rows[0] if rows else None


def _destination(shot: dict, name: str) -> Path:
    """Inbox/<project folder>/<post folder>/<name> (just the project folder when the
    idea has no post folder yet), with (2), (3)... when the name is taken."""
    idea = shot.get("ideas") or {}
    project = idea.get("projects") or {}
    folder = settings.INBOX / (cloud_sync._safe_folder(project.get("folder")) or
                               cloud_sync._safe_folder(project.get("name")) or "Uploads")
    post = cloud_sync._safe_folder(idea.get("folder"))
    if post:
        folder = folder / post
    folder.mkdir(parents=True, exist_ok=True)
    target, n = folder / name, 2
    while target.exists():
        target = folder / f"{Path(name).stem} ({n}){Path(name).suffix}"
        n += 1
    return target


def _mirror(upload_id: str, values: dict) -> bool:
    """Write the upload's state to shot_uploads. False when that failed (sync() retries)."""
    try:
        cloud_sync.Client().update("shot_uploads", {"id": f"eq.{upload_id}"}, values)
        return True
    except cloud_sync.CloudError as e:
        log.warning("Couldn't update upload %s online: %s", upload_id, e)
        return False


# ── Routes ────────────────────────────────────────────────────────────────────

def _answer(state: dict, status: int = 200, **extra):
    return jsonify({"id": state["id"], "offset": _received(state) if state["state"] == "uploading" else state["size"],
                    "size": state["size"], "state": state["state"], **extra}), status


def _idea(idea_id: int) -> dict | None:
    """The idea (from the planner), or None."""
    rows = cloud_sync.Client().select("ideas", {"id": f"eq.{idea_id}", "select": "id,title,format,project_id"})
    return rows[0] if rows else None


def _start_photo(user: dict, data: dict):
    """A photo for a carousel slide: same pieces as a video, kept on the Studio machine."""
    try:
        idea_id, size = int(data.get("idea_id")), int(data.get("size"))
    except (TypeError, ValueError):
        return _error(400, "idea_id and size are needed")
    name = safe_name(str(data.get("name") or ""))
    kind = str(data.get("type") or "")
    if size <= 0:
        return _error(400, "the file is empty")
    if size > MAX_PHOTO:
        return _error(413, f"That photo is over {MAX_PHOTO >> 20} MB.")
    if not slide_images.is_photo(name, kind):
        return _error(415, "Only photos (JPEG, PNG or HEIC) can go on a slide")
    mine = [s for s in _all() if s["user"] == user["id"] and s["state"] == "uploading"]
    for s in mine:  # resume
        if s.get("idea_id") == idea_id and s["name"] == name and s["size"] == size:
            return jsonify({"id": s["id"], "offset": _received(s), "chunk": CHUNK}), 201
    if len(mine) >= MAX_OPEN:
        return _error(429, "You have too many unfinished uploads: finish or cancel some first")
    if _limited(user["id"], "new"):
        return _error(429, "Too many new uploads at once: try again in a few minutes")
    try:
        idea = _idea(idea_id)
    except cloud_sync.CloudError as e:
        log.warning("Photo upload not started: %s", e)
        return _error(502, "Can't reach the planner's database right now; try again")
    if idea is None:
        return _error(404, "That idea isn't in the planner any more")
    upload_id = str(uuid.uuid4())
    _part(upload_id).touch()
    _save({"id": upload_id, "idea_id": idea_id, "shot_id": None, "user": user["id"], "email": user["email"],
           "name": name, "size": size, "type": kind[:100], "state": "uploading", "created": catalog.now(),
           "path": None, "kind": "photo"})
    log.info("Photo upload %s started by %s: %s (%.1f MB) for idea %s", upload_id, user["email"], name, size / 1e6, idea_id)
    return jsonify({"id": upload_id, "offset": 0, "chunk": CHUNK}), 201


@bp.post("/api/uploads")
def start():
    user = g.planner_user
    data = body()
    if data.get("idea_id") is not None and data.get("shot_id") is None:
        cleanup()
        return _start_photo(user, data)
    try:
        shot_id, size = int(data.get("shot_id")), int(data.get("size"))
    except (TypeError, ValueError):
        return _error(400, "shot_id and size are needed")
    name = safe_name(str(data.get("name") or ""))
    kind = str(data.get("type") or "")
    if size <= 0:
        return _error(400, "the file is empty")
    if size > MAX_SIZE:
        return _error(413, f"That video is over {MAX_SIZE >> 30} GB. Trim it on the phone first.")
    if Path(name).suffix.lower() not in settings.VIDEO_EXTS or (kind and not kind.startswith("video/")
                                                                and kind != "application/octet-stream"):
        return _error(415, "Only videos can be uploaded to a shot (" + ", ".join(sorted(settings.VIDEO_EXTS)) + ")")
    cleanup()
    mine = [s for s in _all() if s["user"] == user["id"] and s["state"] == "uploading"]
    for s in mine:  # resume: same person, shot, name and size
        if s["shot_id"] == shot_id and s["name"] == name and s["size"] == size:
            return jsonify({"id": s["id"], "offset": _received(s), "chunk": CHUNK}), 201
    if len(mine) >= MAX_OPEN:
        return _error(429, "You have too many unfinished uploads: finish or cancel some first")
    if _limited(user["id"], "new"):
        return _error(429, "Too many new uploads at once: try again in a few minutes")
    if shutil.disk_usage(_folder()).free - size < MIN_FREE:
        return _error(507, "Studio's disk is too full for this video. Tell Bryan.")
    try:
        shot = _shot(shot_id)
        if shot is None:
            return _error(404, "That shot isn't in the planner any more")
        upload_id = str(uuid.uuid4())
        cloud_sync.Client().insert("shot_uploads", {"id": upload_id, "shot_id": shot_id, "file_name": name[:300],
                                                    "size": size, "state": "uploading", "uploaded_by": user["id"]},
                                   returning=False)
    except cloud_sync.CloudError as e:
        if cloud_sync._missing_table(e):
            return _error(503, "Studio's online database needs an update (cloud_setup.py --update)")
        log.warning("Upload not started: %s", e)
        return _error(502, "Can't reach the planner's database right now; try again")
    _part(upload_id).touch()
    state = {"id": upload_id, "shot_id": shot_id, "user": user["id"], "email": user["email"], "name": name,
             "size": size, "type": kind[:100], "state": "uploading", "created": catalog.now(),
             "path": None, "shot_info": shot}
    _save(state)
    log.info("Upload %s started by %s: %s (%.1f MB) for shot %s", upload_id, user["email"], name, size / 1e6, shot_id)
    return jsonify({"id": upload_id, "offset": 0, "chunk": CHUNK}), 201


def _mine(upload_id: str) -> dict | None:
    state = _load(upload_id)
    if state is None or (state["user"] != g.planner_user["id"] and g.planner_user["role"] != "owner"):
        return None
    return state


@bp.put("/api/uploads/<upload_id>")
def put(upload_id: str):
    state = _mine(upload_id)
    if state is None:
        return _error(404, "No such upload (it may have been cancelled)")
    try:
        offset = int(request.args.get("offset", ""))
    except ValueError:
        return _error(400, "offset is needed")
    length = request.content_length
    if length is None:
        return _error(411, "send the piece with a Content-Length")
    if length > MAX_PUT:
        return _error(413, f"send at most {MAX_PUT >> 20} MB per piece")
    with _lock(upload_id):
        state = _load(upload_id) or state
        if state["state"] != "uploading":
            return jsonify({"offset": state["size"], "state": state["state"]}), 409
        have = _received(state)
        if offset != have:
            return jsonify({"offset": have, "error": "the server has a different amount of this file"}), 409
        if have + length > state["size"]:
            return _error(400, "that's more than the file's size")
        if length and shutil.disk_usage(_folder()).free - length < MIN_FREE // 2:
            return _error(507, "Studio's disk is too full for this video. Tell Bryan.")
        with open(_part(upload_id), "r+b") as f:
            f.seek(have)
            left = length  # read exactly that much (reading past it counts as too large)
            while left and (chunk := request.stream.read(min(left, 1 << 20))):
                f.write(chunk)
                left -= len(chunk)
        have = _received(state)
        if have < state["size"]:
            return jsonify({"offset": have})
        error = _finish(state)
    if error:
        return _error(415 if state.get("kind") == "photo" else 500, error, offset=have)
    return jsonify({"offset": have, "state": "received", **({"photo": state["photo"]} if state.get("photo") else {})})


def _finish_photo(state: dict) -> str | None:
    """A whole photo: kept as a JPEG (HEIC converted), its preview uploaded for the planner."""
    part = _part(state["id"])
    try:
        photo = slide_images.save_photo(part)
    except ValueError as e:
        state.update(state="failed", error=str(e))
        _save(state)
        part.unlink(missing_ok=True)
        return str(e)
    thumb = f"photos/{photo['id']}.jpg"
    try:
        cloud_sync.Client().upload(thumb, slide_images.preview(photo["path"]))
    except cloud_sync.CloudError as e:
        log.warning("Photo %s kept, but its preview didn't upload: %s", photo["id"], e)
        thumb = None
    part.unlink(missing_ok=True)
    state.update(state="received", received=catalog.now(),
                 photo={"id": photo["id"], "thumb_path": thumb, "width": photo["width"], "height": photo["height"]})
    _save(state)
    log.info("Photo upload %s kept as photo %s (%dx%d)", state["id"], photo["id"], photo["width"], photo["height"])
    return None


def _finish(state: dict) -> str | None:
    """The whole file is here: move it into the inbox, pinned to its shot."""
    if state.get("kind") == "photo":
        return _finish_photo(state)
    try:
        try:
            shot = _shot(state["shot_id"]) or {}
        except cloud_sync.CloudError:
            shot = {}
        if not shot:
            shot = state.get("shot_info") or {}
        target = _destination(shot, state["name"])
        rel = target.relative_to(settings.INBOX).as_posix()
        conn = catalog.connect()
        try:
            conn.execute("""INSERT OR REPLACE INTO upload_pins (upload_id, path, shot_remote, user_id, state, created_at)
                            VALUES (?, ?, ?, ?, 'received', ?)""",
                         (state["id"], rel, state["shot_id"], state["user"], catalog.now()))
            conn.commit()
            try:
                shutil.move(str(_part(state["id"])), str(target))
            except OSError:
                conn.execute("DELETE FROM upload_pins WHERE upload_id = ?", (state["id"],))
                conn.commit()
                raise
            state.update(state="received", path=rel, received=catalog.now())
            _save(state)
            if _mirror(state["id"], {"state": "received"}):
                conn.execute("UPDATE upload_pins SET reported = 'received' WHERE upload_id = ?", (state["id"],))
                conn.commit()
        finally:
            conn.close()
    except OSError as e:
        log.exception("Upload %s couldn't be moved into the inbox", state["id"])
        return f"Studio couldn't save the file: {e}"
    log.info("Upload %s received: Inbox/%s (shot %s)", state["id"], rel, state["shot_id"])
    return None


@bp.get("/api/uploads/<upload_id>")
def status(upload_id: str):
    state = _mine(upload_id)
    if state is None:
        return _error(404, "No such upload")
    extra = {"photo": state["photo"]} if state.get("photo") else {}
    if state["state"] == "received" and state.get("kind") != "photo":
        pin = db().execute("SELECT state, clip_id, error FROM upload_pins WHERE upload_id = ?", (upload_id,)).fetchone()
        if pin and pin["state"] != "received":
            state = {**state, "state": pin["state"]}
            extra = {"clip_id": pin["clip_id"], "error": pin["error"]}
    return _answer(state, **extra)


@bp.delete("/api/uploads/<upload_id>")
def cancel(upload_id: str):
    state = _mine(upload_id)
    if state is None:
        return _error(404, "No such upload")
    with _lock(upload_id):
        state = _load(upload_id) or state
        if state["state"] != "uploading":
            return _error(409, "That upload has already finished")
        _remove(upload_id)
    if state.get("kind") == "photo":
        return jsonify({"ok": True})
    try:
        cloud_sync.Client().request("DELETE", "/rest/v1/shot_uploads", params={"id": f"eq.{upload_id}"})
    except cloud_sync.CloudError as e:
        log.warning("Couldn't remove cancelled upload %s online: %s", upload_id, e)
    log.info("Upload %s cancelled", upload_id)
    return jsonify({"ok": True})


# ── Pictures from footage, for carousel slides ───────────────────────────────

@bp.post("/api/uploads/stills")
def still():
    """The sharpest frame near a moment of a library clip, for a slide: kept full size on the
    Studio machine (carousel.py lays it out), a preview uploaded for the planner."""
    data = body()
    try:
        clip_id, at = int(data.get("clip_id")), float(data.get("at") or 0)
    except (TypeError, ValueError):
        return _error(400, "clip_id and at are needed")
    conn = catalog.connect()
    try:
        row = conn.execute("SELECT duration FROM clips WHERE id = ?", (clip_id,)).fetchone()
        if row is None:
            return _error(404, "That clip isn't in Studio's library")
        at = min(max(at, 0.0), max((row["duration"] or at) - 0.05, 0.0))
        try:
            path, taken = slide_images.clip_still(conn, clip_id, at)
        except slide_images.Unavailable as e:
            return _error(404, f"Studio couldn't take a still: {e}")
    finally:
        conn.close()
    thumb = f"stills/{clip_id}_{int(round(at * 1000))}.jpg"
    try:
        cloud_sync.Client().upload(thumb, slide_images.preview(path))
    except cloud_sync.CloudError as e:
        log.warning("Still of clip %s at %.1fs kept, but its preview didn't upload: %s", clip_id, at, e)
        return _error(502, "Can't reach the planner's storage right now; try again")
    # at stays the moment asked for (the still is cached by it); taken is the sharp frame's time
    return jsonify({"clip_id": clip_id, "at": round(at, 2), "taken": round(taken, 2), "thumb_path": thumb})


@bp.get("/api/uploads/clips/<int:clip_id>/moments")
def moments(clip_id: int):
    """A clip's best moments (Claude's tags) and its library frames as small images, so a
    slide's moment can be picked on a phone."""
    import base64
    row = db().execute("SELECT id, title, source_name, duration, moments, clip_dir FROM clips WHERE id = ?",
                       (clip_id,)).fetchone()
    if row is None:
        return _error(404, "That clip isn't in Studio's library")
    try:
        marks = [{"start": m.get("start"), "end": m.get("end"), "description": m.get("description", "")}
                 for m in json.loads(row["moments"] or "[]") if isinstance(m, dict)][:12]
    except ValueError:
        marks = []
    frames = []
    for t, frame in cloud_sync._frames(row["clip_dir"])[:24]:
        try:
            data = slide_images.preview(frame, 240)
        except OSError:
            continue
        frames.append({"at": t, "src": "data:image/jpeg;base64," + base64.b64encode(data).decode()})
    return jsonify({"id": row["id"], "title": row["title"] or row["source_name"], "duration": row["duration"],
                    "moments": marks, "frames": frames})


def _remove(upload_id: str) -> None:
    for suffix in (".part", ".json"):
        (_folder() / f"{upload_id}{suffix}").unlink(missing_ok=True)
    with _locks_guard:
        _locks.pop(upload_id, None)


def cleanup(api=None) -> int:
    """Remove uploads nobody has added to for 3 days (and the notes of finished
    ones that old). Returns how many unfinished ones went. Never raises."""
    global _last_clean
    now = time.time()
    if now - _last_clean < 600:
        return 0
    _last_clean = now
    n = 0
    try:
        for state in _all():
            part = _part(state["id"])
            try:
                touched = part.stat().st_mtime if part.exists() else \
                    (_folder() / f"{state['id']}.json").stat().st_mtime
            except OSError:
                continue
            if now - touched < STALE_SECONDS:
                continue
            _remove(state["id"])
            if state["state"] == "uploading" and state.get("kind") != "photo":
                n += 1
                log.info("Upload %s (%s) removed: unfinished for 3 days", state["id"], state["name"])
                try:
                    (api or cloud_sync.Client()).update(
                        "shot_uploads", {"id": f"eq.{state['id']}", "state": "eq.uploading"},
                        {"state": "failed", "error": "The upload wasn't finished within 3 days"})
                except cloud_sync.CloudError:
                    pass
        for part in _folder().glob("*.part"):   # a piece with no state file
            if not (_folder() / f"{part.stem}.json").exists() and now - part.stat().st_mtime > STALE_SECONDS:
                part.unlink(missing_ok=True)
    except OSError as e:
        log.warning("Couldn't tidy the uploads folder: %s", e)
    return n


def busy() -> bool:
    """Someone is uploading right now (a restart for an update should wait)."""
    try:
        return any(time.time() - p.stat().st_mtime < 120 for p in settings.UPLOADS.glob("*.part"))
    except OSError:
        return False


# ── Ingest: the pinned file becomes a take ───────────────────────────────────

def pin_for(conn, path: Path):
    """The pin of an inbox file uploaded to a shot, or None."""
    try:
        rel = Path(path).resolve().relative_to(settings.INBOX.resolve()).as_posix()
    except ValueError:
        return None
    return conn.execute("SELECT * FROM upload_pins WHERE path = ? AND state = 'received' ORDER BY created_at DESC",
                        (rel,)).fetchone()


def _take(conn, shot_remote: int, clip_id: int) -> bool:
    """A manual take of the clip for the shot (local ids). False when the shot isn't here yet."""
    shot = conn.execute("SELECT id, idea_id FROM shots WHERE remote_id = ?", (shot_remote,)).fetchone()
    if shot is None:
        return False
    duration = conn.execute("SELECT duration FROM clips WHERE id = ?", (clip_id,)).fetchone()[0]
    conn.execute(
        """INSERT INTO takes (shot_id, clip_id, start, end, source, note, created_at)
           VALUES (?, ?, 0, ?, 'manual', 'Uploaded to this shot', ?)
           ON CONFLICT (shot_id, clip_id) DO UPDATE SET source = 'manual', note = excluded.note
           WHERE takes.remote_id IS NULL""",
        (shot["id"], clip_id, duration, catalog.now()))
    conn.execute("UPDATE clips SET idea_id = COALESCE(idea_id, ?) WHERE id = ?", (shot["idea_id"], clip_id))
    conn.commit()
    return True


def attach(conn, pin, clip_id: int) -> None:
    """After tagging: the uploaded clip is a take of its shot."""
    made = _take(conn, pin["shot_remote"], clip_id)
    conn.execute("UPDATE upload_pins SET state = 'ingested', clip_id = ?, error = NULL WHERE upload_id = ?",
                 (clip_id, pin["upload_id"]))
    conn.commit()
    log.info("Upload %s is clip %s%s", pin["upload_id"], clip_id,
             ", a take of its shot" if made else " (its shot arrives with the next sync)")


def failed(conn, pin, error: str, clip_id: int | None = None) -> None:
    conn.execute("UPDATE upload_pins SET state = 'failed', error = ?, clip_id = ? WHERE upload_id = ?",
                 (error[:300], clip_id, pin["upload_id"]))
    conn.commit()


def retried(conn, clip_id: int) -> None:
    """A clip whose first processing failed worked on a retry: its uploads are takes now."""
    for pin in conn.execute("SELECT * FROM upload_pins WHERE clip_id = ? AND state = 'failed'", (clip_id,)).fetchall():
        attach(conn, pin, clip_id)


# ── Sync: mirror pins online (cloud_sync, every tick) ────────────────────────

def sync(api, conn) -> int:
    """Write each pin's state to shot_uploads once it changes: ingested (with the
    clip, once the clip is online, and the shot's filmed_at if empty) or failed.
    Also makes a take that couldn't be made at ingest (the shot wasn't here yet).
    Returns how many were written."""
    cleanup(api)
    n = 0
    for pin in conn.execute("SELECT * FROM upload_pins WHERE reported IS NULL OR reported != state").fetchall():
        if pin["state"] == "received":
            api.update("shot_uploads", {"id": f"eq.{pin['upload_id']}"}, {"state": "received"})
        elif pin["state"] == "failed":
            api.update("shot_uploads", {"id": f"eq.{pin['upload_id']}"},
                       {"state": "failed", "error": pin["error"] or "Studio couldn't use this video"})
        else:
            clip = conn.execute("SELECT cloud_hash FROM clips WHERE id = ?", (pin["clip_id"],)).fetchone()
            if clip is None:
                continue
            local = conn.execute("""SELECT t.id FROM takes t JOIN shots s ON s.id = t.shot_id
                                    WHERE s.remote_id = ? AND t.clip_id = ?""",
                                 (pin["shot_remote"], pin["clip_id"])).fetchone()
            if local is None and not _take(conn, pin["shot_remote"], pin["clip_id"]):
                if not api.select("shots", {"id": f"eq.{pin['shot_remote']}", "select": "id"}):
                    conn.execute("UPDATE upload_pins SET reported = state WHERE upload_id = ?", (pin["upload_id"],))
                    conn.commit()  # the shot was deleted meanwhile: nothing to attach to
                continue
            if not clip["cloud_hash"]:
                continue  # the clip goes online first (push_clips, next tick)
            api.update("shot_uploads", {"id": f"eq.{pin['upload_id']}"},
                       {"state": "ingested", "clip_id": pin["clip_id"], "error": None})
            api.update("shots", {"id": f"eq.{pin['shot_remote']}", "filmed_at": "is.null"},
                       {"filmed_at": catalog.now()})
        conn.execute("UPDATE upload_pins SET reported = state WHERE upload_id = ?", (pin["upload_id"],))
        conn.commit()
        n += 1
    return n
