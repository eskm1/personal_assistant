"""
Animated explainers (stage 1): short 1080x1920 videos made only of moving type, shapes and
drawings in the Urban Makers look, with no generated footage. Claude (or Bryan) writes a SPEC,
a list of scenes in JSON; this module checks it, lays every scene out from the library's own
components and renders it to MP4 with the HyperFrames toolchain the editor already uses.

  1. validate(): the spec's shape, lengths and limits; no prices, only the planner's offers in
     the ask; a freeform drawing's SVG paths are parsed, made absolute and kept inside their box.
     Problems come back in plain words (SpecError), so a spec can be fixed, not guessed at.
  2. page_html(): each scene becomes markup from the library (render/motion: motion.css, the
     moves in motion.js, the line icons in icons.json). Every moving element says which move and
     when (data-a, data-at); motion.js turns that into one GSAP timeline.
  3. check(): the page is opened in the headless Chrome with the network refused, the fonts and
     the timeline are waited for, and every scene is measured while fully on screen: text and
     drawings inside the safe area (clear of Instagram's top bar, caption and buttons), no text on
     top of other text, nothing cut off.
  4. render(): HyperFrames renders it (no network needed: fonts and scripts sit next to the page),
     then ffmpeg adds the sound (a music bed, or silence) and makes it Instagram-ready.
  5. render_insert() / overlay_insert(): one scene on a paper card over a talking-head Reel, above
     the caption band (the editor's CARD_BOTTOM), while Bryan explains it.

Scene types: headline, steps, layers (a cross-section), measure, before_after, compare, plan,
icons, stat, timeline, end, freeform. SCENE_TYPES describes each for Claude's prompt.
Nothing here changes the editor: stage 1 is additive.
"""
import html
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import render

W, H, FPS = 1080, 1920, 30
HERE = render.RENDER_DIR / "motion"
THEME_FONTS = Path(__file__).resolve().parent / "cloud" / "planner" / "fonts"
FONT_FILES = ["barlow-latin-500-normal.woff2", "barlow-latin-600-normal.woff2", "ibm-plex-sans-latin-400-normal.woff2",
              "ibm-plex-sans-latin-500-normal.woff2", "ibm-plex-sans-latin-600-normal.woff2",
              "ibm-plex-mono-latin-500-normal.woff2"]
FAMILIES = ("Barlow", "IBM Plex Sans", "IBM Plex Mono")
MARK = render.RENDER_DIR / "carousel" / "um-mark.svg"
ICONS = {k: v for k, v in json.loads((HERE / "icons.json").read_text(encoding="utf-8")).items() if not k.startswith("_")}

# Instagram covers the top ~150 px (the bar) and the bottom ~250 px (caption, audio), and its
# like / comment / share buttons sit down the right-hand side. Text and drawings stay inside SAFE
# and out of BUTTONS (x beyond, y below); the stage each scene lays out in sits well inside both.
SAFE = {"left": 64, "top": 150, "right": W - 64, "bottom": H - 250}
BUTTONS = {"x": 960, "y": 1100}
STAGES = {"full": (96, 300, 888, 1260), "panel": (96, 236, 888, 868), "cutaway": (96, 250, 888, 880)}
FIG_MAX = {"full": 920, "panel": 600, "cutaway": 620}   # the tallest a scene's drawing gets, leaving room for its title
CARD_BOTTOM = 1150  # an insert's card ends here, above the editor's caption band (editor.CARD_BOTTOM)

# Colours a spec may name (freeform drawings, panels): the tokens in motion.css.
TOKENS = {"ink", "ink-soft", "paper", "paper-mid", "card", "line", "line-soft", "sage", "sage-deep", "sage-tint",
          "beige", "beige-deep", "beige-tint", "orange", "orange-fill", "orange-tint"}
# Materials for a cross-section (and freeform fills): patterns in the page's defs.
MATERIALS = {"concrete", "screed", "tile", "timber", "vinyl", "adhesive", "membrane"}
TONES = TOKENS | MATERIALS

MIN_TOTAL, MAX_TOTAL = 5.0, 60.0
MIN_SCENE, MAX_SCENE = 1.5, 10.0
MAX_SCENES = 14
WORDS_PER_SECOND = 4.5   # reading pace (a Reel's: people replay): a scene stays up at least words / this + ENTRANCE
ENTRANCE = 0.8
GLANCE = 0.5             # a drawing's labels (room names, layers, dimensions) are glanced at: half a word each
GENERIC_ASKS = ("Follow for more", "Save this for later", "Save this", "Send this to someone renovating",
                "Which would you pick?")
_PRICE = re.compile(r"(\$\s?\d|\bS\$|\bSGD\b|\bUSD\b|\d\s?(?:k|K)\b|\bper\s+sq|\bpsf\b|\bdiscount|\bpromo|% off)", re.I)
_CTRL = re.compile(r"[\x00-\x09\x0b-\x1f\x7f​-‏  ‪-‮⁦-⁩]")


class SpecError(ValueError):
    """A spec that can't be rendered; .problems lists every reason in plain words."""

    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


# ── The spec ──────────────────────────────────────────────────────────────────

SCENE_TYPES = {
    "headline": "kinetic headline, word by word: text (<=90 chars), optional kicker, emphasis (up to 3 words to "
                "underline), sub (<=80), image",
    "steps": "numbered steps or a checklist: title, items (2-5, each <=60 chars), style numbered|checklist",
    "layers": "a cross-section that builds up: title, layers listed TOP FIRST (2-6: label <=26, note <=22, "
              "size thin|medium|thick, tone concrete|screed|tile|timber|vinyl|adhesive|membrane|sage|beige, "
              "tag <=10 such as New), caption",
    "measure": "a dimension drawing in: title, value (<=12, e.g. 15 mm), label (<=60), kind step|width|height, "
               "left / right labels (<=20)",
    "before_after": "two panels, the after wiping in: title, before and after {label, text <=70, paths or image}",
    "compare": "A vs B table with ticks: title, columns (2 names <=14), rows (2-6: label <=30, a / b true, false "
               "or text <=12), winner 0|1",
    "plan": "a floor plan that draws itself: title, size {w,h} in plan units, rooms [{name,x,y,w,h}], items "
            "(furniture: shape rect|circle, x,y,w,h, label), openings [{kind door|window, x,y,len,along h|v}], "
            "highlight {x,y,w,h,label}, caption",
    "icons": "icon + label list or grid: title, items (2-6: icon from ICONS, label <=28, note <=50), numbered true|false",
    "stat": "a number or a quote: value (<=10) + unit + label (<=70) + source, or quote (<=140) + by",
    "timeline": "renovation phases: title, phases (2-6: label <=26, note <=26 such as Week 1-2)",
    "end": "the end card with the mark: ask (one of the planner's offers' asks), line (<=60), handle",
    "freeform": "a drawing the library lacks: title, caption, paths (1-24 SVG paths in a 1000x1000 box: d, stroke, "
                "fill, width 1-12, draw true|false), labels (0-8: text <=40, x, y, size s|m|l, anchor)",
}
DEFAULT_SECONDS = {"headline": 3.2, "steps": 4.5, "layers": 5.0, "measure": 4.0, "before_after": 4.5, "compare": 5.0,
                   "plan": 5.5, "icons": 5.5, "stat": 3.5, "timeline": 5.0, "end": 3.2, "freeform": 4.5}


class _Check:
    """Collects problems while cleaning one spec."""

    def __init__(self):
        self.problems: list[str] = []
        self.notes: list[str] = []

    def bad(self, where: str, what: str):
        self.problems.append(f"{where}: {what}")

    def text(self, obj: dict, key: str, where: str, limit: int, required: bool = False, lines: int = 1) -> str:
        v = obj.get(key)
        if v is None or (isinstance(v, str) and not v.strip()):
            if required:
                self.bad(where, f"{key} is missing")
            return ""
        if not isinstance(v, str):
            self.bad(where, f"{key} must be text")
            return ""
        v = _CTRL.sub("", v.replace("\r", ""))
        parts = [" ".join(p.split()) for p in v.split("\n")]
        parts = [p for p in parts if p][:lines]
        v = "\n".join(parts)
        if len(v) > limit:
            self.bad(where, f"{key} is {len(v)} characters; keep it to {limit}")
        if _PRICE.search(v):
            self.bad(where, f"{key} mentions a price or promotion (\"{v[:40]}\"); explainers never quote prices")
        return v

    def number(self, obj: dict, key: str, where: str, lo: float, hi: float, default=None):
        v = obj.get(key, default)
        if v is None:
            return None
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            self.bad(where, f"{key} must be a number")
            return default
        if v < lo or v > hi:
            self.bad(where, f"{key} must be between {lo:g} and {hi:g}")
            return min(max(v, lo), hi)
        return v

    def choice(self, obj: dict, key: str, where: str, options, default: str) -> str:
        v = obj.get(key, default)
        if v not in options:
            self.bad(where, f"{key} must be one of: {', '.join(sorted(options))}")
            return default
        return v

    def items(self, obj: dict, key: str, where: str, lo: int, hi: int) -> list:
        v = obj.get(key)
        if not isinstance(v, list):
            self.bad(where, f"{key} must be a list of {lo} to {hi}")
            return []
        if not lo <= len(v) <= hi:
            self.bad(where, f"{key} has {len(v)}; it takes {lo} to {hi}")
        return v[:hi]


def _words(*texts) -> int:
    return sum(len(t.split()) for t in texts if isinstance(t, str))


