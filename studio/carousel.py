"""
Carousels on the Studio machine (cloud/CONTRACT.md, "Carousels"): a post of 1080x1350
slides people swipe through, made without a shoot.

  1. The slides are written in the planner (Claude's "Write slides", then edited line by
     line): a cover (the hook), the points, and the ask, each with a picture: a still from
     Urban Makers' own footage, a photo uploaded from the phone, an AI concept picture
     (concepts.py; marked "Concept", never in a Finished homes post) or text only
     (slide_images.py).
  2. "Make this" (the owner) queues a `carousel` job; cloud_sync runs make(), which files a
     'planned' production (format 'carousel') for the editing queue, like a Reel.
  3. run() has the concept pictures drawn and checked (concepts.prepare: two options each,
     cached, so nothing is drawn twice), then lays it out: each slide becomes a page in the Urban Makers look (the bundled
     fonts in render/carousel/fonts, the brand green and amber), and the headless Chrome the
     editor already uses saves each one as a PNG (render/carousel/shoot.mjs). The cover's
     words stay inside the middle of the slide, so the profile grid's 3:4 crop keeps them.
  4. Studio's read (draft_review.py) looks at the slides; Bryan approves, rejects or sends
     it back with notes (a note can name a slide). A send-back has Claude revise the slides
     from the notes (revise()) and lays them out again as the next version. Picking the
     other concept option (choose()) lays the same version out again without drawing;
     Redraw in Review (redraw()) draws one slide's pictures again as the next version.
  5. Approved carousels are copied to OneDrive "Studio Ready to post/<title>/" as 01.png,
     02.png ... and caption.txt (onedrive.py), and Review offers them as a .zip.

English only. Only the offers listed in the planner, never a made-up price or promotion,
and never a place name.
"""
import html
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

import catalog
import concepts
import render
import settings
import slide_images
import wording

log = logging.getLogger("studio.carousel")

W, H = 1080, 1350
# Instagram's profile grid shows a 3:4 crop of each post: on a 1080x1350 slide that's the
# middle 1012 px across. The cover's words must stay inside it.
GRID_LEFT, GRID_RIGHT = round((W - H * 3 / 4) / 2), round(W - (W - H * 3 / 4) / 2)
MAX_SLIDES = 12
ROLES = ("cover", "point", "ask")
RENDERS = settings.ROOT / "Renders"
HERE = render.RENDER_DIR / "carousel"
FONTS = {"display": HERE / "fonts" / "BricolageGrotesque-latin.woff2", "text": HERE / "fonts" / "Figtree-latin.woff2"}
SHOOT = HERE / "shoot.mjs"
LOGO = HERE / "um-mark.svg"
# The Urban Makers look: the green Bryan approved in the content plan, warm white, and the
# amber accent Studio's Reels graphics use.
COLOURS = {"bg": "#22302c", "deep": "#18231f", "ink": "#f6f4ee", "soft": "#d6ddd8", "accent": "#f5a524"}
# Heading and body sizes (px at 1080 wide) by layout; each shrinks to fit (down to FIT_MIN).
SIZES = {"cover-text": (120, 48), "cover-photo": (106, 44), "point-text": (96, 52), "point-photo": (70, 42),
         "ask-text": (108, 54), "ask-photo": (70, 44)}
FIT_MIN = 0.56
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
_chrome: str | None = None


def ready() -> str | None:
    """None when slides can be laid out here, else what's missing."""
    try:
        render.node_version()
    except Exception as e:
        return f"Node.js {e}"
    if not (render.RENDER_DIR / "node_modules" / "puppeteer-core" / "package.json").exists():
        return f"the renderer's packages are out of date; run npm ci in {render._RENDER_DIR}"
    missing = [p.name for p in (*FONTS.values(), SHOOT) if not p.exists()]
    if missing:
        return "carousel files are missing: " + ", ".join(missing)
    try:
        _browser()
    except Exception as e:
        return str(e)
    return None


def _browser() -> str:
    global _chrome
    if _chrome is None or not Path(_chrome).exists():
        _chrome = render.browser_path()
    return _chrome


# ── Slides ────────────────────────────────────────────────────────────────────

def _text(v, limit: int) -> str:
    return v.strip()[:limit].strip() if isinstance(v, str) else ""


def _key() -> str:
    return uuid.uuid4().hex[:10]


def clean_image(v) -> dict:
    """A picture as the planner stores it (what valid_slides() online accepts)."""
    i = v if isinstance(v, dict) else {}
    source = i.get("source") if i.get("source") in slide_images.SOURCES or i.get("source") == "text" else "text"
    out = {"source": source}
    if source == "clip" and isinstance(i.get("clip_id"), int) and not isinstance(i.get("clip_id"), bool):
        out["clip_id"] = i["clip_id"]
        out["at"] = round(max(float(i.get("at") or 0), 0.0), 1) if isinstance(i.get("at"), (int, float)) else 0.0
    elif source == "photo" and isinstance(i.get("photo_id"), str) and slide_images._ID.match(i["photo_id"]):
        out["photo_id"] = i["photo_id"]
    elif source in ("clip", "photo"):
        out = {"source": "text"}
    elif source == "concept":
        out.update(clean_concept(i))
    if isinstance(i.get("thumb_path"), str) and len(i["thumb_path"]) <= 200 and \
            i["thumb_path"].split("/")[0] in ("clips", "takes", "stills", "photos") and ".." not in i["thumb_path"]:
        out["thumb_path"] = i["thumb_path"]
    note = _text(i.get("note"), 300)
    if note:
        out["note"] = note
    return out


def clean_concept(i: dict) -> dict:
    """A concept picture's own fields (like _shared/slides.ts cleanImage): its brief, the
    cache key of its pictures, the options that passed, the one picked, a redraw's change."""
    out = {}
    for k, limit in (("brief", 2000), ("previous", 2000), ("change", 300), ("why", 300)):
        v = _text(i.get(k), limit)
        if v:
            out[k] = v
    if isinstance(i.get("concept"), str) and concepts._HASH.match(i["concept"]):
        out["concept"] = i["concept"]
        options = [o for o in concepts.OPTIONS if o in (i.get("options") or [])]
        if options:
            out["options"] = options
            if i.get("chosen") in options:
                out["chosen"] = i["chosen"]
    if isinstance(i.get("round"), int) and not isinstance(i.get("round"), bool) and 0 <= i["round"] <= 50:
        out["round"] = i["round"]
    return out


def text_only(image: dict) -> dict:
    """A slide's picture taken away (its note on what would suit stays)."""
    return {"source": "text", **({"note": image["note"]} if image.get("note") else {})}


