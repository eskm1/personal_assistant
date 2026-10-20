"""
Studio's pages from far away. Bryan opens Studio on his phone in Singapore and the server is in
Europe, about 200 ms a round trip, so what costs time is asking the server again, not the bytes.

- Every page names its scripts, styles, fonts and icons with ?v=<hash of that file> (and of what it
  imports), so a browser keeps them for a year and asks again only for the files an update
  changed. The pages themselves are always checked, so an update shows on the next open.
- The planner's modules name each other the same way (and index.html preloads them all), so they
  come in one go instead of one level of imports after another.
- Scripts, styles, pages and JSON go out gzipped when the browser takes it (video and pictures never).

The planner on Netlify is the same files without any of this: its index.html preloads the
modules too, and Netlify checks every file on each visit (it's close by, in Singapore).
"""
import gzip
import hashlib
import re
import threading
import time
from pathlib import Path

from flask import Response, request

HERE = Path(__file__).resolve().parent
PLANNER = HERE / "cloud" / "planner"
WEB = HERE / "web"
# Review's /static/ serves these from the planner's folder (planner_site.py): one copy of each.
SHARED = {"shell.js", "shell.css", "theme.css", "post-ui.js", "post-ui.css", "help.js", "help.css", "capture.js"}

TEXT = {".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".svg": "image/svg+xml"}
TYPES = {**TEXT, ".woff2": "font/woff2", ".png": "image/png", ".ico": "image/x-icon", ".webp": "image/webp",
         ".jpg": "image/jpeg", ".html": "text/html; charset=utf-8"}
# Compressed when the browser accepts gzip and it's bigger than a packet or so.
GZIP_TYPES = ("application/json", "text/html", "text/javascript", "text/css", "image/svg+xml",
              "application/manifest+json", "text/plain")
GZIP_MIN = 1400
YEAR = "public, max-age=31536000, immutable"

_ATTR = re.compile(r'''(\s(?:href|src)=")([^"#?:]+?\.(?:css|js|woff2|png|ico|svg|webp))"''')
_IMPORT = re.compile(r'''(\b(?:from|import)\s*\(?\s*)(["'])(\.{1,2}/[^"'?]+?\.js)\2''')
_URL = re.compile(r'''url\((["']?)([^"')?:]+?\.(?:woff2|png|svg|webp|jpg))\1\)''')

_lock = threading.Lock()
_state = {"checked": 0.0, "print": None}
_keys: dict[Path, str] = {}
_bodies: dict[tuple, tuple[bytes, str]] = {}   # (path, key, gz) -> (body, etag)


def _fresh() -> None:
    """Forget every hash once a file changed (looked at once a second at most): an update."""
    now = time.monotonic()
    if now - _state["checked"] < 1:
        return
    h = hashlib.sha1()
    for root in (PLANNER, WEB):
        for p in sorted(root.rglob("*")):
            if p.suffix.lower() in TYPES and p.is_file():
                st = p.stat()
                h.update(f"{p}|{st.st_size}|{st.st_mtime_ns}\n".encode())
    with _lock:
        _state["checked"] = now
        if h.digest() != _state["print"]:
            _state["print"] = h.digest()
            _keys.clear()
            _bodies.clear()


def file_for(ref: str, base: Path) -> Path | None:
    """The file behind an address on a page or in a file: /static/..., /plan/... or relative to
    the folder of whatever names it."""
    if ref.startswith("/static/"):
        name = ref[len("/static/"):]
        path = PLANNER / name if name in SHARED or name.startswith("fonts/") else WEB / name
    elif ref.startswith("/plan/"):
        path = PLANNER / ref[len("/plan/"):]
    elif ref.startswith("/"):
        return None
    else:
        path = base / ref
    path = path.resolve()
    ok = (path.is_relative_to(PLANNER) or path.is_relative_to(WEB)) and path.is_file()
    return path if ok else None


def _refs(path: Path) -> list[Path]:
    """What a script imports or a stylesheet loads."""
    suffix = path.suffix.lower()
    if suffix not in (".js", ".css"):
        return []
    text = path.read_text(encoding="utf-8")
    found = [m[3] for m in _IMPORT.finditer(text)] if suffix == ".js" else [m[2] for m in _URL.finditer(text)]
    return [p for p in (file_for(r, path.parent) for r in found) if p]


def key(path: Path) -> str:
    """A short hash of the file and of everything it imports, all the way down (imports may go
    round in a circle; the set of files is the same from anywhere in it)."""
    _fresh()
    path = path.resolve()
    if path in _keys:
        return _keys[path]
    seen, todo = set(), [path]
    while todo:
        p = todo.pop()
        if p not in seen:
            seen.add(p)
            todo.extend(_refs(p))
    h = hashlib.sha1()
    for p in sorted(seen):
        h.update(f"{p.relative_to(HERE)}|".encode() + hashlib.sha1(p.read_bytes()).digest())
    with _lock:
        _keys[path] = h.hexdigest()[:10]
    return _keys[path]


def _stamp(ref: str, base: Path) -> str:
    target = file_for(ref, base)
    return f"{ref}?v={key(target)}" if target else ref


def stamp_html(text: str, base: Path = PLANNER) -> str:
    """A page with ?v= on its own scripts, styles, fonts and icons (not other sites, not the
    manifest, which isn't one of ours to keep). base: the folder its relative addresses are in."""
    return _ATTR.sub(lambda m: f'{m[1]}{_stamp(m[2], base)}"', text)


def with_sw(page: str) -> str:
    """Pages served by Studio ask for the service worker (shell.js registers it, web/sw.js); the
    planner on Netlify doesn't."""
    import settings
    return page.replace('<html lang="en"', '<html lang="en" data-sw="/sw.js"', 1) if settings.SERVICE_WORKER else page


def _body(path: Path, gz: bool) -> tuple[bytes, str]:
    k = key(path)
    hit = _bodies.get((path, k, gz))
    if hit:
        return hit
    data = path.read_bytes()
    suffix = path.suffix.lower()
    if suffix == ".js":
        text = _IMPORT.sub(lambda m: f"{m[1]}{m[2]}{_stamp(m[3], path.parent)}{m[2]}", data.decode("utf-8"))
        data = text.encode("utf-8")
    elif suffix == ".css":
        text = _URL.sub(lambda m: f"url({m[1]}{_stamp(m[2], path.parent)}{m[1]})", data.decode("utf-8"))
        data = text.encode("utf-8")
    etag = f"{k}{'-gz' if gz else ''}"
    if gz:
        data = gzip.compress(data, compresslevel=6, mtime=0)
    with _lock:
        _bodies[(path, k, gz)] = (data, etag)
    return data, etag


def accepts_gzip() -> bool:
    return "gzip" in (request.headers.get("Accept-Encoding") or "").lower()


def send(path: Path, cache: str = "no-cache") -> Response:
    """One of the pages' files, as it is now (its imports and fonts stamped). Asked for with its
    current ?v=, it's kept for a year; without (or an old one), checked each time."""
    path = path.resolve()
    suffix = path.suffix.lower()
    gz = suffix in TEXT and accepts_gzip() and path.stat().st_size > GZIP_MIN
    data, etag = _body(path, gz)
    keep = request.args.get("v") == key(path)
    headers = {"Content-Type": TYPES.get(suffix, "application/octet-stream"), "ETag": f'"{etag}"',
               "Cache-Control": YEAR if keep else cache, "Vary": "Accept-Encoding"}
    if gz:
        headers["Content-Encoding"] = "gzip"
    if request.if_none_match.contains(etag):
        return Response(status=304, headers=headers)
    return Response(data, headers=headers)


def compress(response: Response) -> Response:
    """after_request: JSON and pages made by the app, gzipped (files and video pass untouched)."""
    if (response.direct_passthrough or response.is_streamed or response.status_code != 200
            or "Content-Encoding" in response.headers or not accepts_gzip()):
        return response
    if not (response.mimetype or "").startswith(GZIP_TYPES):
        return response
    data = response.get_data()
    if len(data) < GZIP_MIN:
        return response
    response.set_data(gzip.compress(data, compresslevel=5, mtime=0))
    response.headers["Content-Encoding"] = "gzip"
    response.vary.add("Accept-Encoding")
    if response.headers.get("ETag") and not response.headers["ETag"].startswith("W/"):
        response.headers["ETag"] = "W/" + response.headers["ETag"]
    return response


def etag_json(response: Response) -> Response:
    """A JSON answer that's polled (Review's board every 10 s): checked with an ETag, so an
    unchanged board is a few bytes (304) instead of the whole list."""
    if response.status_code != 200 or response.direct_passthrough:
        return response
    response.add_etag()
    response.headers["Cache-Control"] = "no-cache"
    return response.make_conditional(request)