# A freeform drawing's paths: parsed, made absolute and rounded, every point kept inside the box.
_PATH_TOKEN = re.compile(r"[MmLlHhVvCcSsQqTtAaZz]|[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
_ARGS = {"M": 2, "L": 2, "H": 1, "V": 1, "C": 6, "S": 4, "Q": 4, "T": 2, "A": 7, "Z": 0}
MAX_SEGMENTS = 200


def clean_path(d: str, w: float = 1000, h: float = 1000, max_segments: int = MAX_SEGMENTS) -> str:
    """An SVG path's d, made safe: only path commands and numbers, absolute coordinates, every
    point (and control point) inside 0..w x 0..h, at most max_segments. Raises ValueError."""
    if not isinstance(d, str) or not d.strip():
        raise ValueError("a path is empty")
    if len(d) > 6000:
        raise ValueError("a path is too long")
    if re.sub(r"[\s,]", "", _PATH_TOKEN.sub("", d)):
        raise ValueError("a path has something other than path commands and numbers")
    tokens = _PATH_TOKEN.findall(d)
    out, i, cmd, x, y, sx, sy, n = [], 0, None, 0.0, 0.0, 0.0, 0.0, 0

    def num():
        nonlocal i
        if i >= len(tokens) or tokens[i].isalpha():
            raise ValueError("a path command is missing its numbers")
        v = float(tokens[i])
        i += 1
        if not math.isfinite(v):
            raise ValueError("a path has a number that isn't finite")
        return v

    def inside(px, py):
        if not (-0.01 <= px <= w + 0.01 and -0.01 <= py <= h + 0.01):
            raise ValueError(f"a path goes outside its {w:g}x{h:g} box ({px:g}, {py:g})")

    def f(v):
        return f"{round(v, 1):g}"

    while i < len(tokens):
        if tokens[i].isalpha():
            cmd = tokens[i]
            i += 1
        elif cmd is None:
            raise ValueError("a path must start with M, and numbers after Z need a command")
        if not out and cmd not in "Mm":
            raise ValueError("a path must start with M")
        up, rel = cmd.upper(), cmd.islower()
        if up == "Z":
            out.append("Z")
            x, y = sx, sy
            n += 1
            cmd = None
            continue
        bx, by = (x, y) if rel else (0.0, 0.0)
        a = [num() for _ in range(_ARGS[up])]
        if up == "H":
            x = a[0] + (x if rel else 0)
            inside(x, y)
            out.append(f"L{f(x)} {f(y)}")
        elif up == "V":
            y = a[0] + (y if rel else 0)
            inside(x, y)
            out.append(f"L{f(x)} {f(y)}")
        elif up == "A":
            rx, ry, rot, large, sweep, ex, ey = a
            if large not in (0, 1) or sweep not in (0, 1):
                raise ValueError("an arc's flags must be 0 or 1")
            rx, ry = min(abs(rx), w), min(abs(ry), h)
            x, y = ex + bx, ey + by
            inside(x, y)
            out.append(f"A{f(rx)} {f(ry)} {f(rot % 360)} {int(large)} {int(sweep)} {f(x)} {f(y)}")
        else:
            pts = [(a[k] + bx, a[k + 1] + by) for k in range(0, len(a), 2)]
            for px, py in pts:
                inside(px, py)
            x, y = pts[-1]
            out.append(up + " ".join(f"{f(px)} {f(py)}" for px, py in pts))
            if up == "M":
                sx, sy = x, y
                cmd = "l" if rel else "L"  # numbers after M's pair are lines
        n += 1
        if n > max_segments:
            raise ValueError(f"a path has more than {max_segments} segments")
    return " ".join(out)


def _clean_image(v, where: str, c: _Check, allow_files: bool) -> dict | None:
    """A picture for a scene: a footage still or an uploaded photo (as the carousel's slides take
    them), or a local file when the caller allows it (tests, inserts)."""
    if v is None:
        return None
    if not isinstance(v, dict):
        c.bad(where, "image must be an object")
        return None
    source = v.get("source")
    if source == "clip" and isinstance(v.get("clip_id"), int) and not isinstance(v.get("clip_id"), bool):
        at = v.get("at") if isinstance(v.get("at"), (int, float)) and not isinstance(v.get("at"), bool) else 0
        return {"source": "clip", "clip_id": v["clip_id"], "at": round(max(float(at), 0.0), 1)}
    if source == "photo" and isinstance(v.get("photo_id"), str) and re.fullmatch(r"[0-9a-f]{32}", v["photo_id"]):
        return {"source": "photo", "photo_id": v["photo_id"]}
    if source == "file" and allow_files and isinstance(v.get("path"), str):
        p = Path(v["path"])
        if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp") and p.is_file():
            return {"source": "file", "path": str(p.resolve())}
        c.bad(where, "image file must be an existing .jpg, .png or .webp")
        return None
    c.bad(where, "image must be a footage still {source: clip, clip_id, at} or a photo {source: photo, photo_id}")
    return None


def _clean_paths(raw, where: str, c: _Check, w: float, h: float, max_paths: int) -> list[dict]:
    paths, segments = [], 0
    for k, p in enumerate(c.items({"paths": raw}, "paths", where, 1, max_paths) if raw is not None else []):
        pw = f"{where}, path {k + 1}"
        if not isinstance(p, dict):
            c.bad(pw, "must be an object with d")
            continue
        try:
            d = clean_path(p.get("d"), w, h)
        except ValueError as e:
            c.bad(pw, str(e))
            continue
        segments += d.count(" ") + 1
        fill = p.get("fill", "none")
        if fill != "none" and fill not in TONES:
            c.bad(pw, "fill must be none or a colour/material name")
            fill = "none"
        stroke = p.get("stroke", "ink")
        if stroke != "none" and stroke not in TOKENS:
            c.bad(pw, "stroke must be none or a colour name")
            stroke = "ink"
        width = c.number(p, "width", pw, 1, 12, 4)
        paths.append({"d": d, "fill": fill, "stroke": stroke, "width": width, "draw": p.get("draw", True) is not False})
    if segments > 1500:
        c.bad(where, "the drawing is too detailed (keep it under 1500 path segments)")
    return paths


def _clean_scene(raw, i: int, c: _Check, allow_files: bool, asks: list[str], insert: bool = False) -> dict | None:
    where = f"scene {i + 1}"
    if not isinstance(raw, dict):
        c.bad(where, "must be an object")
        return None
    kind = raw.get("type")
    if kind not in SCENE_TYPES:
        c.bad(where, f"type must be one of: {', '.join(SCENE_TYPES)}")
        return None
    where = f"scene {i + 1} ({kind})"
    s = {"type": kind}
    dur = c.number(raw, "duration", where, MIN_SCENE, MAX_SCENE)
    s["kicker"] = c.text(raw, "kicker", where, 28)
    if kind in ("steps", "layers", "measure", "compare", "plan", "icons", "timeline"):
        s["title"] = c.text(raw, "title", where, 50, required=True, lines=2)
    elif kind in ("before_after", "freeform"):
        s["title"] = c.text(raw, "title", where, 50, lines=2)
    words = _words(s.get("title"), s.get("kicker"))

    if kind == "headline":
        s["text"] = c.text(raw, "text", where, 90, required=True, lines=3)
        s["sub"] = c.text(raw, "sub", where, 80, lines=2)
        em = raw.get("emphasis") or []
        if not isinstance(em, list) or len(em) > 3 or not all(isinstance(e, str) and 0 < len(e) <= 20 for e in em):
            c.bad(where, "emphasis must be up to 3 words from the text")
            em = []
        s["emphasis"] = [e.strip() for e in em]
        s["image"] = _clean_image(raw.get("image"), where, c, allow_files)
        words += _words(s["text"], s["sub"])
    elif kind == "steps":
        s["style"] = c.choice(raw, "style", where, {"numbered", "checklist"}, "numbered")
        s["items"] = [c.text({"item": t}, "item", f"{where}, item {k + 1}", 60, required=True, lines=2)
                      for k, t in enumerate(c.items(raw, "items", where, 2, 5))]
        words += _words(*s["items"])
    elif kind == "layers":
        layers = []
        for k, l in enumerate(c.items(raw, "layers", where, 2, 6)):
            lw = f"{where}, layer {k + 1}"
            l = l if isinstance(l, dict) else {}
            layers.append({"label": c.text(l, "label", lw, 26, required=True), "note": c.text(l, "note", lw, 22),
                           "size": c.choice(l, "size", lw, {"thin", "medium", "thick"}, "medium"),
                           "tone": c.choice(l, "tone", lw, TONES - {"paper", "card"}, "screed"),
                           "tag": c.text(l, "tag", lw, 10)})
        s["layers"] = layers
        s["caption"] = c.text(raw, "caption", where, 70, lines=2)
        words += sum(_words(l["label"], l["note"]) for l in layers) * GLANCE + _words(s["caption"])
    elif kind == "measure":
        s["value"] = c.text(raw, "value", where, 12, required=True)
        s["label"] = c.text(raw, "label", where, 60, required=True, lines=2)
        s["kind"] = c.choice(raw, "kind", where, {"step", "width", "height"}, "step")
        s["left"] = c.text(raw, "left", where, 20)
        s["right"] = c.text(raw, "right", where, 20)
        words += _words(s["label"]) + _words(s["value"], s["left"], s["right"]) * GLANCE
    elif kind == "before_after":
        for side in ("before", "after"):
            p = raw.get(side)
            sw = f"{where}, {side}"
            if not isinstance(p, dict):
                c.bad(sw, "is missing")
                p = {}
            panel = {"label": c.text(p, "label", sw, 14) or side.capitalize(),
                     "text": c.text(p, "text", sw, 70, required=True, lines=2),
                     "paths": _clean_paths(p.get("paths"), sw, c, 1000, 360, 12) if p.get("paths") is not None else [],
                     "image": _clean_image(p.get("image"), sw, c, allow_files)}
            s[side] = panel
            words += _words(panel["text"]) + _words(panel["label"]) * GLANCE
    elif kind == "compare":
        cols = c.items(raw, "columns", where, 2, 2)
        s["columns"] = [c.text({"name": n}, "name", f"{where}, column {k + 1}", 14, required=True)
                        for k, n in enumerate(cols)] or ["A", "B"]
        rows = []
        for k, r in enumerate(c.items(raw, "rows", where, 2, 6)):
            rw = f"{where}, row {k + 1}"
            r = r if isinstance(r, dict) else {}
            row = {"label": c.text(r, "label", rw, 30, required=True)}
            for side in ("a", "b"):
                v = r.get(side)
                row[side] = v if isinstance(v, bool) else c.text(r, side, rw, 12, required=True)
            rows.append(row)
        s["rows"] = rows
        winner = raw.get("winner")
        s["winner"] = winner if winner in (0, 1) and not isinstance(winner, bool) else None
        words += _words(*s["columns"]) + sum(_words(r["label"], *(v for v in (r["a"], r["b"]) if isinstance(v, str)))
                                             for r in rows)
    elif kind == "plan":
        size = raw.get("size") if isinstance(raw.get("size"), dict) else {}
        pw_, ph_ = c.number(size, "w", f"{where}, size", 10, 200, 100), c.number(size, "h", f"{where}, size", 10, 200, 100)
        s["size"] = {"w": pw_, "h": ph_}

        def box(o, bw, need_label=0):
            o = o if isinstance(o, dict) else {}
            b = {k: c.number(o, k, bw, 0, (pw_ if k in ("x", "w") else ph_), 0) for k in ("x", "y", "w", "h")}
            if b["x"] + b["w"] > pw_ + 0.01 or b["y"] + b["h"] > ph_ + 0.01:
                c.bad(bw, "goes outside the plan")
            if b["w"] <= 0 or b["h"] <= 0:
                c.bad(bw, "needs a width and a height")
            return b
        s["rooms"] = [{**box(r, f"{where}, room {k + 1}"),
                       "name": c.text(r if isinstance(r, dict) else {}, "name", f"{where}, room {k + 1}", 18)}
                      for k, r in enumerate(c.items(raw, "rooms", where, 1, 10))]
        items = []
        for k, it in enumerate(raw.get("items") or []):
            iw = f"{where}, item {k + 1}"
            if k >= 12:
                c.bad(where, "items takes at most 12")
                break
            it = it if isinstance(it, dict) else {}
            items.append({**box(it, iw), "shape": c.choice(it, "shape", iw, {"rect", "circle"}, "rect"),
                          "label": c.text(it, "label", iw, 14)})
        s["items"] = items
        openings = []
        for k, o in enumerate(raw.get("openings") or []):
            ow = f"{where}, opening {k + 1}"
            if k >= 10:
                c.bad(where, "openings takes at most 10")
                break
            o = o if isinstance(o, dict) else {}
            op = {"kind": c.choice(o, "kind", ow, {"door", "window"}, "window"),
                  "along": c.choice(o, "along", ow, {"h", "v"}, "h"),
                  "x": c.number(o, "x", ow, 0, pw_, 0), "y": c.number(o, "y", ow, 0, ph_, 0),
                  "len": c.number(o, "len", ow, 1, max(pw_, ph_), 8)}
            end = (op["x"] + op["len"], op["y"]) if op["along"] == "h" else (op["x"], op["y"] + op["len"])
            if end[0] > pw_ + 0.01 or end[1] > ph_ + 0.01:
                c.bad(ow, "goes outside the plan")
            openings.append(op)
        s["openings"] = openings
        hl = raw.get("highlight")
        s["highlight"] = ({**box(hl, f"{where}, highlight"), "label": c.text(hl, "label", f"{where}, highlight", 20)}
                          if isinstance(hl, dict) else None)
        s["caption"] = c.text(raw, "caption", where, 70, lines=2)
        words += (sum(_words(r["name"]) for r in s["rooms"]) + sum(_words(it["label"]) for it in items)) * GLANCE \
            + _words(s["caption"], (s["highlight"] or {}).get("label"))
    elif kind == "icons":
        items = []
        for k, it in enumerate(c.items(raw, "items", where, 2, 6)):
            iw = f"{where}, item {k + 1}"
            it = it if isinstance(it, dict) else {}
            icon = it.get("icon")
            if icon not in ICONS:
                c.bad(iw, f"icon must be one of: {', '.join(sorted(ICONS))}")
                icon = "check"
            items.append({"icon": icon, "label": c.text(it, "label", iw, 28, required=True),
                          "note": c.text(it, "note", iw, 50, lines=2)})
        s["items"] = items
        s["numbered"] = raw.get("numbered") is True
        words += sum(_words(it["label"], it["note"]) for it in items)
    elif kind == "stat":
        if raw.get("quote"):
            s["quote"] = c.text(raw, "quote", where, 140, required=True, lines=3)
            s["by"] = c.text(raw, "by", where, 40)
            words += _words(s["quote"], s["by"])
        else:
            s["value"] = c.text(raw, "value", where, 10, required=True)
            s["unit"] = c.text(raw, "unit", where, 8)
            s["label"] = c.text(raw, "label", where, 70, required=True, lines=2)
            s["source"] = c.text(raw, "source", where, 40)
            words += _words(s["value"], s["unit"], s["label"], s["source"])
    elif kind == "timeline":
        phases = []
        for k, p in enumerate(c.items(raw, "phases", where, 2, 6)):
            pw = f"{where}, phase {k + 1}"
            p = p if isinstance(p, dict) else {}
            phases.append({"label": c.text(p, "label", pw, 26, required=True), "note": c.text(p, "note", pw, 26)})
        s["phases"] = phases
        words += sum(_words(p["label"], p["note"]) for p in phases)
    elif kind == "end":
        s["ask"] = c.text(raw, "ask", where, 40, required=True)
        if s["ask"] and s["ask"].lower() not in [a.lower() for a in asks]:
            c.bad(where, f"ask must be one of the planner's offers or a plain ask: {', '.join(asks)}")
        s["line"] = c.text(raw, "line", where, 60, lines=2)
        s["handle"] = c.text(raw, "handle", where, 30)
        if s["handle"] and not re.fullmatch(r"@?[A-Za-z0-9._]{2,30}", s["handle"]):
            c.bad(where, "handle must be an Instagram handle")
        words += _words(s["ask"], s["line"])
    elif kind == "freeform":
        s["paths"] = _clean_paths(raw.get("paths"), where, c, 1000, 1000, 24)
        labels = []
        for k, l in enumerate(raw.get("labels") or []):
            lw = f"{where}, label {k + 1}"
            if k >= 8:
                c.bad(where, "labels takes at most 8")
                break
            l = l if isinstance(l, dict) else {}
            labels.append({"text": c.text(l, "text", lw, 40, required=True), "x": c.number(l, "x", lw, 0, 1000, 500),
                           "y": c.number(l, "y", lw, 0, 1000, 500), "size": c.choice(l, "size", lw, {"s", "m", "l"}, "m"),
                           "anchor": c.choice(l, "anchor", lw, {"start", "middle", "end"}, "middle"),
                           "colour": c.choice(l, "colour", lw, TOKENS, "ink")})
        s["labels"] = labels
        s["caption"] = c.text(raw, "caption", where, 70, lines=2)
        words += sum(_words(l["text"]) for l in labels) * GLANCE + _words(s["caption"])

    need = round(words / WORDS_PER_SECOND + ENTRANCE + (0.4 if kind in ("plan", "layers", "measure", "before_after") else 0), 1)
    s["duration"] = dur if dur is not None else max(DEFAULT_SECONDS[kind], min(need, MAX_SCENE))
    if s["duration"] < need and insert:  # over a talking head, Bryan's voice carries it: noted, not stretched
        c.notes.append(f"{where}: {s['duration']:g} s is short to read {round(words)} words; fine while Bryan explains it")
    elif s["duration"] < need:
        c.notes.append(f"{where}: {s['duration']:g} s is short for {round(words)} words; made it {min(need, MAX_SCENE):g} s")
        s["duration"] = min(need, MAX_SCENE)
    s["duration"] = round(float(s["duration"]), 2)
    return s


def offer_asks(offers: list[dict] | None = None) -> list[str]:
    """The asks an end card may make: the planner's offers (content_plan) and the plain asks."""
    if offers is None:
        import content_plan
        offers = content_plan.DEFAULT_OFFERS
    asks = []
    for o in offers:
        for k in ("ask", "name"):
            if isinstance(o.get(k), str) and o[k].strip():
                asks.append(o[k].strip())
    return asks + list(GENERIC_ASKS)


def validate(spec, offers: list[dict] | None = None, allow_files: bool = False, insert: bool = False) -> dict:
    """The spec, cleaned (text tidied, defaults filled, durations long enough to read, paths made
    safe), with "notes" on what was adjusted. Raises SpecError with every problem found. insert:
    one scene laid over a talking-head Reel (no end card, MIN_SCENE to MAX_SCENE long)."""
    c = _Check()
    if not isinstance(spec, dict):
        raise SpecError(["the spec must be a JSON object with scenes"])
    out = {"version": 1, "title": c.text(spec, "title", "spec", 80),
           "kind": c.choice(spec, "kind", "spec", {"knowhow", "ideas", "onsite", "finished", "offer"}, "knowhow")}
    raw = spec.get("scenes")
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_SCENES:
        raise SpecError([f"spec: scenes must be a list of 1 to {MAX_SCENES}"])
    asks = offer_asks(offers)
    if insert and (len(raw) != 1 or (isinstance(raw[0], dict) and raw[0].get("type") == "end")):
        raise SpecError(["spec: an insert is one scene, and not an end card"])
    scenes = [s for s in (_clean_scene(r, i, c, allow_files, asks, insert) for i, r in enumerate(raw)) if s]
    ends = [i for i, s in enumerate(scenes) if s["type"] == "end"]
    if len(ends) > 1 or (ends and ends[0] != len(scenes) - 1):
        c.bad("spec", "there can be one end card, and it comes last")
    total = round(sum(s["duration"] for s in scenes), 2)
    if scenes and not insert and not MIN_TOTAL <= total <= MAX_TOTAL:
        c.bad("spec", f"the explainer runs {total:g} s; keep it between {MIN_TOTAL:g} and {MAX_TOTAL:g} s")
    if c.problems:
        raise SpecError(c.problems)
    out.update(scenes=scenes, duration=total, notes=c.notes)
    return out


# ── Layout: the library's scenes ──────────────────────────────────────────────

KIND_LABELS = {"knowhow": "Know-how", "ideas": "Ideas", "onsite": "On site", "finished": "Finished homes",
               "offer": "Work with us"}
_ARROW = ('<svg viewBox="0 0 40 26" aria-hidden="true"><path d="M2 13h33M25 3l10 10-10 10" fill="none" '
          'stroke="currentColor" stroke-width="3.6" stroke-linecap="round" stroke-linejoin="round"/></svg>')


def _e(t: str) -> str:
    return html.escape(t or "", quote=True)


def _lines(t: str) -> str:
    return "<br>".join(_e(x) for x in (t or "").split("\n"))


def _a(move: str, at: float, d: float | None = None, origin: str | None = None, extra: str = "") -> str:
    """The attributes that make an element move (motion.js reads them)."""
    s = f' data-a="{move}" data-at="{max(at, 0):.2f}"'
    if d is not None:
        s += f' data-d="{d:.2f}"'
    if origin:
        s += f' data-origin="{origin}"'
    return s + extra


def mark_svg() -> str:
    """The Urban Makers mark, inline (render/carousel/um-mark.svg, from Bryan)."""
    svg = MARK.read_text(encoding="utf-8")
    return re.sub(r'\s(width|height)="[^"]*"', "", svg, count=2).replace("<svg", '<svg aria-hidden="true"', 1)


def _wrap(text: str, chars: int) -> list[str]:
    """Words into lines of about `chars` characters (for SVG text, which doesn't wrap)."""
    lines, cur = [], ""
    for word in (text or "").split():
        if cur and len(cur) + 1 + len(word) > chars:
            lines.append(cur)
            cur = word
        else:
            cur = f"{cur} {word}".strip()
    return lines + ([cur] if cur else [])


def _box(x: float, y: float, w: float, h: float, r: float = 0) -> str:
    """A rectangle as a path (only paths draw on reliably), clockwise from the top left."""
    if not r:
        return f"M{x:g} {y:g} H{x + w:g} V{y + h:g} H{x:g} Z"
    return (f"M{x + r:g} {y:g} H{x + w - r:g} A{r} {r} 0 0 1 {x + w:g} {y + r:g} V{y + h - r:g} "
            f"A{r} {r} 0 0 1 {x + w - r:g} {y + h:g} H{x + r:g} A{r} {r} 0 0 1 {x:g} {y + h - r:g} V{y + r:g} "
            f"A{r} {r} 0 0 1 {x + r:g} {y:g} Z")


def _tone(t: str) -> str:
    return f"url(#p-{t})" if t in MATERIALS else f"var(--{t})"


def _head(s: dict, at: float = 0.0, title_px: int = 76) -> str:
    kicker = (f'<div class="kicker t"{_a("fade", at)}><span class="kbar"{_a("grow-x", at)}></span>{_e(s["kicker"])}</div>'
              if s.get("kicker") else "")
    title = (f'<h2 class="title t fit" style="font-size:{title_px}px"{_a("rise", at + 0.08)}>{_lines(s["title"])}</h2>'
             if s.get("title") else "")
    return f'<div class="head">{kicker}{title}</div>' if kicker or title else ""


def _pace(d: float, n: int, start: float, end_room: float = 1.0, most: float = 0.6) -> float:
    """Seconds between n staggered entrances so the last lands with end_room to spare."""
    return max(0.12, min(most, (d - start - end_room) / max(n, 1)))


def _headline(s: dict, d: float, ctx: dict) -> str:
    text = s["text"]
    n = len(text.replace("\n", " "))
    px = 140 if n <= 22 else 124 if n <= 36 else 110 if n <= 56 else 94
    em = {re.sub(r"\W", "", e).lower() for e in s["emphasis"]}
    words, k, gap = [], 0, min(0.09, 1.1 / max(len(text.split()), 1))
    lines = text.split("\n")
    for li, line in enumerate(lines):
        if li:
            words.append("</span><br>")
        if len(lines) > 1:  # lines the writer broke stay whole: the fit step shrinks the type instead
            words.append('<span class="ln">')
        for word in line.split():
            at = 0.2 + k * gap
            mark = (f'<span class="mark"{_a("grow-x", at + 0.55, 0.55)}></span>'
                    if re.sub(r"\W", "", word).lower() in em else "")
            words.append(f'<span class="w{" em" if mark else ""}">{mark}<span class="wi"{_a("rise-word", at)}>{_e(word)}'
                         f'</span></span> ')
            k += 1
    if len(lines) > 1:
        words.append("</span>")
    landed = 0.2 + k * gap + 0.6
    photo = ""
    if s.get("image") and ctx["images"].get(id(s["image"])):
        photo = (f'<div class="photo" data-box style="height:{560 if ctx["mode"] == "full" else 340}px"'
                 f'{_a("wipe-up", 0, 0.9)}><img src="{_e(ctx["images"][id(s["image"])])}" alt=""></div>')
    kicker = (f'<div class="kicker t" style="margin-bottom:40px"{_a("fade", 0)}><span class="kbar"{_a("grow-x", 0)}></span>'
              f'{_e(s["kicker"])}</div>' if s.get("kicker") else "")
    sub = f'<p class="sub t fit"{_a("rise", landed)}>{_lines(s["sub"])}</p>' if s.get("sub") else ""
    return (f'<div class="stage center">{photo}<div>{kicker}<h1 class="hl t fit" style="font-size:{px}px">{"".join(words)}</h1>'
            f'{sub}</div></div>')


def _steps(s: dict, d: float, ctx: dict) -> str:
    items = s["items"]
    step = _pace(d, len(items), 0.5, most=0.55)
    rows = []
    for k, item in enumerate(items):
        at = 0.5 + k * step
        if s["style"] == "checklist":
            mark = (f'<svg viewBox="0 0 96 96"><path d="{_box(10, 10, 76, 76, 12)}" fill="var(--card)" '
                    f'stroke="var(--ink)" stroke-width="3" pathLength="1"{_a("draw", at, 0.5)}/>'
                    f'<path d="M28 49l13 13 27-29" fill="none" stroke="var(--sage-deep)" stroke-width="7" '
                    f'stroke-linecap="round" stroke-linejoin="round" pathLength="1"{_a("draw", at + 0.35, 0.45)}/></svg>')
        else:
            mark = ('<svg viewBox="0 0 96 96"><path d="M48 3 A45 45 0 1 1 48 93 A45 45 0 1 1 48 3" fill="var(--card)" '
                    f'stroke="var(--ink)" stroke-width="3" pathLength="1"{_a("draw", at, 0.6)}/></svg>'
                    f'<b class="t"{_a("fade", at + 0.2)}>{k + 1}</b>')
        link = (f'<span class="link"{_a("grow-y", at + 0.35, 0.5, "top center")}></span>' if k < len(items) - 1 else "")
        rows.append(f'<li class="step"><span class="num" data-box>{mark}</span>{link}'
                    f'<span class="txt t fit"{_a("slide-r", at + 0.1)}>{_lines(item)}</span></li>')
    return f'<div class="stage">{_head(s)}<div class="body"><ol class="steps">{"".join(rows)}</ol></div></div>'


SIZE_PX = {"thin": 80, "medium": 146, "thick": 210}


def _layers(s: dict, d: float, ctx: dict) -> str:
    layers = s["layers"]              # top first
    bw, lx, fw = 400, 470, 888        # the section's width, the labels' x, the figure's width
    heights = [SIZE_PX[l["size"]] for l in layers]
    top = 10
    ys, y = [], top
    for h_ in heights:
        ys.append(y)
        y += h_
    fig_h = y + 10
    # Labels: two lines at most, then spread so none overlap (each wants its band's middle).
    blocks = []
    for l in layers:
        label = _wrap(l["label"], 17)[:2]
        blocks.append((label, l["note"], len(label) * 50 + (46 if l["note"] else 0)))
    want = [ys[k] + heights[k] / 2 - blocks[k][2] / 2 for k in range(len(layers))]
    pos = want[:]
    for _ in range(50):
        moved = False
        for k in range(1, len(pos)):
            gap = pos[k] - (pos[k - 1] + blocks[k - 1][2] + 18)
            if gap < 0:
                pos[k] -= gap / 2
                pos[k - 1] += gap / 2
                moved = True
        pos = [min(max(p, 0), fig_h - blocks[k][2]) for k, p in enumerate(pos)]
        if not moved:
            break
    fig_h = max(fig_h, max(p + b[2] for p, b in zip(pos, blocks)) + 4)
    bands, labels = [], []
    n = len(layers)
    step = _pace(d, n, 0.55, end_room=1.8, most=0.42)
    for order, k in enumerate(range(n - 1, -1, -1)):   # build from the bottom up
        l, at = layers[k], 0.55 + order * step
        y0, h_ = ys[k], heights[k]
        tag = ""
        if l["tag"]:
            tw = len(l["tag"]) * 18 + 34
            tag = (f'<rect x="18" y="{y0 + h_ / 2 - 21}" width="{tw}" height="42" rx="6" fill="var(--sage-deep)"/>'
                   f'<text class="svt t" x="{18 + tw / 2}" y="{y0 + h_ / 2 + 9}" text-anchor="middle" '
                   f'style="font:500 25px var(--mono);letter-spacing:.12em">{_e(l["tag"].upper())}</text>')
        bands.append(f'<g data-box{_a("rise", at, 0.55)}><rect x="0" y="{y0}" width="{bw}" height="{h_}" '
                     f'fill="{_tone(l["tone"])}" stroke="var(--ink)" stroke-width="2.5"/>{tag}</g>')
        label, note, bh = blocks[k]
        ly = pos[k]
        cy = y0 + h_ / 2
        mid = ly + 24
        leader = (f'<path d="M{bw + 6} {cy:.1f} L{bw + 34} {cy:.1f} L{lx - 18} {mid:.1f} L{lx - 6} {mid:.1f}" fill="none" '
                  f'stroke="var(--ink-soft)" stroke-width="2" pathLength="1"{_a("draw", at + 0.25, 0.45)}/>'
                  f'<circle cx="{bw - 10}" cy="{cy:.1f}" r="5" fill="var(--ink)"{_a("fade", at + 0.2, 0.3)}/>')
        text = "".join(f'<tspan x="{lx}" dy="{0 if j == 0 else 50}">{_e(t)}</tspan>' for j, t in enumerate(label))
        lab = f'<text class="svl t" x="{lx}" y="{ly + 40:.1f}">{text}</text>'
        if note:
            lab += f'<text class="svn t" x="{lx}" y="{ly + len(label) * 50 + 38:.1f}">{_e(note)}</text>'
        labels.append(f'{leader}<g{_a("slide-r", at + 0.35, 0.5)}>{lab}</g>')
    caption = (f'<p class="note t fit"{_a("rise", 0.55 + n * step + 0.2)}>{_lines(s["caption"])}</p>'
               if s.get("caption") else "")
    svg = (f'<svg viewBox="-6 -6 {fw + 12} {fig_h + 12:.0f}" preserveAspectRatio="xMidYMid meet">'
           f'{"".join(bands)}{"".join(labels)}</svg>')
    return f'<div class="stage">{_head(s)}<div class="fig">{svg}</div>{caption}</div>'


def _dim_ticks(x1, y1, x2, y2, at) -> str:
    """Architectural dimension ticks (short 45-degree strokes) at both ends of a dimension line."""
    t = "".join(f'<path d="M{x - 11} {y + 11} L{x + 11} {y - 11}" stroke="var(--orange)" stroke-width="4" '
                f'stroke-linecap="round" pathLength="1"{_a("draw", at, 0.25)}/>' for x, y in ((x1, y1), (x2, y2)))
    return t


def _value(v: str) -> str:
    """A measurement as SVG text: the number big, its unit smaller beside it ("15" "mm")."""
    m = re.fullmatch(r"([\d.,/]+)\s*(\D.*)?", v.strip())
    if not m or not m.group(2):
        return _e(v)
    return f'{_e(m.group(1))}<tspan dx="14" style="font-size:.5em">{_e(m.group(2))}</tspan>'


def _measure(s: dict, d: float, ctx: dict) -> str:
    kind = s["kind"]
    parts = []
    if kind == "step":
        # A section through a doorway: the slab, the old floor both sides, the new layer on the left.
        # The step is drawn larger than life so it reads on a phone; the value says the real size.
        vb = "0 0 888 640"
        hi, lo, door = 250, 330, 400   # the two floor levels and the doorway's x
        parts.append(f'<path d="M{door} {hi} V30" stroke="var(--line)" stroke-width="3" stroke-dasharray="10 12" fill="none"'
                     f'{_a("fade", 0.3, 0.5)}/>')
        parts.append(f'<text class="svc t" x="{door}" y="12" text-anchor="middle"{_a("fade", 0.3)}>Doorway</text>')
        parts.append(f'<g data-box{_a("rise", 0.3, 0.6)}><rect x="0" y="400" width="888" height="160" fill="url(#p-concrete)" '
                     'stroke="var(--ink)" stroke-width="2.5"/></g>')
        parts.append(f'<g{_a("fade", 0.55, 0.5)}><rect x="0" y="{lo}" width="888" height="70" fill="url(#p-tile)" '
                     'stroke="var(--ink)" stroke-width="2.5"/></g>')
        parts.append(f'<g{_a("grow-y", 0.9, 0.6, "50% 100%")}><rect x="0" y="{hi}" width="{door}" height="{lo - hi}" '
                     'fill="url(#p-vinyl)" stroke="var(--ink)" stroke-width="2.5"/></g>')
        if s.get("left"):
            parts.append(f'<text class="svc t" x="0" y="606"{_a("fade", 1.2)}>{_e(s["left"])}</text>')
        if s.get("right"):
            parts.append(f'<text class="svc t" x="850" y="606" text-anchor="end"{_a("fade", 1.2)}>{_e(s["right"])}</text>')
        dx = 520
        parts.append(f'<path d="M{door + 4} {hi} H{dx + 30} M{dx - 30} {lo} H{dx + 30}" stroke="var(--orange)" '
                     f'stroke-width="2.5" fill="none" pathLength="1"{_a("draw", 1.5, 0.5)}/>')
        parts.append(f'<path d="M{dx} {hi - 40} V{lo + 40}" stroke="var(--orange)" stroke-width="4" fill="none" '
                     f'pathLength="1"{_a("draw", 1.8, 0.45)}/>')
        parts.append(_dim_ticks(dx, hi, dx, lo, 2.1))
        parts.append(f'<text class="svv t" x="{dx + 44}" y="{(hi + lo) / 2 + 34}"{_a("slide-r", 2.2)}>{_value(s["value"])}</text>')
    elif kind == "width":
        vb = "0 0 888 520"
        parts.append(f'<g data-box{_a("rise", 0.3)}><rect x="40" y="60" width="70" height="400" fill="url(#p-concrete)" '
                     'stroke="var(--ink)" stroke-width="2.5"/><rect x="778" y="60" width="70" height="400" '
                     'fill="url(#p-concrete)" stroke="var(--ink)" stroke-width="2.5"/></g>')
        parts.append(f'<path d="M0 460 H888" stroke="var(--ink)" stroke-width="3" pathLength="1"{_a("draw", 0.5, 0.6)}/>')
        parts.append(f'<path d="M110 300 H778" stroke="var(--orange)" stroke-width="4" fill="none" pathLength="1"'
                     f'{_a("draw", 1.2, 0.6)}/>')
        parts.append(_dim_ticks(110, 300, 778, 300, 1.6))
        parts.append(f'<text class="svv t" x="444" y="262" text-anchor="middle"{_a("rise", 1.8)}>{_value(s["value"])}</text>')
        if s.get("left"):
            parts.append(f'<text class="svc t" x="130" y="420"{_a("fade", 1.0)}>{_e(s["left"])}</text>')
        if s.get("right"):
            parts.append(f'<text class="svc t" x="758" y="420" text-anchor="end"{_a("fade", 1.0)}>{_e(s["right"])}</text>')
    else:  # height
        vb = "0 0 888 640"
        parts.append(f'<path d="M0 600 H888" stroke="var(--ink)" stroke-width="3" pathLength="1"{_a("draw", 0.3, 0.6)}/>')
        parts.append(f'<g data-box{_a("rise", 0.4)}><rect x="0" y="20" width="888" height="40" fill="url(#p-screed)" '
                     'stroke="var(--ink)" stroke-width="2.5"/></g>')
        parts.append(f'<path d="M300 60 V600" stroke="var(--orange)" stroke-width="4" fill="none" pathLength="1"'
                     f'{_a("draw", 1.1, 0.6)}/>')
        parts.append(_dim_ticks(300, 60, 300, 600, 1.5))
        parts.append(f'<text class="svv t" x="344" y="360"{_a("slide-r", 1.7)}>{_value(s["value"])}</text>')
        if s.get("left"):
            parts.append(f'<text class="svc t" x="24" y="100"{_a("fade", 1.0)}>{_e(s["left"])}</text>')
        if s.get("right"):
            parts.append(f'<text class="svc t" x="24" y="580"{_a("fade", 1.0)}>{_e(s["right"])}</text>')
    label = f'<p class="note t fit" style="color:var(--ink);font-size:48px"{_a("rise", 2.5)}>{_lines(s["label"])}</p>'
    svg = f'<svg viewBox="{vb}" preserveAspectRatio="xMidYMid meet">{"".join(parts)}</svg>'
    return f'<div class="stage">{_head(s)}<div class="fig">{svg}</div>{label}</div>'


def _paths_svg(paths: list[dict], at: float, step: float) -> str:
    out = []
    for k, p in enumerate(paths):
        fill = "none" if p["fill"] == "none" else _tone(p["fill"])
        stroke = "none" if p["stroke"] == "none" else f"var(--{p['stroke']})"
        common = (f'd="{p["d"]}" fill="{fill}" stroke="{stroke}" stroke-width="{p["width"]:g}" stroke-linecap="round" '
                  'stroke-linejoin="round"')
        t = at + k * step
        if p["draw"] and p["stroke"] != "none" and p["fill"] == "none":
            out.append(f'<path {common} pathLength="1"{_a("draw", t, 0.7)}/>')
        else:
            out.append(f'<path {common}{_a("fade", t, 0.5)}/>')
    return "".join(out)


def _before_after(s: dict, d: float, ctx: dict) -> str:
    switch = max(1.4, min(d * 0.45, d - 2.0))
    panels = []
    for side, at in (("before", 0.25), ("after", switch)):
        p = s[side]
        if p["image"] and ctx["images"].get(id(p["image"])):
            fig = f'<div class="pfig img" data-box><img src="{_e(ctx["images"][id(p["image"])])}" alt=""></div>'
        elif p["paths"]:
            fig = (f'<div class="pfig" data-box><svg viewBox="0 0 1000 360" preserveAspectRatio="xMidYMid meet">'
                   f'{_paths_svg(p["paths"], at + 0.3, 0.12)}</svg></div>')
        else:
            fig = ""
        move = "rise" if side == "before" else "wipe"
        dim = _a("dim", switch + 0.2, 0.5) if side == "before" else ""
        panels.append(f'<div class="panel {side}"{_a(move, at, 0.7 if side == "after" else 0.6)}><div{dim}>'
                      f'<span class="chip t">{_e(p["label"])}</span></div>'
                      f'<div{dim}>{fig}</div><p class="ptxt t fit"{dim}>{_lines(p["text"])}</p></div>')
    return f'<div class="stage">{_head(s)}<div class="body"><div class="pair">{"".join(panels)}</div></div></div>'


_TICK = '<path d="M14 31l11 11 21-23" fill="none" stroke="var(--sage-deep)" stroke-width="6" stroke-linecap="round" stroke-linejoin="round" pathLength="1"{a}/>'
_CROSS = '<path d="M18 18l24 24M42 18L18 42" fill="none" stroke="var(--beige-deep)" stroke-width="5" stroke-linecap="round" pathLength="1"{a}/>'


def _compare(s: dict, d: float, ctx: dict) -> str:
    rows = s["rows"]
    step = _pace(d, len(rows), 0.7, end_room=1.2, most=0.45)
    cells = ['<div class="ch"></div>'] + [f'<div class="ch t"{_a("rise", 0.3 + 0.1 * k)}>{_e(n)}</div>'
                                          for k, n in enumerate(s["columns"])]
    for k, r in enumerate(rows):
        at = 0.7 + k * step
        cells.append(f'<div class="rule"{_a("grow-x", at - 0.1, 0.5)}></div>')
        cells.append(f'<div class="rl t"{_a("slide-r", at)}>{_e(r["label"])}</div>')
        for j, side in enumerate(("a", "b")):
            v, t = r[side], at + 0.2 + 0.1 * j
            if v is True:
                inner = f'<svg viewBox="0 0 60 60" data-box>{_TICK.format(a=_a("draw", t, 0.45))}</svg>'
            elif v is False:
                inner = f'<svg viewBox="0 0 60 60" data-box>{_CROSS.format(a=_a("draw", t, 0.4))}</svg>'
            else:
                inner = f'<span class="t"{_a("fade", t)}>{_e(v)}</span>'
            cells.append(f'<div class="cell">{inner}</div>')
    win = ""
    if s["winner"] is not None:
        # the winning column (2nd or 3rd of 1.25fr 1fr 1fr) gets a soft sage band
        left = (1.25 + s["winner"]) / 3.25 * 100
        win = (f'<div class="win" style="left:calc({left:.3f}% + 6px);width:calc({100 / 3.25:.3f}% - 12px)"'
               f'{_a("grow-y", 0.5 + len(rows) * step, 0.7, "top center")}></div>')
    return f'<div class="stage">{_head(s)}<div class="body"><div class="cmp">{win}{"".join(cells)}</div></div></div>'


def _plan(s: dict, d: float, ctx: dict) -> str:
    pw, ph = s["size"]["w"], s["size"]["h"]
    max_h = 1000 if ctx["mode"] == "full" else 640
    k = min(888 / pw, max_h / ph)
    X = lambda v: round(v * k, 1)   # noqa: E731 (plan units to px)
    fw, fh = X(pw), X(ph)
    walls, labels, items, opens = [], [], [], []
    walls.append(f'<rect x="0" y="0" width="{fw}" height="{fh}" fill="var(--card)"{_a("fade", 0.3, 1.0)}/>'
                 f'<path d="{_box(0, 0, fw, fh)}" fill="none" stroke="var(--ink)" stroke-width="10" stroke-linejoin="round" '
                 f'pathLength="1"{_a("draw", 0.3, 1.2, extra=" data-box")}/>')
    rooms = s["rooms"]
    for i, r in enumerate(rooms):
        walls.append(f'<path d="{_box(X(r["x"]), X(r["y"]), X(r["w"]), X(r["h"]))}" fill="none" '
                     f'stroke="var(--ink)" stroke-width="5" pathLength="1"{_a("draw", 0.8 + i * 0.12, 0.9)}/>')
        if r["name"]:
            fs = 28 if X(r["w"]) > len(r["name"]) * 18 + 24 else 22
            labels.append(f'<text class="svc t" x="{X(r["x"] + r["w"] / 2)}" y="{X(r["y"] + r["h"] / 2) + 8}" '
                          f'text-anchor="middle" style="font-size:{fs}px"{_a("fade", 1.7 + i * 0.08)}>{_e(r["name"])}</text>')
    for i, o in enumerate(s["openings"]):
        x1, y1 = X(o["x"]), X(o["y"])
        x2, y2 = (X(o["x"] + o["len"]), y1) if o["along"] == "h" else (x1, X(o["y"] + o["len"]))
        at = 1.5 + i * 0.05
        cut = f'<path d="M{x1} {y1} L{x2} {y2}" stroke="var(--card)" stroke-width="12"/>'
        if o["kind"] == "window":
            off = 4
            dx, dy = (0, off) if o["along"] == "h" else (off, 0)
            glass = (f'<path d="M{x1 - dx} {y1 - dy} L{x2 - dx} {y2 - dy} M{x1 + dx} {y1 + dy} L{x2 + dx} {y2 + dy}" '
                     'stroke="var(--ink-soft)" stroke-width="2"/>')
            opens.append(f'<g{_a("fade", at, 0.4)}>{cut}{glass}</g>')
        else:
            r_ = X(o["len"])
            if o["along"] == "h":
                swing = f'M{x1} {y1} L{x1} {y1 + r_} A{r_} {r_} 0 0 0 {x2} {y1}'
            else:
                swing = f'M{x1} {y1} L{x1 + r_} {y1} A{r_} {r_} 0 0 1 {x1} {y2}'
            opens.append(f'<g{_a("fade", at, 0.4)}>{cut}<path d="{swing}" fill="none" stroke="var(--ink-soft)" '
                         'stroke-width="2"/></g>')
    for i, it in enumerate(s["items"]):
        at = 2.0 + i * 0.08
        if it["shape"] == "circle":
            shape = (f'<ellipse cx="{X(it["x"] + it["w"] / 2)}" cy="{X(it["y"] + it["h"] / 2)}" rx="{X(it["w"] / 2)}" '
                     f'ry="{X(it["h"] / 2)}" fill="var(--paper-mid)" stroke="var(--line)" stroke-width="3"/>')
        else:
            shape = (f'<rect x="{X(it["x"])}" y="{X(it["y"])}" width="{X(it["w"])}" height="{X(it["h"])}" rx="6" '
                     'fill="var(--paper-mid)" stroke="var(--line)" stroke-width="3"/>')
        lab = (f'<text class="svc t" x="{X(it["x"] + it["w"] / 2)}" y="{X(it["y"] + it["h"] / 2) + 7}" text-anchor="middle" '
               f'style="font-size:22px">{_e(it["label"])}</text>' if it["label"] else "")
        items.append(f'<g{_a("fade", at, 0.5)}>{shape}{lab}</g>')
    hl = ""
    h = s.get("highlight")
    if h:
        t0 = min(2.6, d - 2.0)
        x, y, w, hh = X(h["x"]), X(h["y"]), X(h["w"]), X(h["h"])
        hl = (f'<rect x="{x + 4}" y="{y + 4}" width="{w - 8}" height="{hh - 8}" rx="8" fill="var(--orange-tint)" '
              f'opacity=".9"{_a("fade", t0, 0.6)}/>'
              f'<path d="{_box(x + 4, y + 4, w - 8, hh - 8, 8)}" fill="none" stroke="var(--orange)" '
              f'stroke-width="5" pathLength="1"{_a("draw", t0, 0.9)}/>')
        if h["label"]:  # a tag in the middle of the highlight (above it when the highlight is too small)
            tw = len(h["label"]) * 16 + 36
            inside = tw <= w - 20 and hh >= 70
            tx = x + w / 2 - tw / 2 if inside else min(max(x + w / 2 - tw / 2, 0), fw - tw)
            ty = y + hh / 2 - 24 if inside else (y - 64 if y > 80 else y + hh + 16)
            hl += (f'<g{_a("pop", t0 + 0.6, 0.5)}><rect x="{tx}" y="{ty}" width="{tw}" height="48" rx="24" '
                   f'fill="var(--orange-fill)"/><text class="svt t" x="{tx + tw / 2}" y="{ty + 33}" text-anchor="middle">'
                   f'{_e(h["label"])}</text></g>')
    caption = (f'<p class="note t fit"{_a("rise", min(3.3, d - 1.4))}>{_lines(s["caption"])}</p>' if s.get("caption") else "")
    svg = (f'<svg viewBox="-8 -8 {fw + 16} {fh + 16}" preserveAspectRatio="xMidYMid meet">'
           f'{"".join(walls)}{"".join(opens)}{"".join(items)}{hl}{"".join(labels)}</svg>')
    return f'<div class="stage">{_head(s)}<div class="fig">{svg}</div>{caption}</div>'


def icon_svg(name: str, at: float | None = None, step: float = 0.12) -> str:
    """One of the library's line icons; with `at`, its strokes draw on one after another."""
    paths = []
    for k, d in enumerate(ICONS[name]):
        move = _a("draw", at + k * step, 0.6) if at is not None else ""
        paths.append(f'<path d="{d}" pathLength="1"{move}/>' if at is not None else f'<path d="{d}"/>')
    return f'<svg viewBox="0 0 48 48">{"".join(paths)}</svg>'


def _icons(s: dict, d: float, ctx: dict) -> str:
    items = s["items"]
    grid = len(items) in (4, 6)
    step = _pace(d, len(items), 0.5, end_room=1.4, most=0.75)
    out = []
    for k, it in enumerate(items):
        at = 0.5 + k * step
        note = f'<div class="in t fit"{_a("fade", at + 0.4)}>{_lines(it["note"])}</div>' if it["note"] else ""
        num = f'<div class="num-s t"{_a("fade", at + 0.15)}>{k + 1:02d}</div>' if s.get("numbered") else ""
        out.append(f'<div class="ic"><div class="tile" data-box{_a("pop", at, 0.5)}>{icon_svg(it["icon"], at + 0.15)}</div>'
                   f'<div>{num}<div class="il t fit"{_a("rise", at + 0.2)}>{_e(it["label"])}</div>{note}</div></div>')
    return (f'<div class="stage">{_head(s)}<div class="body"><div class="icons{" grid" if grid else ""}">'
            f'{"".join(out)}</div></div></div>')


def _stat(s: dict, d: float, ctx: dict) -> str:
    if s.get("quote"):
        by = f'<div class="src t"{_a("fade", 1.4)}>{_e(s["by"])}</div>' if s.get("by") else ""
        n = len(s["quote"])
        px = 76 if n <= 60 else 66 if n <= 100 else 58
        return (f'<div class="stage center">{_head(s)}<div class="quote"><div class="qm"{_a("fade", 0.1)}>&ldquo;</div>'
                f'<div class="qt t fit" style="font-size:{px}px"{_a("rise", 0.35, 0.8)}>{_lines(s["quote"])}</div>{by}</div></div>')
    value = s["value"]
    m = re.fullmatch(r"(\d{1,6})(?:\.(\d{1,2}))?", value)
    num = (f'<span{_a("count", 0.2, 1.1, extra=f" data-to={value} data-dec={len(m.group(2) or "")}")}>{_e(value)}</span>'
           if m else f'<span>{_e(value)}</span>')
    unit = f'<small>{_e(s["unit"])}</small>' if s.get("unit") else ""
    px = 250 if len(value) <= 4 else 190 if len(value) <= 7 else 150
    src = f'<div class="src t"{_a("fade", 1.5)}>{_e(s["source"])}</div>' if s.get("source") else ""
    return (f'<div class="stage center">{_head(s)}<div class="stat">'
            f'<div class="val t fit" style="font-size:{px}px"{_a("rise", 0.15, 0.7)}>{num}{unit}</div>'
            f'<div class="rule"{_a("grow-x", 0.7)}></div><div class="lbl t fit"{_a("rise", 0.9)}>{_lines(s["label"])}</div>'
            f'{src}</div></div>')


def _timeline(s: dict, d: float, ctx: dict) -> str:
    phases = s["phases"]
    step = _pace(d, len(phases), 0.5, end_room=1.2, most=0.6)
    rows = []
    for k, p in enumerate(phases):
        at = 0.5 + k * step
        note = f'<div class="pn t"{_a("fade", at + 0.25)}>{_e(p["note"])}</div>' if p["note"] else ""
        rows.append(f'<div class="ph{" last" if k == len(phases) - 1 else ""}"><span class="node"{_a("pop", at, 0.4)}></span>'
                    f'<div class="pl t fit"{_a("slide-r", at + 0.08)}>{_e(p["label"])}</div>{note}</div>')
    # The rail runs from the first node down to the last one, drawn down as the phases arrive.
    rail = (f'<span class="rail" style="top:30px;bottom:60px"{_a("grow-y", 0.45, step * len(phases), "top center")}></span>')
    return f'<div class="stage">{_head(s)}<div class="body"><div class="tl">{rail}{"".join(rows)}</div></div></div>'


def _end(s: dict, d: float, ctx: dict) -> str:
    line = f'<div class="ln t fit"{_a("rise", 0.75)}>{_lines(s["line"])}</div>' if s.get("line") else ""
    handle = f'<div class="hd t"{_a("fade", 1.4)}>{_e(s["handle"])}</div>' if s.get("handle") else ""
    return (f'<div class="end"{_a("wipe-up", 0, 0.75)}><div class="stage">'
            f'<div class="mk" data-box{_a("pop", 0.35, 0.6)}>{mark_svg()}</div>'
            f'<div class="nm t"{_a("fade", 0.55)}>Urban Makers</div>{line}'
            f'<div class="cta t"{_a("rise", 1.05)}>{_e(s["ask"])}{_ARROW}</div>{handle}</div></div>')


def _freeform(s: dict, d: float, ctx: dict) -> str:
    step = min(0.25, max(0.06, (d - 2.2) / max(len(s["paths"]), 1)))
    sizes = {"s": "sv-s", "m": "sv-m", "l": "sv-l"}
    labels = "".join(f'<text class="{sizes[l["size"]]} t" x="{l["x"]:g}" y="{l["y"]:g}" text-anchor="{l["anchor"]}" '
                     f'style="fill:var(--{l["colour"]})"{_a("fade", 0.6 + len(s["paths"]) * step + k * 0.12)}>{_e(l["text"])}</text>'
                     for k, l in enumerate(s["labels"]))
    svg = (f'<svg viewBox="0 0 1000 1000" preserveAspectRatio="xMidYMid meet" data-box>'
           f'{_paths_svg(s["paths"], 0.4, step)}{labels}</svg>')
    caption = (f'<p class="note t fit"{_a("rise", min(d - 1.4, 0.6 + len(s["paths"]) * step + 0.4))}>{_lines(s["caption"])}</p>'
               if s.get("caption") else "")
    return f'<div class="stage">{_head(s)}<div class="fig">{svg}</div>{caption}</div>'


BUILDERS = {"headline": _headline, "steps": _steps, "layers": _layers, "measure": _measure, "before_after": _before_after,
            "compare": _compare, "plan": _plan, "icons": _icons, "stat": _stat, "timeline": _timeline, "end": _end,
            "freeform": _freeform}

# Materials for cross-sections, as SVG patterns: aggregate in concrete, sand in screed, joints in
# tiles and vinyl planks, grain in timber.
_DEFS = """<svg class="defs" aria-hidden="true"><defs>
<pattern id="p-concrete" width="64" height="64" patternUnits="userSpaceOnUse"><rect width="64" height="64" fill="#E4DDD1"/>
<path d="M8 12l6-3 3 6zM40 8l7 2-3 6zM24 34l5-4 4 5zM52 40l6 3-5 4zM12 50l4 6-6 1zM34 54l6-2 1 6z" fill="#BFB3A1"/>
<circle cx="30" cy="16" r="2" fill="#B3A795"/><circle cx="6" cy="32" r="1.6" fill="#B3A795"/><circle cx="46" cy="26" r="1.6" fill="#B3A795"/>
<circle cx="58" cy="58" r="2" fill="#B3A795"/><circle cx="22" cy="58" r="1.4" fill="#B3A795"/></pattern>
<pattern id="p-screed" width="24" height="24" patternUnits="userSpaceOnUse"><rect width="24" height="24" fill="#EFEBE3"/>
<circle cx="5" cy="6" r="1.3" fill="#CFC6B8"/><circle cx="17" cy="14" r="1.3" fill="#CFC6B8"/><circle cx="9" cy="20" r="1" fill="#CFC6B8"/></pattern>
<pattern id="p-tile" width="110" height="200" patternUnits="userSpaceOnUse"><rect width="110" height="200" fill="#FFFFFF"/>
<rect x="107" width="3" height="200" fill="#C9C4B8"/></pattern>
<pattern id="p-vinyl" width="180" height="200" patternUnits="userSpaceOnUse"><rect width="180" height="200" fill="#C9C9B6"/>
<rect x="177" width="3" height="200" fill="#8F8F78"/></pattern>
<pattern id="p-timber" width="200" height="40" patternUnits="userSpaceOnUse"><rect width="200" height="40" fill="#DCC7AE"/>
<path d="M0 12c40-6 80 6 120 0s60-4 80 0M0 28c50 5 90-5 130 0s50 4 70 0" stroke="#BFA588" stroke-width="2" fill="none"/></pattern>
<pattern id="p-adhesive" width="16" height="16" patternUnits="userSpaceOnUse"><rect width="16" height="16" fill="#B9B3A6"/>
<path d="M0 16L16 0" stroke="#A29B8C" stroke-width="2"/></pattern>
<pattern id="p-membrane" width="20" height="20" patternUnits="userSpaceOnUse"><rect width="20" height="20" fill="#7E93A0"/>
<path d="M0 20L20 0" stroke="#6C808C" stroke-width="2"/></pattern>
</defs></svg>"""


def _scene_times(spec: dict) -> list[tuple[float, float]]:
    t, out = 0.0, []
    for s in spec["scenes"]:
        out.append((round(t, 3), s["duration"]))
        t += s["duration"]
    return out


def page_html(spec: dict, images: dict | None = None, mode: str = "full") -> str:
    """The composition: a cleaned spec (validate()) as one page for HyperFrames. mode "full" is a
    whole explainer; the inserts are one scene over a talking head, transparent around it:
    "cutaway" fills the frame with paper (the content above the caption band), "panel" puts it on a
    paper card over the top of the footage."""
    ctx = {"mode": mode, "images": images or {}}
    sx, sy, sw, sh = STAGES[mode]
    total = spec["duration"]
    times = _scene_times(spec)
    clips = []
    if mode == "full":
        clips.append(f'<div id="bg" class="bg clip" data-start="0" data-duration="{total:.3f}" data-track-index="0">'
                     f'<div class="grid"></div>{_DEFS}</div>')
        body_scenes = [s for s in spec["scenes"] if s["type"] != "end"]
        if body_scenes:
            end_at = times[len(body_scenes) - 1][0] + times[len(body_scenes) - 1][1]
            segs = "".join(f'<span class="seg" data-i="{i}"><b></b></span>' for i in range(len(body_scenes)))
            clips.append(f'<div id="hdr" class="hdr clip" data-start="0" data-duration="{end_at:.3f}" data-track-index="1">'
                         f'<div class="row"><span class="brand t">{mark_svg()}Urban Makers</span>'
                         f'<span class="kind t"><i></i>{_e(KIND_LABELS[spec["kind"]])}</span></div>'
                         f'<div class="segs">{segs}</div></div>')
    for i, (s, (start, dur)) in enumerate(zip(spec["scenes"], times)):
        inner = BUILDERS[s["type"]](s, dur, ctx)
        last = i == len(spec["scenes"]) - 1
        if mode == "panel":
            inner = f'<div class="pcard"{_a("rise", 0, 0.5)}><div class="grid"></div>{_DEFS}</div>{inner}'
        elif mode == "cutaway":
            inner = f'<div class="cut"{_a("fade", 0, 0.3)}><div class="grid"></div>{_DEFS}</div>{inner}'
        clips.append(f'<div id="s{i}" class="scene clip" data-i="{i}" data-start="{start:.3f}" data-duration="{dur:.3f}" '
                     f'data-track-index="{2 + i % 2}" data-exit="{0 if last and mode == "full" else 1}">'
                     f'<div class="inner">{inner}</div></div>')
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Studio explainer</title>'
            '<link rel="stylesheet" href="motion.css">'
            f'<style>:root{{--sx:{sx}px;--sy:{sy}px;--sw:{sw}px;--sh:{sh}px;--figmax:{FIG_MAX[mode]}px}}</style>'
            '<script src="gsap.min.js"></script></head>'
            f'<body class="mode-{mode}"><div id="main" data-composition-id="main" data-start="0" '
            f'data-duration="{total:.3f}" data-width="{W}" data-height="{H}" data-fps="{FPS}">{"".join(clips)}</div>'
            '<script src="motion.js"></script></body></html>')