def clean_slides(raw) -> list[dict]:
    """The slides stored on an idea, cleaned; empty slides are left out."""
    out = []
    for s in (raw if isinstance(raw, list) else [])[:MAX_SLIDES]:
        if not isinstance(s, dict):
            continue
        # The safe fixes (wording.tidy: invisible characters, em dashes...) on words written online too.
        heading, body = wording.tidy(_text(s.get("heading"), 200)), wording.tidy(_text(s.get("body"), 800))
        if not heading and not body:
            continue
        out.append({"key": _text(s.get("key"), 40) or _key(), "role": s.get("role") if s.get("role") in ROLES else "point",
                    "heading": heading, "body": body, "image": clean_image(s.get("image"))})
    return out


def plan_slides(raw_slides, current: list[dict], clips: dict[int, dict], new_concepts: bool = False,
                finished: bool = False) -> list[dict]:
    """Claude's slides as stored slides (like _shared/slides.ts planSlides): one cover first,
    the ask last, a picture only when it's one the team picked (keep_image), a still from a
    listed clip, or a concept picture (new ones only when Studio can draw them; never in a
    Finished homes post), at most MAX_SLIDES. A kept concept with a redraw is drawn again."""
    slides = []
    for s in (raw_slides if isinstance(raw_slides, list) else [])[:MAX_SLIDES * 2]:
        if not isinstance(s, dict):
            continue
        heading, body = _text(s.get("heading"), 200), _text(s.get("body"), 800)
        if not heading and not body:
            continue
        note = _text(s.get("image_idea"), 300)
        keep = round(s["keep_image"]) if isinstance(s.get("keep_image"), (int, float)) else 0
        clip = clips.get(s.get("clip_id")) if isinstance(s.get("clip_id"), int) else None
        kept = current[keep - 1]["image"] if 1 <= keep <= len(current) else None
        if kept and kept.get("source") == "concept" and finished:
            image = text_only(kept)
        elif kept and kept.get("source") != "text":
            image = dict(kept)
            redraw = _text(s.get("redraw"), 300)
            if kept.get("source") == "concept" and redraw:
                image = concepts.redraw_image(kept, redraw)
            if note and not image.get("note"):
                image["note"] = note
        elif s.get("concept") is True and new_concepts and not finished:
            image = {"source": "concept", **({"note": note} if note else {})}
        elif clip:
            at = float(s["at"]) if isinstance(s.get("at"), (int, float)) else 0.0
            image = {"source": "clip", "clip_id": clip["id"],
                     "at": round(min(max(at, 0.0), clip.get("duration") or at), 1)}
            if note:
                image["note"] = note
        else:
            image = {"source": "text", **({"note": note} if note else {})}
        slides.append({"key": _key(), "role": s.get("role") if s.get("role") in ROLES else "point",
                       "heading": heading, "body": body, "image": image})
    if not slides:
        return []
    covers = [k for k, s in enumerate(slides) if s["role"] == "cover"]
    cover = slides.pop(covers[0] if covers else 0)
    cover["role"] = "cover"
    asks = [k for k, s in enumerate(slides) if s["role"] == "ask"]
    ask = slides.pop(asks[-1]) if asks else None
    for s in slides:
        s["role"] = "point"
    return [cover, *slides[:MAX_SLIDES - 1 - (1 if ask else 0)], *([ask] if ask else [])]


def picture_text(image: dict, clips: dict[int, dict] | None = None) -> str:
    """How a slide's picture reads to Claude and to Bryan."""
    source = image.get("source")
    if source == "clip":
        title = ((clips or {}).get(image.get("clip_id")) or {}).get("title")
        return f"a still from clip {image.get('clip_id')}" + (f" (\"{title}\")" if title else "") + f" at {image.get('at', 0)}s"
    if source == "photo":
        return "a photo the team uploaded"
    if source == "concept":
        return "an AI concept picture (marked \"Concept\")" + (f", of: {image['note']}" if image.get("note") else "")
    return "text only"


# ── Making one ────────────────────────────────────────────────────────────────

def make(conn, idea_id: int, slides, caption: str | None = None) -> int:
    """"Make this" on a carousel: a 'planned' production for the editing queue, from the
    idea's slides. Returns its id (the existing one if it's already made)."""
    idea = conn.execute("SELECT * FROM ideas WHERE id = ?", (idea_id,)).fetchone()
    if idea is None:
        raise KeyError(idea_id)
    if idea["production_id"]:
        return idea["production_id"]
    slides = clean_slides(slides)
    if len(slides) < 2:
        raise RuntimeError("write the slides first (a cover and at least one more)")
    plan = {"idea_id": idea_id, "title": idea["title"], "brand": idea["brand"], "format": "carousel",
            "post_type": idea["post_type"], "hook": idea["hook"] or slides[0]["heading"], "purpose": idea["purpose"] or "",
            "cta": idea["cta"] or "", "slides": slides}
    clip_ids = list(dict.fromkeys(s["image"]["clip_id"] for s in slides if s["image"].get("source") == "clip"))
    stamp = catalog.now()
    cur = conn.execute(
        """INSERT INTO productions (title, brand, format, status, version, idea, source_clips, caption, plan,
               created_at, updated_at) VALUES (?, ?, 'carousel', 'planned', 0, ?, ?, ?, ?, ?, ?)""",
        (idea["title"], idea["brand"], f"{plan['hook']}\n\n{plan['purpose']}".strip(), json.dumps(clip_ids),
         wording.tidy(caption if caption is not None else idea["caption"]), json.dumps(plan), stamp, stamp))
    conn.execute("UPDATE ideas SET status = 'made', production_id = ?, slides = ? WHERE id = ?",
                 (cur.lastrowid, json.dumps(slides), idea_id))
    conn.commit()
    log.info("Carousel %s queued: %s (%d slides)", cur.lastrowid, idea["title"], len(slides))
    return cur.lastrowid


def current(row) -> dict:
    """The production's latest slides ({slides, files, ...}); before the first render, the
    slides it was made from (no files yet)."""
    try:
        data = json.loads(row["slides"] or "null")
    except ValueError:
        data = None
    if isinstance(data, dict) and isinstance(data.get("slides"), list):
        return data
    plan = json.loads(row["plan"] or "{}")
    return {"slides": clean_slides(plan.get("slides")), "files": []}


# ── Layout ────────────────────────────────────────────────────────────────────

_ARROW = ('<svg width="34" height="22" viewBox="0 0 34 22" aria-hidden="true"><path d="M2 11h27M20 2l9 9-9 9" '
          'fill="none" stroke="currentColor" stroke-width="3.4" stroke-linecap="round" stroke-linejoin="round"/></svg>')

