"""
The Brand kit page's server side (Review > More > Brand kit; brand_kit.py): read and save the kit, add or remove a
font or the logo, and the live preview.

The preview is the real thing: a Reel frame laid out by the editor's own overlay page (editor.render_overlay with
keep=, so nothing is rendered) and slides by the carousel's own page (carousel.page_html), in the kit being edited
(not yet saved), with sample words. Each is written to Studio/BrandKit/previews/<hash>/ and the page shows it in a
frame; its show(n) picks the moment (the hook, a callout, a card, the end card) or the slide.

Everyone signed in can see the kit; only Bryan changes it (style is his call, studio/CLAUDE.md), unless
STUDIO_BRAND_KIT_TEAM=1 lets the team too.
"""
import hashlib
import json
import logging
import mimetypes
import re
import shutil
import threading
import time
from pathlib import Path

from flask import Blueprint, abort, g, jsonify, request, send_file

import brand_kit
import settings
from webutil import body, is_owner

log = logging.getLogger("studio.brand_kit_routes")

bp = Blueprint("brand_kit", __name__)
PREVIEWS_KEPT = 24
_preview_lock = threading.Lock()


def can_edit() -> bool:
    return is_owner() or (settings.BRAND_KIT_TEAM and bool(getattr(g, "user", None)))


def _who() -> str:
    try:
        return str(g.user["name"] or "")
    except (AttributeError, KeyError, TypeError, IndexError):
        return ""


def _refuse():
    return jsonify({"error": "Only Bryan can change the brand kit."}), 403


def _view(data: dict | None = None) -> dict:
    v = brand_kit.view(data)
    v["editable"] = can_edit()
    return v


@bp.get("/api/brand-kit")
def get_kit():
    response = jsonify(_view())
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.post("/api/brand-kit")
def save_kit():
    if not can_edit():
        return _refuse()
    try:
        return jsonify(brand_kit.save(body(), _who()))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


def _upload() -> tuple[bytes, str]:
    f = request.files.get("file")
    if f is None:
        raise ValueError("Choose a file first.")
    raw = f.stream.read(brand_kit.FONT_MAX_BYTES + 1)
    return raw, f.filename or ""


@bp.post("/api/brand-kit/font")
def add_font():
    if not can_edit():
        return _refuse()
    request.max_content_length = brand_kit.FONT_MAX_BYTES + 64 * 1024
    try:
        raw, name = _upload()
        entry = brand_kit.add_font(raw, name, _who())
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"font": entry, **_view()})


@bp.post("/api/brand-kit/font/<fid>/remove")
def remove_font(fid: str):
    if not can_edit():
        return _refuse()
    if not re.fullmatch(r"[0-9a-f]{16}", fid):
        abort(404)
    try:
        brand_kit.remove_font(fid)
    except KeyError:
        abort(404)
    return jsonify(_view())


@bp.post("/api/brand-kit/logo")
def add_logo():
    if not can_edit():
        return _refuse()
    request.max_content_length = brand_kit.LOGO_MAX_BYTES + 64 * 1024
    try:
        raw, _ = _upload()
        name = brand_kit.add_logo(raw, _who())
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"logo": name, "url": f"/media/brand-kit/logo/{name}"})


@bp.get("/media/brand-kit/mark.svg")
def mark_file():
    """The Urban Makers mark the renders use when the kit has no logo of its own (render/carousel/um-mark.svg)."""
    import carousel
    return send_file(carousel.LOGO.resolve(), mimetype="image/svg+xml", max_age=86400)


@bp.get("/media/brand-kit/logo/<name>")
def logo_file(name: str):
    path = brand_kit.logo_file(name)
    if not path:
        abort(404)
    return send_file(path.resolve(), mimetype="image/png", max_age=365 * 86400)


# ── The preview ───────────────────────────────────────────────────────────────