# ── Rendering ─────────────────────────────────────────────────────────────────

def _env() -> dict:
    """HyperFrames without its usage pings: nothing leaves the machine while rendering."""
    return {**os.environ, "HYPERFRAMES_NO_TELEMETRY": "1", "DO_NOT_TRACK": "1", "HF_CLI_TELEMETRY_DISABLED": "1"}


def _images(spec: dict, project: Path, resolve_image=None, conn=None) -> dict:
    """Copies each scene's picture next to the page; returns {id(image dict): file name}. Footage
    stills and photos come from slide_images (they need the catalog, conn) unless resolve_image is given."""
    found = {}
    pics = []
    for s in spec["scenes"]:
        if s.get("image"):
            pics.append(s["image"])
        for side in ("before", "after"):
            if isinstance(s.get(side), dict) and s[side].get("image"):
                pics.append(s[side]["image"])
    for k, img in enumerate(pics):
        path = None
        if img["source"] == "file":
            path = Path(img["path"])
        elif resolve_image:
            path = resolve_image(img)
        else:
            import slide_images  # footage stills and uploaded photos, as on the carousel's slides
            path = slide_images.picture(img, conn)[0]
        if path and Path(path).is_file():
            name = f"img{k}{Path(path).suffix.lower()}"
            shutil.copy(path, project / name)
            found[id(img)] = name
    return found