_CSS = """
@font-face{font-family:"UM Display";src:url("%(display)s") format("woff2");font-weight:200 800;font-display:block}
@font-face{font-family:"UM Text";src:url("%(text)s") format("woff2");font-weight:300 900;font-display:block}
:root{--bg:%(bg)s;--deep:%(deep)s;--ink:%(ink)s;--soft:%(soft)s;--accent:%(accent)s}
*{box-sizing:border-box;margin:0;padding:0}
html,body{width:1080px;background:#000}
.slide{position:relative;width:1080px;height:1350px;overflow:hidden;background:var(--bg);color:var(--ink);
  font-family:"UM Text",sans-serif;font-weight:500;-webkit-font-smoothing:antialiased;text-rendering:geometricPrecision}
.pic{position:absolute;left:0;top:0;width:1080px;height:1350px;object-fit:cover;object-position:center 45%%;display:block}
.shade-top{position:absolute;left:0;right:0;top:0;height:250px;z-index:1;
  background:linear-gradient(rgba(10,16,14,.7),rgba(10,16,14,0))}
.shade{position:absolute;left:0;right:0;bottom:0;height:960px;z-index:1;
  background:linear-gradient(rgba(14,22,19,0),rgba(14,22,19,.80) 42%%,rgba(14,22,19,.96))}
.top{position:absolute;left:84px;right:84px;top:64px;height:40px;display:flex;align-items:center;
  justify-content:space-between;z-index:3}
.mark{display:flex;align-items:center;gap:14px;font:800 25px/1 "UM Text";letter-spacing:.16em;text-transform:uppercase}
.mark svg{display:block;height:34px;width:auto}
.num{font:700 27px/1 "UM Text";letter-spacing:.06em;font-variant-numeric:tabular-nums}
.tag{position:absolute;left:84px;top:132px;z-index:3;font:800 22px/1 "UM Text";letter-spacing:.12em;
  text-transform:uppercase;color:var(--accent);background:rgba(10,16,14,.74);padding:10px 14px;border-radius:8px}
.copy{position:absolute;left:84px;right:84px;display:flex;flex-direction:column;gap:30px;overflow:hidden;z-index:2}
.bar{flex:none;width:112px;height:12px;border-radius:6px;background:var(--accent)}
.h{font-family:"UM Display",sans-serif;font-weight:800;line-height:1.04;letter-spacing:-.015em;text-wrap:balance;
  overflow-wrap:break-word}
.b{font-weight:500;line-height:1.38;color:var(--soft);text-wrap:pretty;overflow-wrap:break-word}
.cover.text .copy{top:236px;bottom:240px;justify-content:center}
.cover.photo .copy{top:520px;bottom:200px;justify-content:flex-end}
.cover.photo .h{text-shadow:0 2px 24px rgba(0,0,0,.35)}
.cover.photo .b{color:var(--ink)}
.swipe{position:absolute;right:84px;bottom:92px;z-index:3;display:flex;align-items:center;gap:14px;
  font:800 26px/1 "UM Text";letter-spacing:.14em;text-transform:uppercase;color:var(--accent)}
.point.text .copy{top:236px;bottom:160px;justify-content:center}
.photo.split .pic{height:var(--pic,780px)}
.photo.split .panel{position:absolute;left:0;right:0;bottom:0;top:var(--pic,780px);background:var(--bg);z-index:1}
.photo.split .copy{top:calc(var(--pic,780px) + 44px);bottom:72px;gap:22px;justify-content:center}
.ask{background:var(--deep)}
.ask .h{color:var(--accent)}
.ask .b{color:var(--ink)}
.ask.text .copy{top:236px;bottom:280px;justify-content:center}
.ask.photo .panel{background:var(--deep)}
.sign{position:absolute;left:84px;right:84px;bottom:92px;display:flex;align-items:center;gap:18px;z-index:2;
  font:800 34px/1 "UM Text";letter-spacing:.16em;text-transform:uppercase}
.sign svg{display:block;height:64px;width:auto}
"""

_FIT = """
window.studioFit = () => [...document.querySelectorAll(".slide")].map((s) => {
  const copy = s.querySelector(".copy"), h = s.querySelector(".h"), b = s.querySelector(".b");
  const H = Number(s.dataset.h), B = Number(s.dataset.b);
  // Too much text: the blocks (and the gaps between them) are taller than the space, or a
  // word is wider than the line.
  const over = () => {
    const kids = [...copy.children], gap = parseFloat(getComputedStyle(copy).rowGap) || 0;
    const need = kids.reduce((a, k) => a + k.getBoundingClientRect().height, 0) + gap * Math.max(kids.length - 1, 0);
    return need > copy.clientHeight + 1 || kids.some((k) => k.scrollWidth > k.clientWidth + 1);
  };
  let f = 1;
  const set = () => { if (h) h.style.fontSize = Math.round(H * f) + "px"; if (b) b.style.fontSize = Math.round(B * f) + "px"; };
  set();
  // Smaller words first; a photo slide then gives its text more of the slide.
  while (over() && f > %(min)s) { f = Math.round((f - 0.03) * 100) / 100; set(); }
  if (s.classList.contains("split")) {
    let pic = 780;
    while (over() && pic > 520) { pic -= 40; s.style.setProperty("--pic", pic + "px"); }
  }
  const t = s.getBoundingClientRect();
  const box = (el) => {
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return [Math.round(r.left - t.left), Math.round(r.top - t.top), Math.round(r.right - t.left), Math.round(r.bottom - t.top)];
  };
  // Where the words really are (not the boxes around them).
  const ink = (el) => {
    if (!el || !el.textContent.trim()) return null;
    const range = document.createRange();
    range.selectNodeContents(el);
    const rs = [...range.getClientRects()];
    if (!rs.length) return null;
    return [Math.round(Math.min(...rs.map((r) => r.left)) - t.left), Math.round(Math.min(...rs.map((r) => r.top)) - t.top),
            Math.round(Math.max(...rs.map((r) => r.right)) - t.left), Math.round(Math.max(...rs.map((r) => r.bottom)) - t.top)];
  };
  return { n: Number(s.dataset.n), layout: s.dataset.layout, fit: f, overflow: over(),
           heading_px: h ? parseFloat(h.style.fontSize) : null, body_px: b ? parseFloat(b.style.fontSize) : null,
           boxes: { copy: box(copy), heading: ink(h), body: ink(b), mark: box(s.querySelector(".mark")),
                    num: box(s.querySelector(".num")), tag: box(s.querySelector(".tag")) } };
});
"""


def _lines(text: str) -> str:
    return "<br>".join(html.escape(line) for line in text.split("\n"))