# Sample words: what a Know-how Reel and carousel say (no client, no place).
HOOK = "Three things to check before you *tile*"
CHUNKS = [{"start": 0.2, "end": 3.2, "text": "The waterproofing membrane goes up the wall", "key": 2},
          {"start": 3.6, "end": 5.6, "text": "Count the ways on the board"},
          {"start": 6.0, "end": 8.4, "text": "Older flats get less power", "key": 3}]
GRAPHICS = [{"style": "callout", "start": 3.6, "duration": 2.0, "title": "Only 16-way", "italic": "16-way",
             "lines": ["This board, today"]},
            {"style": "card", "start": 6.0, "duration": 2.4, "title": "Power supply by home", "italic": "",
             "lines": ["Older HDB 30A", "HDB 40A", "Condo, landed 63A"]}]
END = {"start": 9.0, "duration": 1.8, "ask": "Save this for your *reno*", "handle": "@urbanmakers_sg"}
TOTAL = 10.8
MOMENTS = {"title": 2.4, "callout": 5.2, "card": 7.9, "end": 10.6}
SLIDES = [{"role": "cover", "heading": "Three things to check before you tile", "body": "Swipe for the list.",
           "image": {"source": "text"}},
          {"role": "point", "heading": "The membrane goes up the wall", "body":
           "At least 150 mm in the shower, so water can't creep behind the tiles.", "image": {"source": "text"}},
          {"role": "ask", "heading": "Save this for your reno", "body": "Follow for more site notes.",
           "image": {"source": "text"}}]

# The preview frame's own lines: footage-like ground under the overlay, and show(n).
_REEL_SHOW = """<style>html,body{background:#6f6a62}body{background:linear-gradient(168deg,#b4ab9c 0%,#8c8478 34%,
#5b564e 70%,#2f2d29 100%)}</style><script>
window.show=function(t){var tl=window.__timelines&&window.__timelines.overlay;
document.querySelectorAll('.clip').forEach(function(c){var s=+c.dataset.start,d=+c.dataset.duration;
c.style.visibility=(t>=s&&t<s+d)?'visible':'hidden'});if(tl)tl.seek(t,false);};
addEventListener('load',function(){document.fonts.ready.then(function(){show(+(location.hash.slice(1)||{T}));
document.documentElement.dataset.ready='1';});});
addEventListener('hashchange',function(){show(+location.hash.slice(1))});</script>"""
_SLIDE_SHOW = """<style>html,body{background:transparent;overflow:hidden}.slide{display:none}.slide.on{display:block}</style>
<script>window.show=function(n){document.querySelectorAll('.slide').forEach(function(s,i){
s.classList.toggle('on',i===n-1)});};addEventListener('load',function(){document.fonts.ready.then(function(){
if(window.studioFit)window.studioFit();show(+(location.hash.slice(1)||1));document.documentElement.dataset.ready='1';});});
addEventListener('hashchange',function(){show(+location.hash.slice(1))});</script>"""


def preview_dir() -> Path:
    return brand_kit.kit_dir() / "previews"


def build_preview(kit: dict) -> str:
    """The preview pages for a resolved kit, in previews/<hash>/ (made once per kit). Returns the hash."""
    import carousel
    import editor
    key = hashlib.sha256(json.dumps([kit, brand_kit.VERSION, 3], sort_keys=True).encode()).hexdigest()[:16]
    out = preview_dir() / key
    with _preview_lock:
        if (out / "reel" / "index.html").is_file() and (out / "slide" / "index.html").is_file():
            out.touch()
            return key
        tmp = preview_dir() / f".{key}.{time.time_ns()}"
        (tmp / "reel").mkdir(parents=True)
        (tmp / "slide").mkdir()
        try:
            look = editor.look_of_kit(kit)
            editor.render_overlay([dict(c) for c in CHUNKS], HOOK, TOTAL, tmp / "reel" / "x.webm",
                                  [dict(g) for g in GRAPHICS], editor.ACCENT["urban_makers"], style=look["titles"],
                                  font=look["caption_font"], end=dict(END), kit=kit, keep=tmp / "reel")
            page = (tmp / "reel" / "index.html").read_text(encoding="utf-8")
            page = page.replace("</head>", _REEL_SHOW.replace("{T}", str(MOMENTS["title"])) + "</head>", 1)
            (tmp / "reel" / "index.html").write_text(page, encoding="utf-8")
            slides = carousel.clean_slides(SLIDES)
            html = carousel.page_html(slides, [(None, "")] * len(slides), kit=kit)
            html = _local_files(html, tmp / "slide").replace("</head>", _SLIDE_SHOW + "</head>", 1)
            (tmp / "slide" / "index.html").write_text(html, encoding="utf-8")
            if out.exists():
                shutil.rmtree(out, ignore_errors=True)
            tmp.rename(out)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        _prune()
    return key