def build_project(spec: dict, project: Path, mode: str = "full", resolve_image=None, conn=None) -> Path:
    """The HyperFrames project for a cleaned spec: the page, the library, GSAP and the fonts."""
    project.mkdir(parents=True, exist_ok=True)
    (project / "fonts").mkdir(exist_ok=True)
    for f in FONT_FILES:
        shutil.copy(THEME_FONTS / f, project / "fonts" / f)
    for f in ("motion.css", "motion.js"):
        shutil.copy(HERE / f, project / f)
    shutil.copy(render.RENDER_DIR / "node_modules" / "gsap" / "dist" / "gsap.min.js", project)
    images = _images(spec, project, resolve_image, conn)
    (project / "index.html").write_text(page_html(spec, images, mode), encoding="utf-8")
    (project / "meta.json").write_text(json.dumps({"id": "main", "name": spec.get("title") or "explainer",
                                                   "width": W, "height": H, "fps": FPS}), encoding="utf-8")
    return project


def check_times(spec: dict) -> list[dict]:
    """When to measure each scene: once its entrance has landed, and just before it leaves."""
    out = []
    for i, (start, dur) in enumerate(_scene_times(spec)):
        for t in sorted({round(start + max(dur * 0.62, min(3.4, dur - 0.6)), 2), round(start + dur - 0.5, 2)}):
            out.append({"scene": i, "t": t})
    return out