def _logo() -> str:
    """The Urban Makers mark (render/carousel/um-mark.svg, from Bryan), inline so the page
    needs nothing from outside."""
    svg = LOGO.read_text(encoding="utf-8")
    return re.sub(r'\s(width|height)="[^"]*"', "", svg, count=2).replace("<svg", '<svg aria-hidden="true"', 1)


def slide_html(n: int, total: int, slide: dict, picture: Path | None, mark: str = "") -> str:
    """One slide as a section of the page (layout by role, and whether it has a picture)."""
    role = slide["role"]
    layout = f"{role}-{'photo' if picture else 'text'}"
    h_px, b_px = SIZES[layout]
    split = picture is not None and role in ("point", "ask")
    classes = ["slide", role, "photo" if picture else "text"] + (["split"] if split else [])
    parts = []
    if picture:
        parts.append(f'<img class="pic" src="{html.escape(picture.resolve().as_uri())}" alt="">')
        parts.append('<div class="shade-top"></div>')
        parts.append('<div class="panel"></div>' if split else '<div class="shade"></div>')
    top_mark = f'<span class="mark">{_logo()}Urban Makers</span>' if role != "ask" else "<span></span>"
    parts.append(f'<div class="top">{top_mark}<span class="num">{n}/{total}</span></div>')
    if mark:
        parts.append(f'<span class="tag">{html.escape(mark)}</span>')
    copy = []
    if not picture or role == "cover":
        copy.append('<span class="bar"></span>')
    if slide["heading"]:
        copy.append(f'<div class="h">{_lines(slide["heading"])}</div>')
    if slide["body"]:
        copy.append(f'<div class="b">{_lines(slide["body"])}</div>')
    parts.append(f'<div class="copy">{"".join(copy)}</div>')
    if role == "cover" and total > 1:
        parts.append(f'<div class="swipe">Swipe{_ARROW}</div>')
    if role == "ask" and not picture:
        parts.append(f'<div class="sign">{_logo()}Urban Makers</div>')
    return (f'<section class="{" ".join(classes)}" id="slide-{n}" data-n="{n}" data-layout="{layout}" '
            f'data-h="{h_px}" data-b="{b_px}">{"".join(parts)}</section>')


def page_html(slides: list[dict], pictures: list[tuple[Path | None, str]]) -> str:
    """The whole carousel as one page: every slide 1080x1350, one under the other."""
    css = _CSS % {"display": FONTS["display"].resolve().as_uri(), "text": FONTS["text"].resolve().as_uri(), **COLOURS}
    body = "".join(slide_html(n, len(slides), s, pic, mark) for n, (s, (pic, mark)) in enumerate(zip(slides, pictures), 1))
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><style>{css}</style></head>'
            f'<body>{body}<script>{_FIT % {"min": FIT_MIN}}</script></body></html>')


def shoot(page: str, out_dir: Path) -> dict:
    """Lay out the page in the headless Chrome and save each slide as NN.png in out_dir.
    Returns shoot.mjs's report (each slide's fit and text boxes, the fonts' state)."""
    node = render._node()
    if not node:
        raise RuntimeError(f"Node.js not found; install it with: {render._NODE_FIX}")
    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="studio-carousel-") as tmp:
        page_file, job_file = Path(tmp) / "slides.html", Path(tmp) / "job.json"
        page_file.write_text(page, encoding="utf-8")
        job_file.write_text(json.dumps({"chrome": _browser(), "html": str(page_file), "out_dir": str(out_dir),
                                        "width": W, "height": H}), encoding="utf-8")
        result = subprocess.run([node, str(SHOOT), str(job_file)], cwd=HERE, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=600, creationflags=_NO_WINDOW)
    if result.returncode != 0:
        raise RuntimeError("laying out the slides failed: " + (result.stderr or result.stdout).strip()[-400:])
    report = json.loads(result.stdout)
    unloaded = [f["family"] for f in report.get("fonts", []) if f.get("status") != "loaded"]
    if unloaded:
        raise RuntimeError(f"the carousel fonts didn't load ({', '.join(unloaded)})")
    return report


def lay_out(conn, slides: list[dict], out_dir: Path) -> dict:
    """Pictures, layout and PNGs for these slides. Returns {files, layout, pictures}."""
    pictures, notes = [], []
    for n, s in enumerate(slides, 1):
        path, mark, why = slide_images.picture(s["image"], conn)
        pictures.append((path, mark))
        notes.append({"n": n, "source": s["image"].get("source", "text") if path else "text",
                      **({"missing": why} if why else {})})
    report = shoot(page_html(slides, pictures), out_dir)
    files = [s["file"] for s in report["slides"]]
    if len(files) != len(slides) or not all(Path(f).is_file() for f in files):
        raise RuntimeError("Chrome didn't save every slide")
    layout = [{k: v for k, v in s.items() if k != "file"} for s in report["slides"]]
    return {"files": files, "layout": layout, "pictures": notes, "fonts": report.get("fonts", [])}


def _thumb(png: Path, out: Path) -> None:
    from PIL import Image
    with Image.open(png) as img:
        img = img.convert("RGB")
        img.thumbnail((540, 675), Image.LANCZOS)
        img.save(out, "JPEG", quality=85)


def _summary(slides: list[dict], pictures: list[dict], revision: dict | None, drew: dict | None = None) -> str:
    counts = {"clip": 0, "photo": 0, "concept": 0, "text": 0}
    for p in pictures:
        counts[p["source"] if p["source"] in counts else "text"] += 1
    parts = [f"{counts['clip']} with stills from your footage" if counts["clip"] else "",
             f"{counts['photo']} with your photos" if counts["photo"] else "",
             f"{counts['concept']} with AI concept pictures (marked Concept)" if counts["concept"] else "",
             f"{counts['text']} text only" if counts["text"] else ""]
    line = f"Laid out {len(slides)} slides: " + ", ".join(p for p in parts if p) + "."
    if drew and drew.get("drawn"):
        line += f" Drew {drew['drawn']} concept picture{'s' if drew['drawn'] != 1 else ''} (US${drew['usd']:.2f})."
    missing = [p for p in pictures if p.get("missing")]
    if missing:
        line += " " + " ".join(f"Slide {p['n']} is text only for now ({p['missing']})." for p in missing)
    if revision and revision.get("summary"):
        line = revision["summary"].strip() + " " + line
    return line


# ── Rendering a version (the editing queue, worker.run_editor) ────────────────