_FILE_URI = re.compile(r'(?:url\(|src=)"(file://[^"]+)"')


def _local_files(html: str, folder: Path) -> str:
    """The carousel page names its fonts and logo by file:// address; the preview copies each next to it. Only
    Studio's own fonts (render/) and the kit's files (Studio/BrandKit/) are copied."""
    from urllib.parse import unquote, urlparse
    allowed = [brand_kit.render.RENDER_DIR.resolve(), brand_kit.kit_dir().resolve()]
    names = {}

    def swap(m):
        uri = m.group(1)
        path = Path(unquote(urlparse(uri).path)).resolve()
        if not any(path.is_relative_to(a) for a in allowed) or not path.is_file():
            return m.group(0).replace(uri, "")
        if uri not in names:
            names[uri] = f"f{len(names) + 1}{path.suffix.lower()}"
            shutil.copy(path, folder / names[uri])
        return m.group(0).replace(uri, names[uri])
    return _FILE_URI.sub(swap, html)


def _prune() -> None:
    try:
        dirs = sorted((p for p in preview_dir().iterdir() if p.is_dir() and not p.name.startswith(".")),
                      key=lambda p: p.stat().st_mtime, reverse=True)
        for old in dirs[PREVIEWS_KEPT:]:
            shutil.rmtree(old, ignore_errors=True)
    except OSError:
        pass


@bp.post("/api/brand-kit/preview")
def preview():
    """The preview of the kit as it's being edited ({kit: as for saving, kind}): its pages' addresses, the kit it
    resolves to and what it would draw badly."""
    b = body()
    kind = b.get("kind") if b.get("kind") in brand_kit.KINDS else None
    try:
        data = brand_kit.merged(b.get("kit") if isinstance(b.get("kit"), dict) else {}, brand_kit.load())
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    kit = brand_kit.resolve(kind, data=data)
    try:
        key = build_preview(kit)
    except Exception as e:
        log.warning("Brand kit preview failed: %s: %s", type(e).__name__, e)
        return jsonify({"error": "The preview couldn't be made. Try again."}), 500
    return jsonify({"reel": f"/media/brand-kit/preview/{key}/reel/index.html",
                    "slide": f"/media/brand-kit/preview/{key}/slide/index.html", "moments": MOMENTS,
                    "slides": len(SLIDES), "resolved": kit, "warnings": brand_kit.warnings(kit)})


@bp.get("/media/brand-kit/preview/<key>/<part>/<name>")
def preview_file(key: str, part: str, name: str):
    if not re.fullmatch(r"[0-9a-f]{16}", key) or part not in ("reel", "slide") or \
            not re.fullmatch(r"[A-Za-z0-9_.-]{1,60}", name) or name.startswith("."):
        abort(404)
    path = preview_dir() / key / part / name
    if not path.is_file():
        abort(404)
    mime = mimetypes.guess_type(name)[0] or ("font/woff2" if name.endswith(".woff2") else "application/octet-stream")
    response = send_file(path.resolve(), mimetype=mime, max_age=0 if name.endswith(".html") else 86400)
    if name.endswith(".html"):
        response.headers["Cache-Control"] = "no-cache"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"   # shown in the Brand kit page's frame
    return response