def check(project: Path, spec: dict, shots: Path | None = None) -> dict:
    """The render check (render/motion/check.mjs) on a built project: fonts, network, safe area,
    overlaps, fit. Returns its report; report["problems"] is empty when all is well. With
    `shots`, a PNG of each moment measured is saved there (a quick look without rendering)."""
    node = render._node()
    if not node:
        raise RuntimeError(f"Node.js not found; install it with: {render._NODE_FIX}")
    job = project / "check-job.json"
    job.write_text(json.dumps({"chrome": render.browser_path(), "html": str(project / "index.html"), "width": W, "height": H,
                               "safe": SAFE, "buttons": BUTTONS, "times": check_times(spec),
                               **({"shots": str(shots)} if shots else {})}), encoding="utf-8")
    result = subprocess.run([node, str(HERE / "check.mjs"), str(job)], cwd=HERE, capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=600, env=_env(), creationflags=render._NO_WINDOW)
    job.unlink(missing_ok=True)
    if result.returncode != 0:
        raise RuntimeError("the render check failed to run: " + (result.stderr or result.stdout).strip()[-400:])
    report = json.loads(result.stdout)
    families = {f["family"] for f in report["fonts"] if f["status"] == "loaded"}
    for fam in FAMILIES:
        if fam not in families:
            report["problems"].append(f"the font {fam} didn't load")
    for f in report["fonts"]:
        if f["status"] == "error":
            report["problems"].append(f"a font file failed: {f['family']} {f['weight']}")
    if report["refused"]:
        report["problems"].append("the page asked the network for: " + ", ".join(report["refused"][:3]))
    return report