def post_type(conn, production) -> str | None:
    """The post's kind now (it can change in the planner after it's made)."""
    plan = json.loads(production["plan"] or "{}")
    row = conn.execute("SELECT post_type FROM ideas WHERE id = ?", (plan.get("idea_id"),)).fetchone()
    return (row["post_type"] if row and row["post_type"] else None) or plan.get("post_type")


def run(conn, production_id: int, client=None) -> Path:
    """Make the next draft of a carousel: revise the slides from Bryan's open notes when it
    was sent back (or draw one slide's concept pictures again, Redraw), have the concept
    pictures drawn and checked, then lay them out. Returns the folder of PNGs."""
    row = conn.execute("SELECT * FROM productions WHERE id = ?", (production_id,)).fetchone()
    if row is None:
        raise KeyError(production_id)
    was = row["status"]
    data = current(row)
    slides = data["slides"]
    if not slides:
        raise RuntimeError("this carousel has no slides")
    catalog.set_production_status(conn, production_id, "producing")
    notes = [dict(r) for r in conn.execute(
        "SELECT * FROM feedback WHERE target = 'production' AND target_id = ? AND status = 'open' ORDER BY id",
        (production_id,))]
    revision = None
    redraws = data.get("redraw") if isinstance(data.get("redraw"), dict) else {}
    # Sent back (or interrupted while revising): Bryan's open notes on the last version. A Redraw
    # from Review only draws pictures again: the notes wait for Send back.
    # Studio's read of the last version rides along with any revision (Bryan: add them automatically).
    import restyle
    import self_fix
    fresh = was == "revising" and row["restyle"] == restyle.FRESH
    read = [{**f, "body": f"From Studio's read: {f['body']}"} for f in self_fix.read_notes(row, notes)] \
        if not fresh and (was == "revising" or (notes and row["version"] >= 1 and not redraws)) else []
    if fresh:  # start over: rewritten from the plan's own slides, not the last version's; no read of it
        slides = clean_slides(json.loads(row["plan"] or "{}").get("slides")) or slides
        revision = revise(conn, dict(row), slides, [restyle.CAROUSEL_NOTE] + notes, client)
        slides = revision["slides"]
    elif notes and (was == "revising" or (row["version"] >= 1 and not redraws)):
        revision = revise(conn, dict(row), slides, notes + read, client)
        slides = revision["slides"]
    elif was == "revising" and (row["restyle"] or read):  # a restyle (restyle.py) and/or the read's fixes
        revision = revise(conn, dict(row), slides, ([restyle.CAROUSEL_NOTE] if row["restyle"] else []) + read, client)
        slides = revision["slides"]
    for n, change in redraws.items():
        k = int(n)
        if 1 <= k <= len(slides) and slides[k - 1]["image"].get("source") == "concept":
            slides[k - 1] = {**slides[k - 1], "image": concepts.redraw_image(slides[k - 1]["image"], change)}
    # A Finished homes post: its concept slides are laid out as text (prepare says why) and
    # Studio's read flags them.
    kind = post_type(conn, row)
    drew = concepts.prepare(conn, production_id, row["title"], kind, slides, client)
    version = row["version"] + 1
    out_dir = RENDERS / f"carousel_{production_id}_v{version}"
    if out_dir.exists():
        shutil.rmtree(out_dir)
    made = lay_out(conn, slides, out_dir)
    thumb = out_dir / "thumb.jpg"
    _thumb(Path(made["files"][0]), thumb)
    stored = {"version": version, "slides": slides, **made,
              "summary": _summary(slides, made["pictures"], revision, drew),
              "notes_addressed": (revision or {}).get("notes_addressed", []),
              "concepts": {"drawn": drew["drawn"], "usd": drew["usd"]}}
    caption = row["caption"]
    if revision and revision.get("caption"):
        caption = revision["caption"]
    clip_ids = list(dict.fromkeys(s["image"]["clip_id"] for s in slides if s["image"].get("source") == "clip"))
    # It stays 'producing' until Studio's read and its own fix rounds are done (self_fix.deliver).
    conn.execute(
        """UPDATE productions SET version = ?, thumb_path = ?, slides = ?, caption = ?,
               source_clips = ?, duration = NULL, error = NULL, updated_at = ?, self_fix = ? WHERE id = ?""",
        (version, str(thumb), json.dumps(stored, ensure_ascii=False), caption, json.dumps(clip_ids), catalog.now(),
         json.dumps({"version": version, "pending": True}), production_id))
    if revision is not None or not redraws:
        conn.execute("""UPDATE feedback SET status = 'addressed'
                        WHERE target = 'production' AND target_id = ? AND status = 'open'""", (production_id,))
    conn.commit()
    log.info("Carousel %s v%d laid out: %d slides", production_id, version, len(slides))
    if revision is not None:  # Bryan's editing notes become rules Studio keeps (learning.py)
        import learning
        learning.from_notes(conn, notes, revision.get("notes_addressed", []), "carousel")
    import self_fix
    delivered = self_fix.deliver(conn, production_id, client)  # Studio's read and its own fixes, then To review
    shown = files(delivered)
    return shown[0].parent if shown else out_dir


def self_revise(conn, production_id: int, notes: list[dict], round_no: int, client=None) -> Path:
    """One of Studio's own fix rounds on a carousel, before Bryan sees it: Claude revises the
    slides from the read's fixes (no new or redrawn concept pictures: those spend, so they're
    Bryan's call) and they're laid out again as the same version, in a folder of their own."""
    row = conn.execute("SELECT * FROM productions WHERE id = ?", (production_id,)).fetchone()
    data = current(row)
    revision = revise(conn, dict(row), data["slides"], notes, client, own=True)
    slides = revision["slides"]
    out_dir = RENDERS / f"carousel_{production_id}_v{row['version']}_fix{round_no}"
    if out_dir.exists():
        shutil.rmtree(out_dir)
    made = lay_out(conn, slides, out_dir)
    thumb = out_dir / "thumb.jpg"
    _thumb(Path(made["files"][0]), thumb)
    stored = {"version": row["version"], "slides": slides, **made,
              "summary": _summary(slides, made["pictures"], revision),
              "notes_addressed": revision.get("notes_addressed", []), "concepts": data.get("concepts")}
    clip_ids = list(dict.fromkeys(s["image"]["clip_id"] for s in slides if s["image"].get("source") == "clip"))
    conn.execute("""UPDATE productions SET thumb_path = ?, slides = ?, caption = ?, source_clips = ?, updated_at = ?
                    WHERE id = ?""",
                 (str(thumb), json.dumps(stored, ensure_ascii=False), revision.get("caption") or row["caption"],
                  json.dumps(clip_ids), catalog.now(), production_id))
    conn.commit()
    log.info("Carousel %s v%s: Studio's fix round %d laid out", production_id, row["version"], round_no)
    return out_dir


