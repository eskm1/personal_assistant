"""
The Instagram sign-in Studio downloads references with (cloud/CONTRACT.md,
"Instagram sign-in for downloads"): a cookies.txt exported from a browser signed
in to a spare Instagram account, uploaded by the owner on the planner's
References page.

  GET    /api/uploads/instagram-signin   -> {present, added_at, expires}
  PUT    /api/uploads/instagram-signin   the cookies.txt file (text) -> the same
  DELETE /api/uploads/instagram-signin   removes it

Owner only, with the planner session like the upload API (uploads.guard: Bearer
token checked with Supabase, the planner's own address). The file goes straight
to this machine as ROOT/cookies.txt (style_study.COOKIES_FILE, readable by Studio
only), keeping only instagram.com cookies; it is never sent anywhere else, never
stored online and never logged. yt-dlp and gallery-dl read it for downloads.
"""
import logging
import os
import time
from datetime import datetime, timezone

from flask import Blueprint, g, jsonify, request

log = logging.getLogger("studio.signin")
bp = Blueprint("ig_signin", __name__)

MAX_BYTES = 256 << 10
PATH = "/api/uploads/instagram-signin"


def _file():
    import style_study
    return style_study.COOKIES_FILE


def _owner():
    user = getattr(g, "planner_user", None)
    if not user or user.get("role") != "owner":
        return jsonify({"error": "Only the owner can change Studio's Instagram sign-in"}), 403
    return None


def parse(text: str) -> tuple[list[str], dict]:
    """The instagram.com lines of a Netscape cookies.txt and what they say: {session
    (a signed-in account), expires (the sign-in's expiry, ISO date or None)}. Raises
    ValueError with a plain reason when it isn't one."""
    keep, session, expires = [], False, None
    for raw in text.replace("\r\n", "\n").split("\n"):
        line = raw.strip("\n")
        if not line.strip() or (line.startswith("#") and not line.startswith("#HttpOnly_")):
            continue
        parts = line.split("\t")
        if len(parts) != 7:
            continue
        domain = parts[0].removeprefix("#HttpOnly_").lower().lstrip(".")
        if domain != "instagram.com" and not domain.endswith(".instagram.com"):
            continue  # another site's cookies: not kept
        keep.append(line)
        if parts[5] == "sessionid" and parts[6].strip():
            session = True
            try:
                stamp = int(parts[4])
                if stamp > 0:
                    expires = datetime.fromtimestamp(stamp, timezone.utc).date().isoformat()
            except (ValueError, OverflowError, OSError):
                pass
    if not keep:
        raise ValueError("That isn't a cookies.txt with Instagram in it. Export it with the extension while "
                         "instagram.com is open and signed in.")
    if not session:
        raise ValueError("The file has no Instagram sign-in in it: sign in to the spare account on "
                         "instagram.com first, then export cookies.txt again.")
    return keep, {"session": True, "expires": expires}


def _status() -> dict:
    path = _file()
    try:
        st = path.stat()
    except OSError:
        return {"present": False}
    try:
        _, meta = parse(path.read_text(encoding="utf-8", errors="replace"))
    except ValueError:
        meta = {"session": False, "expires": None}
    return {"present": True, "added_at": datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat(timespec="seconds"),
            "expires": meta.get("expires"), "signed_in": meta.get("session", False)}


def save(text: str) -> dict:
    """Keep the Instagram lines, readable by this user only, replacing an old file."""
    lines, _ = parse(text)
    path = _file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("# Netscape HTTP Cookie File\n# Studio's Instagram sign-in for downloads (instagram.com only)\n")
            fh.write("\n".join(lines) + "\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    os.utime(path, (time.time(), time.time()))
    return _status()


@bp.get(PATH)
def status():
    return _owner() or jsonify(_status())


@bp.put(PATH)
def upload():
    refused = _owner()
    if refused:
        return refused
    if (request.content_length or 0) > MAX_BYTES:
        return jsonify({"error": "That file is too big for a cookies.txt"}), 413
    data = request.get_data(cache=False)
    if len(data) > MAX_BYTES:
        return jsonify({"error": "That file is too big for a cookies.txt"}), 413
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return jsonify({"error": "That isn't a cookies.txt (it should be a plain text file)"}), 400
    try:
        out = save(text)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    log.info("Instagram sign-in for downloads replaced by %s", g.planner_user.get("name") or "the owner")
    return jsonify(out)


@bp.delete(PATH)
def remove():
    refused = _owner()
    if refused:
        return refused
    _file().unlink(missing_ok=True)
    log.info("Instagram sign-in for downloads removed by %s", g.planner_user.get("name") or "the owner")
    return jsonify({"present": False})