def _ffmpeg() -> str:
    try:
        import settings
        return settings.FFMPEG
    except Exception:
        return shutil.which("ffmpeg") or "ffmpeg"


def probe(path: Path) -> dict:
    """Width, height, duration, frame rate and streams of a rendered file (ffprobe)."""
    ffprobe = str(Path(_ffmpeg()).with_name("ffprobe" + (".exe" if os.name == "nt" else ""))) \
        if Path(_ffmpeg()).is_absolute() else (shutil.which("ffprobe") or "ffprobe")
    out = subprocess.run([ffprobe, "-v", "error", "-show_entries",
                          "stream=codec_type,codec_name,width,height,r_frame_rate,pix_fmt:format=duration", "-of", "json",
                          str(path)], capture_output=True, text=True, creationflags=render._NO_WINDOW)
    info = json.loads(out.stdout or "{}")
    video = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), {})
    num, _, den = (video.get("r_frame_rate") or "0/1").partition("/")
    return {"width": video.get("width"), "height": video.get("height"), "codec": video.get("codec_name"),
            "pix_fmt": video.get("pix_fmt"), "fps": round(float(num) / float(den or 1), 2) if num else None,
            "duration": round(float(info.get("format", {}).get("duration") or 0), 2),
            "audio": any(s.get("codec_type") == "audio" for s in info.get("streams", []))}