SLIDE_FIELDS = {"heading": 120, "body": 400}  # the most a patch may write (the layout fits what's there)


def patch(conn, production_id: int, actions: list[dict], round_no: int) -> tuple[Path | None, list[dict]]:
    """One of Studio's own fix rounds made as patches: the read's slide_text actions ({n, action:
    {item, field, to}}) change those words only, no Claude call, and the slides are laid out again
    as the same version, in a folder of their own. Returns the folder (None when nothing could be
    changed) and each action's result {n, type, ok, did | why}."""
    row = conn.execute("SELECT * FROM productions WHERE id = ?", (production_id,)).fetchone()
    data = current(row)
    slides = json.loads(json.dumps(data["slides"]))
    caption, results = row["caption"], []
    for item in actions:
        a = item["action"]
        res = {"n": item["n"], "type": a.get("type")}
        to = re.sub(r"[ \t]+", " ", str(a.get("to") or "").replace("*", "")).strip()
        field, n = a.get("field"), a.get("item")
        if a.get("type") != "slide_text":
            res.update(ok=False, why="that needs the slides written again")
        elif field == "caption":
            if not to:
                res.update(ok=False, why="the read gave no caption")
            else:
                caption = to
                res.update(ok=True, did="rewrote the caption")
        elif field not in SLIDE_FIELDS or not isinstance(n, int) or not 1 <= n <= len(slides):
            res.update(ok=False, why="the read named a slide or a part of it the carousel doesn't have")
        elif not to and field == "heading":
            res.update(ok=False, why="a slide needs a heading")
        elif len(to) > SLIDE_FIELDS[field]:
            res.update(ok=False, why="the new words are too long for the slide")
        elif to == slides[n - 1].get(field, ""):
            res.update(ok=False, why="the slide already says that")
        else:
            slides[n - 1][field] = to
            res.update(ok=True, did=f"slide {n}'s {field} now reads \"{to[:60]}{'...' if len(to) > 60 else ''}\"")
        results.append(res)
    if not any(r.get("ok") for r in results):
        return None, results
    for s in slides:  # the safe fixes on viewer-facing words (no rewrite call: these came from Studio's read)
        for k in SLIDE_FIELDS:
            if s.get(k):
                s[k] = wording.tidy(s[k])
    out_dir = RENDERS / f"carousel_{production_id}_v{row['version']}_fix{round_no}"
    if out_dir.exists():
        shutil.rmtree(out_dir)
    made = lay_out(conn, slides, out_dir)
    thumb = out_dir / "thumb.jpg"
    _thumb(Path(made["files"][0]), thumb)
    did = "; ".join(r["did"] for r in results if r.get("ok"))
    stored = {**data, "version": row["version"], "slides": slides, **made,
              "summary": _summary(slides, made["pictures"], None) + f" Studio then {did}.",
              "notes_addressed": [], "patched": [r for r in results if r.get("ok")]}
    conn.execute("""UPDATE productions SET thumb_path = ?, slides = ?, caption = ?, updated_at = ? WHERE id = ?""",
                 (str(thumb), json.dumps(stored, ensure_ascii=False), caption, catalog.now(), production_id))
    conn.commit()
    log.info("Carousel %s v%s: Studio's fix round %d patched %d thing(s)", production_id, row["version"], round_no,
             sum(1 for r in results if r.get("ok")))
    return out_dir, results


def _concept_slide(conn, production_id: int, n: int):
    row = conn.execute("SELECT * FROM productions WHERE id = ?", (production_id,)).fetchone()
    if row is None or row["format"] != "carousel":
        raise KeyError(production_id)
    if row["status"] != "ready":
        raise ValueError("only a draft waiting for review can change its pictures")
    data = current(row)
    if not 1 <= n <= len(data["slides"]) or data["slides"][n - 1]["image"].get("source") != "concept":
        raise ValueError("that slide has no concept picture")
    return row, data


def choose(conn, production_id: int, n: int, option: str) -> dict:
    """Bryan picks the other concept option for slide n: the same version is laid out again
    with it (nothing is drawn, nothing is paid). Returns the stored slides."""
    row, data = _concept_slide(conn, production_id, n)
    image = data["slides"][n - 1]["image"]
    if option not in (image.get("options") or []):
        raise ValueError("that option isn't there")
    if image.get("chosen") == option:
        return data
    image["chosen"] = option
    shared = ((concepts.meta(image.get("concept")) or {}).get("shared") or {}).get(option)
    if shared:
        image["thumb_path"] = shared
    out_dir = RENDERS / f"carousel_{production_id}_v{row['version']}"
    made = lay_out(conn, data["slides"], out_dir)
    _thumb(Path(made["files"][0]), out_dir / "thumb.jpg")
    data.update(made)
    data["layouts"] = int(data.get("layouts") or 1) + 1
    conn.execute("UPDATE productions SET slides = ?, thumb_path = ?, updated_at = ? WHERE id = ?",
                 (json.dumps(data, ensure_ascii=False), str(out_dir / "thumb.jpg"), catalog.now(), production_id))
    conn.commit()
    log.info("Carousel %s v%s: slide %d uses concept option %s", production_id, row["version"], n, option)
    return data


def redraw(conn, production_id: int, n: int, change: str = "") -> None:
    """Redraw in Review (Bryan): slide n's concept pictures are drawn again (with the change,
    if he wrote one) as the next version. That spends, like Make this."""
    row, data = _concept_slide(conn, production_id, n)
    why = concepts.available()
    if why:
        raise ValueError(f"Studio can't draw pictures: it {why}")
    if post_type(conn, row) == concepts.FINISHED:
        raise ValueError(concepts.NOT_IN_FINISHED)
    data["redraw"] = {**(data.get("redraw") or {}), str(n): (change or "").strip()[:300]}
    conn.execute("UPDATE productions SET slides = ? WHERE id = ?", (json.dumps(data, ensure_ascii=False), production_id))
    catalog.set_production_status(conn, production_id, "planned")
    log.info("Carousel %s: redraw slide %d's concept pictures", production_id, n)


# ── Revising from Bryan's notes ───────────────────────────────────────────────

# Keep in step with cloud/supabase/functions/_shared/prompts.ts (SLIDE_SCHEMA, SLIDE_RULES).
_SLIDE = {
    "type": "object",
    "properties": {
        "role": {"type": "string", "enum": list(ROLES)},
        "heading": {"type": "string"}, "body": {"type": "string"},
        "keep_image": {"type": "integer"}, "clip_id": {"type": "integer"}, "at": {"type": "number"},
        "image_idea": {"type": "string"}, "concept": {"type": "boolean"},
    },
    "required": ["role", "heading", "body", "keep_image", "clip_id", "at", "image_idea", "concept"],
    "additionalProperties": False,
}
# Revising can also ask for one slide's concept picture to be drawn again.
_REVISE_SLIDE = {**_SLIDE, "properties": {**_SLIDE["properties"], "redraw": {"type": "string"}},
                 "required": [*_SLIDE["required"], "redraw"]}
REVISE_SCHEMA = {
    "type": "object",
    "properties": {
        "slides": {"type": "array", "items": _REVISE_SLIDE},
        "caption_changed": {"type": "boolean"},
        "caption": {"type": "string"},
        "summary": {"type": "string"},
        "notes_addressed": {"type": "array", "items": {
            "type": "object", "properties": {"note_id": {"type": "integer"}, "how": {"type": "string"},
                                             "kind": {"type": "string", "enum": ["editing", "taste"]},
                                             "rule": {"type": "string"}, "same_rule": {"type": "integer"}},
            "required": ["note_id", "how", "kind", "rule", "same_rule"], "additionalProperties": False}},
    },
    "required": ["slides", "caption_changed", "caption", "summary", "notes_addressed"],
    "additionalProperties": False,
}

SLIDE_RULES = """How to write a carousel:
- Bryan's voice: plain, practical and warm, like he explains things on site; "we" for Urban Makers. \
No hype, no emoji on the slides, no trade jargon a homeowner wouldn't use. English only.
- Slide 1 (role cover) is the hook: a heading of at most 9 words that makes someone stop and swipe \
(a promise, a tension or a question); body empty or one short line.
- Then 4 to 8 point slides (role point), one idea each: a heading of at most 8 words and a body of \
at most 30 words, specific and useful (what to do and why). Each slide makes sense on its own, so \
the post is worth saving. Use a number only when it's a well-known fact or Bryan said it.
- The last slide (role ask) is the call to action, fitting the post's kind (the content plan lists \
the ask for each kind). An ask that offers something (a Work with us post, or any other) uses ONLY \
an offer listed in the content plan, worded as listed; otherwise ask people to save, share, follow \
or comment. Never invent a price, discount, promotion, deadline, freebie or offer.
- Never name a place (estate, street, condo, block, town) or a client: where clients live stays \
private. Never invent facts about a project; write [placeholder] where the team must fill something in.
- Pictures come only from Urban Makers' own footage and photos, or an AI concept picture drawn for \
the slide; never anyone else's images. For each slide: keep_image is the number of a current slide \
whose picture this slide keeps (someone picked it; keep it with the point it illustrates when you \
rewrite), else 0. clip_id and at: a still from one of the clips listed (at = the moment in seconds) \
that shows exactly what the slide says, else 0 and 0. Never use a clip just to fill a slide: \
text-only slides read well. image_idea: one short line on what picture would suit, for the team \
("close-up of the window seat's drawer"), or "".
- concept: true to illustrate a slide with a new AI concept picture instead, only when the prompt \
says concept pictures are available (otherwise always false). Studio draws it when Bryan presses \
Make this and marks it "Concept"; image_idea then says what it shows (the room, the idea, the \
materials), and keep_image, clip_id and at are 0. Use concepts mainly in Ideas and Know-how posts, \
for a design idea no clip shows. A real still or photo always beats a concept: where the footage has \
a real example, use it (a concept can sit beside it). Never in a Finished homes post, which shows \
only real work. A concept slide's words never say it's a home Urban Makers built or a client's home.
- caption: a ready-to-edit Instagram caption in Bryan's voice that adds to the slides rather than \
repeating them, ending with the same ask, with 3 to 5 hashtags (the caption rule below)."""

_REVISE_SYSTEM = """You revise a draft Instagram carousel for Bryan, who owns Urban Makers, a \
renovation company in Singapore, from the notes he left on it.

{brands}

Bryan's playbook (his voice and story rules):

<playbook>
{playbook}
</playbook>

The content plan (the kinds of post, this month's mix and the offers):

<content_plan>
{content_plan}
</content_plan>

""" + SLIDE_RULES + """

Revising:
- Apply every note. A note marked with a slide number is about that slide; one without is about \
the whole post. Keep everything the notes don't ask to change, word for word, with keep_image set \
to its own slide number so its picture stays.
- A note about a concept picture ("redraw slide 3, warmer"): keep_image = that slide and redraw = \
what to change in the picture ("warmer light, oak instead of white"); Studio draws only that slide \
again. Otherwise redraw is "" and a kept concept picture stays exactly as it is.
- caption_changed: true only when a note asks for a different caption; then caption is the new \
caption, otherwise caption is "".
- summary: one or two sentences for Bryan on what you changed.
- notes_addressed: every note id, with how you handled it, and its kind: "editing" when it's a \
craft problem a good editor should never repeat on any carousel (too much text on a slide, a \
spelling slip, a weak cover line, an ask that doesn't fit), "taste" when it's Bryan's judgement or \
preference about this post. For an editing note, rule is the general rule it teaches as an \
instruction in at most 14 words, and same_rule the number of the learned rule it repeats (R3 = \
3) or 0; otherwise rule is "" and same_rule 0.
- The playbook's "Editing rules Studio learned" are problems caught before: never repeat them."""


def _clips(conn, production: dict, slides: list[dict]) -> dict[int, dict]:
    """Clips Claude may take stills from: those already on the slides, the project's own
    usable clips, then recent ones from elsewhere (no retakes)."""
    plan = json.loads(production.get("plan") or "{}")
    idea = conn.execute("SELECT project_id FROM ideas WHERE id = ?", (plan.get("idea_id"),)).fetchone()
    project_id = idea["project_id"] if idea else None
    used = [s["image"]["clip_id"] for s in slides if s["image"].get("source") == "clip"]
    rows = []
    if used:
        rows += conn.execute(f"SELECT * FROM clips WHERE id IN ({','.join('?' * len(used))})", used).fetchall()
    base = "SELECT * FROM clips WHERE status = 'tagged' AND COALESCE(usable, 1) = 1 AND {} ORDER BY COALESCE(recorded_at, ingested_at) DESC LIMIT ?"
    if project_id:
        rows += conn.execute(base.format("project_id = ?"), (project_id, 30)).fetchall()
        rows += conn.execute(base.format("COALESCE(project_id, -1) <> ?"), (project_id, 20)).fetchall()
    else:
        rows += conn.execute(base.format("1 = 1"), (40,)).fetchall()
    out = {}
    for r in rows:
        q = json.loads(r["quality"] or "{}") if r["quality"] else {}
        if q.get("verdict") == "retake" and r["id"] not in used:
            continue
        out.setdefault(r["id"], {"id": r["id"], "title": r["title"] or r["source_name"], "duration": r["duration"]})
    return out