def add_sound(video: Path, out: Path, total: float, music: Path | None = None, music_lufs: float = -16.0) -> None:
    """The finished file: H.264 for Instagram with a music bed (looped, faded in and out, set to
    music_lufs; nobody talks, so the music carries it) or a silent track."""
    ff = _ffmpeg()
    if music:
        audio = ["-stream_loop", "-1", "-i", str(music)]
        fade = max(total - 1.5, 0)
        af = (f"[1:a]aresample=48000,aformat=channel_layouts=stereo,afade=t=in:d=0.6,afade=t=out:st={fade:.3f}:d=1.5,"
              f"loudnorm=I={music_lufs}:TP=-1.5:LRA=11[a]")
    else:
        audio = ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]
        af = "[1:a]anull[a]"
    subprocess.run([ff, "-y", "-v", "error", "-i", str(video), *audio, "-filter_complex", af, "-map", "0:v", "-map", "[a]",
                    "-c:v", "libx264", "-preset", "medium", "-crf", "17", "-profile:v", "high", "-pix_fmt", "yuv420p",
                    "-r", str(FPS), "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-t", f"{total:.3f}", str(out)],
                   check=True, capture_output=True, creationflags=render._NO_WINDOW)


def render_video(spec, out: Path, music: Path | None = None, offers: list[dict] | None = None,
                 allow_files: bool = False, resolve_image=None, keep_project: Path | None = None, conn=None) -> dict:
    """Validate, build, check and render an explainer to an MP4 at `out`. Returns a report: the
    cleaned spec's notes, the check, and the file's measurements. Raises SpecError for a bad
    spec, RuntimeError when the check finds problems or the render fails."""
    clean = validate(spec, offers, allow_files)
    with tempfile.TemporaryDirectory(prefix="studio-motion-") as tmp:
        project = build_project(clean, (keep_project or Path(tmp)) / "explainer", "full", resolve_image, conn)
        report = check(project, clean)
        if report["problems"]:
            raise RuntimeError("the explainer didn't pass its check: " + "; ".join(report["problems"][:6]))
        raw = Path(tmp) / "raw.mp4"
        result = render.hyperframes(["render", str(project), "--quality", "standard", "--fps", str(FPS),
                                     "--output", str(raw), "--quiet"])
        if result.returncode != 0 or not raw.exists():
            raise RuntimeError("the explainer render failed: " + ((result.stderr or "") + (result.stdout or "")).strip()[-400:])
        out.parent.mkdir(parents=True, exist_ok=True)
        add_sound(raw, out, clean["duration"], music)
    return {"notes": clean["notes"], "duration": clean["duration"], "scenes": len(clean["scenes"]),
            "fit": report["fit"], "fonts": report["fonts"], "refused": report["refused"], "file": probe(out)}