def _mmss(s) -> str:
    s = int(round(s or 0))
    return f"{s // 60}:{s % 60:02d}"


def concepts_line(kind: str | None, on: bool) -> str:
    """What Claude is told about concept pictures (like _shared/slides.ts conceptsLine)."""
    if kind == concepts.FINISHED:
        return "AI concept pictures: never in this post (Finished homes shows only real work): concept is always false."
    if on:
        return "AI concept pictures: available (concept may be true where a design idea needs showing)."
    return "AI concept pictures: not available on Studio yet (no image key): concept is always false."


def revise_prompt(production: dict, slides: list[dict], notes: list[dict], clips: dict[int, dict],
                  kind: str | None = None, own: bool = False) -> str:
    plan = json.loads(production.get("plan") or "{}")
    kind = kind or plan.get("post_type")
    lines = [f"The carousel: {production['title']} (kind: {kind or 'not set'}), draft "
             f"v{production['version']}.", f"Purpose: {plan.get('purpose') or '-'}", "Slides now:"]
    for n, s in enumerate(slides, 1):
        lines.append(f"  {n}. [{s['role']}] {s['heading'] or '(no heading)'}" + (f" | {s['body']}" if s["body"] else "")
                     + f" | picture: {picture_text(s['image'], clips)}")
    lines.append(f"Caption now: {production.get('caption') or '(none)'}")
    lines.append("Studio's own read of these slides, before Bryan sees them: fix each point (take kind "
                 "\"editing\", rule \"\" for each), keep everything else as it is, and draw no new or redrawn "
                 "concept pictures (concept false, redraw \"\"):" if own else "Bryan's notes:")
    for note in notes:
        lines.append(f"  note {note['id']}" + (f" (slide {note['slide']})" if note.get("slide") else "")
                     + f": {note['body']}")
    lines.append("Clips from Urban Makers' own footage you may take stills from (id, title, length):")
    lines += [f"  clip {c['id']}: \"{c['title'] or 'untitled'}\", {_mmss(c['duration'])}" for c in clips.values()] \
        or ["  (none: keep text-only slides)"]
    lines.append(concepts_line(kind, concepts.available() is None and not own))
    import learning
    rules = learning.rules_text("carousel")
    if rules:
        lines.append(rules)
    return "\n".join(lines)


def _context_text() -> str:
    import cloud_sync
    import content_plan
    try:
        api = cloud_sync.Client() if cloud_sync.enabled() else None
    except Exception:
        api = None
    return content_plan.context_text(api)


def revise(conn, production: dict, slides: list[dict], notes: list[dict], client=None, own: bool = False) -> dict:
    """One Claude call: the slides (and caption, when a note asks) changed as the notes say.
    own: the notes are Studio's read (one of its own fix rounds): no pictures are drawn, and
    the cost is counted apart. Returns {slides, caption, summary, notes_addressed}."""
    import self_fix
    import anthropic

    import llm
    import playbook
    import usage
    clips = _clips(conn, production, slides)
    kind = post_type(conn, production)
    system = (_REVISE_SYSTEM.replace("{brands}", settings.brands_text()).replace("{playbook}", playbook.text())
              .replace("{content_plan}", _context_text()) + "\n\n" + wording.guide(caption=True))
    client = client or anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY or None)
    response = llm.final_message(
        client, model=settings.CLAUDE_MODEL, max_tokens=24000,
        betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        output_config={"effort": "high", "format": {"type": "json_schema", "schema": REVISE_SCHEMA}},
        system=system, messages=[{"role": "user", "content": revise_prompt(production, slides, notes, clips, kind, own)}])
    usage.record("carousel", self_fix.ref(production["id"]) if own else f"production:{production['id']}", response)
    if response.stop_reason in ("refusal", "max_tokens"):
        raise RuntimeError(f"Claude couldn't revise the slides ({response.stop_reason})")
    answer = json.loads(next(b.text for b in response.content if b.type == "text"))
    if own:  # Studio's own round never spends on pictures
        for s in answer.get("slides") or []:
            if isinstance(s, dict):
                s.pop("redraw", None)
    new = plan_slides(answer.get("slides"), slides, clips, new_concepts=concepts.available() is None and not own,
                      finished=kind == concepts.FINISHED)
    if len(new) < 2:
        raise RuntimeError("Claude's revision had no slides; send it back again")
    ids = {n["id"] for n in notes}
    # The words viewers read: the safe fixes, and one rewrite of what still reads machine-written
    # (counted with this revision).
    caption = _text(answer.get("caption"), 4000) if answer.get("caption_changed") else ""
    fields = {f"{n}.{k}": s[k] for n, s in enumerate(new) for k in ("heading", "body") if s[k]}
    if caption:
        fields["caption"] = caption
    polished, _ = wording.polish(fields, "carousel", self_fix.ref(production["id"]) if own else f"production:{production['id']}",
                                 client, limits={**{k: 200 if k.endswith("heading") else 800 for k in fields}, "caption": 4000})
    for key, text in polished.items():
        if key == "caption":
            caption = text
        else:
            n, k = key.split(".")
            new[int(n)][k] = text
    return {"slides": new, "caption": caption,
            "summary": _text(answer.get("summary"), 600),
            "notes_addressed": [{"note_id": a["note_id"], "how": _text(a.get("how"), 300), "kind": a.get("kind"),
                                 "rule": _text(a.get("rule"), 200), "same_rule": a.get("same_rule") or 0}
                                for a in answer.get("notes_addressed", []) if a.get("note_id") in ids]}


# ── Files ─────────────────────────────────────────────────────────────────────

def files(row) -> list[Path]:
    """The current draft's PNGs, in order (only files Studio rendered)."""
    data = current(row)
    out = []
    for f in data.get("files") or []:
        p = Path(f)
        try:
            p.resolve().relative_to(RENDERS.resolve())
        except ValueError:
            continue
        if p.is_file():
            out.append(p)
    return out


def caption_text(row) -> str:
    return (row["caption"] or "").strip() + "\n"


def download_files(row) -> list[tuple[str, Path]]:
    """For the Review download: 01.png, 02.png ... (and the caption, written next to them)."""
    pngs = files(row)
    if not pngs:
        return []
    out = [(p.name, p) for p in pngs]
    cap = pngs[0].parent / "caption.txt"
    cap.write_text(caption_text(row), encoding="utf-8")
    out.append(("caption.txt", cap))
    return out