def render_insert(scene: dict, duration: float, out: Path, style: str = "cutaway", offers: list[dict] | None = None,
                  allow_files: bool = False) -> dict:
    """One library scene as a transparent WebM (VP9 with alpha) to lay over a talking-head Reel
    while Bryan explains it; his voice and the captions carry on. style "cutaway" fills the frame
    (dissolves in and out), "panel" is a card over the top of the footage; either way the scene
    stays above the caption band."""
    if style not in ("cutaway", "panel"):
        raise ValueError("style must be cutaway or panel")
    clean = validate({"scenes": [{**scene, "duration": duration}]}, offers, allow_files, insert=True)
    with tempfile.TemporaryDirectory(prefix="studio-insert-") as tmp:
        project = build_project(clean, Path(tmp) / "insert", style)
        report = check(project, clean)
        if report["problems"]:
            raise RuntimeError("the insert didn't pass its check: " + "; ".join(report["problems"][:6]))
        result = render.hyperframes(["render", str(project), "--format", "webm", "--quality", "standard",
                                     "--fps", str(FPS), "--output", str(out), "--quiet"])
        if result.returncode != 0 or not out.exists():
            raise RuntimeError("the insert render failed: " + ((result.stderr or "") + (result.stdout or "")).strip()[-400:])
    return {"duration": clean["duration"], "fit": report["fit"], "file": probe(out)}


def overlay_insert(footage: Path, insert: Path, at: float, out: Path) -> None:
    """The talking-head Reel with the insert laid over it from `at` seconds; the voice is untouched."""
    subprocess.run([_ffmpeg(), "-y", "-v", "error", "-i", str(footage), "-c:v", "libvpx-vp9", "-i", str(insert),
                    "-filter_complex", f"[1:v]setpts=PTS+{at:.3f}/TB[ov];[0:v][ov]overlay=0:0:format=auto:eof_action=pass[v]",
                    "-map", "[v]", "-map", "0:a?", "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
                    "-c:a", "copy", "-movflags", "+faststart", str(out)],
                   check=True, capture_output=True, creationflags=render._NO_WINDOW)


# ── Previews ──────────────────────────────────────────────────────────────────

def still_times(spec: dict, count: int = 6) -> list[float]:
    """Moments that show the explainer best: each scene once its entrance has landed (the longest
    scenes first when there are more scenes than stills)."""
    times = _scene_times(clean_or(spec))
    picks = [(round(start + max(dur * 0.66, min(3.4, dur - 0.5)), 2), dur) for start, dur in times]
    if len(picks) > count:
        keep = sorted(sorted(range(len(picks)), key=lambda i: -picks[i][1])[:count])
        picks = [picks[i] for i in keep]
    # Fewer scenes than stills: add the longest scenes mid-build, so the sheet shows the motion too.
    for start, dur in sorted(times, key=lambda x: -x[1])[:max(count - len(picks), 0)]:
        picks.append((round(start + dur * 0.36, 2), dur))
    return sorted(p[0] for p in picks)


def clean_or(spec: dict) -> dict:
    return spec if "duration" in spec and all("duration" in s for s in spec.get("scenes", [])) else validate(spec)


def contact_sheet(video: Path, times: list[float], out: Path, cols: int = 3, width: int = 360) -> None:
    """Stills from the rendered file at `times`, in a grid with each one's time under it."""
    from PIL import Image, ImageDraw
    ff = _ffmpeg()
    shots = []
    with tempfile.TemporaryDirectory(prefix="studio-sheet-") as tmp:
        for k, t in enumerate(times):
            png = Path(tmp) / f"{k}.png"
            subprocess.run([ff, "-y", "-v", "error", "-ss", f"{t:.3f}", "-i", str(video), "-frames:v", "1",
                            "-vf", f"scale={width}:-1:flags=lanczos", str(png)], check=True, creationflags=render._NO_WINDOW)
            shots.append((t, Image.open(png).convert("RGB")))
    h_ = shots[0][1].height
    rows = math.ceil(len(shots) / cols)
    pad, label = 16, 30
    sheet = Image.new("RGB", (cols * width + (cols + 1) * pad, rows * (h_ + label) + (rows + 1) * pad), "#E9E6DE")
    draw = ImageDraw.Draw(sheet)
    for k, (t, img) in enumerate(shots):
        x, y = pad + (k % cols) * (width + pad), pad + (k // cols) * (h_ + label + pad)
        sheet.paste(img, (x, y))
        draw.text((x + 4, y + h_ + 8), f"{t:.1f} s", fill="#1E2A32")
    sheet.save(out)


def gif(video: Path, out: Path, width: int = 240, fps: int = 10) -> None:
    """A small looping GIF preview (one shared palette, so the paper stays clean)."""
    ff = _ffmpeg()
    vf = f"fps={fps},scale={width}:-1:flags=lanczos"
    with tempfile.TemporaryDirectory(prefix="studio-gif-") as tmp:
        palette = Path(tmp) / "palette.png"
        subprocess.run([ff, "-y", "-v", "error", "-i", str(video), "-vf", f"{vf},palettegen=max_colors=96:stats_mode=diff",
                        str(palette)], check=True, creationflags=render._NO_WINDOW)
        subprocess.run([ff, "-y", "-v", "error", "-i", str(video), "-i", str(palette), "-lavfi",
                        f"{vf}[x];[x][1:v]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle", "-loop", "0",
                        str(out)], check=True, creationflags=render._NO_WINDOW)
