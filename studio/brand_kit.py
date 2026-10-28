"""
The brand kit (Bryan, 2026-10-28: "introduce fonts, colour scheme as part of the brand design kit? Similar to how
canva does it. So we can have more consistent assets across and also can tweak based on content type").

One kit, kept on the Studio machine (Studio/brand_kit.json; uploaded fonts and the logo in Studio/BrandKit/), and
read by every render: a Reel's captions, hook, callouts, cards and end card (editor.render_overlay), carousels
(carousel.lay_out) and slideshow Reels (their slides and end card). It holds:

- the look it starts from (preset): site (Site Notes, Bryan's pick), gallery or classic (editor.TITLE_STYLES);
- colours as roles (ROLES), each named by what it colours on a Reel or a slide;
- fonts as roles (FONT_ROLES): one of the fonts Studio bundles (BUNDLED) or one Bryan uploaded (.ttf, .otf or
  .woff2; parsed and checked, never trusted by its name: add_font);
- the logo (a PNG, checked and saved again: add_logo) for the end card and the slides' mark;
- per kind of post (content_plan.KINDS) the colours or fonts that differ; the rest is the main kit's.

Nothing changes until it's edited: with no file the kit is the preset's own values, exactly today's look
(tests/quality/suite_brand_kit.py renders a frame and a slide both ways and compares the pixels). A draft keeps the
kit it was made with (resolve() is a snapshot, kept in edl["look"]["kit"] or a carousel version's "kit"), so Edit
words and Studio's own fix rounds keep its look; a new edit, a send-back and Edit it again take the kit of the day.
Only Urban Makers posts use it (personal is on hold and keeps its own colours).
"""
import contextvars
import hashlib
import io
import json
import logging
import os
import re
import shutil
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import render
import settings

log = logging.getLogger("studio.brand_kit")

VERSION = 1
FONT_EXTS = {".ttf", ".otf", ".woff2"}
FONT_MAX_BYTES = 3 * 1024 * 1024
LOGO_MAX_BYTES = 3 * 1024 * 1024
LOGO_MAX_SIDE = 4000
UPLOADS_MAX = 40
KINDS = ("knowhow", "ideas", "onsite", "finished", "offer")   # content_plan.KINDS, in the planner's order
PRESETS = ("site", "gallery", "classic")
PRESET_NAMES = {"site": "Site Notes", "gallery": "Gallery", "classic": "Classic"}
MIN_CONTRAST = 4.5   # WCAG AA for text

# Colour roles: key -> (name, group, what it colours). Reels are the Site Notes look's; gallery and classic map
# them onto their own (editor._theme_css).
ROLES = {
    "text": ("Words", "reel", "Captions and the words on labels and cards"),
    "label": ("Labels", "reel", "Behind the hook, callouts and cards"),
    "key": ("Key word", "reel", "The key word, callout labels and the handle"),
    "key_ink": ("Words on key colour", "reel", "Words on a key-colour label"),
    "end": ("End card", "reel", "The shade over the last frame"),
    "slide_bg": ("Background", "slide", "Each slide's background"),
    "slide_heading": ("Headings", "slide", "Headings and the Urban Makers mark"),
    "slide_body": ("Body", "slide", "The words under a heading"),
    "slide_accent": ("Accent", "slide", "The bar, tags, Swipe and the ask"),
    "slide_end": ("Last slide", "slide", "The ask slide's background"),
}
FONT_ROLES = {
    "titles": ("Titles", "reel", "Hook, callouts, cards and the end card"),
    "captions": ("Captions", "reel", "The words as they're said"),
    "slide_headings": ("Headings", "slide", "Slide headings"),
    "slide_text": ("Body", "slide", "Body text, the mark and labels"),
}
# The roles a look sets in a bold face (its words are 700 or heavier): a font without one falls back.
NEEDS_BOLD = {"site": {"titles", "captions", "slide_headings"}, "gallery": {"slide_headings"},
              "classic": {"captions", "slide_headings"}}

# Each look's own values: today's (editor.THEME urban_makers, _SITE_CSS, carousel.COLOURS and FONTS).
_SLIDE_COLOURS = {"slide_bg": "#22302C", "slide_heading": "#F6F4EE", "slide_body": "#D6DDD8",
                  "slide_accent": "#F5A524", "slide_end": "#18231F"}
_PRESET_COLOURS = {
    "site": {"text": "#FFFFFF", "label": "#12110E", "key": "#F2B65A", "key_ink": "#16140F", "end": "#12110E"},
    "gallery": {"text": "#F6F4EE", "label": "#0E1214", "key": "#F6F4EE", "key_ink": "#16140F", "end": "#18231F"},
    "classic": {"text": "#F6F4EE", "label": "#18231F", "key": "#F5A524", "key_ink": "#16140F", "end": "#18231F"},
}
# What a role does in each look, where it isn't what ROLES says.
ROLE_NOTES = {
    "gallery": {"label": "The shade behind titles and captions", "key": "The key word in captions",
                "key_ink": "Not used in this look", "end": "End card and full-screen cards"},
    "classic": {"label": "Behind captions and the hook", "key": "The bar on cards and callouts",
                "key_ink": "Not used in this look", "end": "End card and full-screen cards"},
}

# The fonts Studio bundles (OFL, latin): key -> (name, faces). A face: (file under render/, weight or a range, style).
_F = "captions/fonts/"
BUNDLED = {
    "archivo": ("Archivo", tuple((f"{_F}Archivo-latin-{w}.woff2", w, "normal") for w in ("700", "750", "800"))),
    "inter": ("Inter", ((f"{_F}Inter-latin-500.woff2", "500", "normal"), (f"{_F}Inter-latin-600.woff2", "600", "normal"))),
    "figtree": ("Figtree", (("carousel/fonts/Figtree-latin.woff2", "100 1000", "normal"),)),
    "nunito": ("Nunito", ((f"{_F}Nunito-latin.woff2", "100 1000", "normal"),)),
    "dm_sans": ("DM Sans", ((f"{_F}DMSans-latin.woff2", "100 1000", "normal"),)),
    "outfit": ("Outfit", ((f"{_F}Outfit-latin.woff2", "100 1000", "normal"),)),
    "instrument_serif": ("Instrument Serif", ((f"{_F}InstrumentSerif-latin-400.woff2", "400", "normal"),
                                              (f"{_F}InstrumentSerif-latin-400-italic.woff2", "400", "italic"))),
    "bricolage": ("Bricolage Grotesque", (("carousel/fonts/BricolageGrotesque-latin.woff2", "200 800", "normal"),)),
}
# The carousel declares its fonts' weights as it always has (carousel._CSS); the same files.
_SLIDE_RANGES = {"bricolage": "200 800", "figtree": "300 900"}
_FORMAT = {".woff2": "woff2", ".ttf": "truetype", ".otf": "opentype"}

_lock = threading.Lock()


# ── Where it lives ────────────────────────────────────────────────────────────

def kit_file() -> Path:
    return settings.ROOT / "brand_kit.json"


def kit_dir() -> Path:
    return settings.ROOT / "BrandKit"


def font_dir() -> Path:
    return kit_dir() / "fonts"


def load() -> dict:
    """The saved kit ({} when Bryan hasn't saved one: every value is then the preset's own)."""
    try:
        data = json.loads(kit_file().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        log.warning("The brand kit couldn't be read (%s); using today's look", e)
        return {}


def _write(data: dict) -> None:
    path = kit_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ── Values ────────────────────────────────────────────────────────────────────

_HEX = re.compile(r"^#?([0-9A-Fa-f]{6})$")


def clean_colour(v) -> str | None:
    m = _HEX.match(str(v or "").strip())
    return "#" + m.group(1).upper() if m else None


def rgb(hex_colour: str) -> tuple[int, int, int]:
    h = clean_colour(hex_colour) or "#000000"
    return int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16)


def rgb_text(hex_colour: str) -> str:
    """"18,17,14": for rgba(var(--x-rgb), .9)."""
    return ",".join(str(c) for c in rgb(hex_colour))


def luminance(hex_colour: str) -> float:
    def lin(c):
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = rgb(hex_colour)
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def contrast(a: str, b: str) -> float:
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def mix(a: str, b: str, t: float) -> str:
    """a moved t of the way to b."""
    ra, rb = rgb(a), rgb(b)
    return "#" + "".join(f"{round(x + (y - x) * t):02X}" for x, y in zip(ra, rb))


def preset_of(data: dict | None = None) -> str:
    """The look the kit starts from: the saved kit's, else STUDIO_TITLE_STYLE's (site)."""
    p = (data or {}).get("preset")
    if p in PRESETS:
        return p
    p = (settings.TITLE_STYLE or "site").lower().strip()
    return p if p in PRESETS else "site"


def _caption_default() -> str:
    key = (settings.CAPTION_FONT or "inter").lower().replace("-", "_").replace(" ", "_")
    return key if key in BUNDLED and key not in ("instrument_serif", "bricolage") else "inter"


def preset_values(preset: str) -> dict:
    """A look's own colours and fonts: exactly what Studio drew before the kit."""
    fonts = {"site": {"titles": "archivo", "captions": "archivo"},
             "gallery": {"titles": "instrument_serif", "captions": _caption_default()},
             "classic": {"titles": _caption_default(), "captions": _caption_default()}}[preset]
    return {"colours": {**_PRESET_COLOURS[preset], **_SLIDE_COLOURS},
            "fonts": {**fonts, "slide_headings": "bricolage", "slide_text": "figtree"}}


# ── Fonts ─────────────────────────────────────────────────────────────────────

def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "font"


def upload_key(family: str) -> str:
    return "up:" + _slug(family)


def uploads(data: dict | None = None, hidden: bool = False) -> dict:
    data = load() if data is None else data
    ups = data.get("uploads") if isinstance(data.get("uploads"), dict) else {}
    return {k: v for k, v in ups.items() if isinstance(v, dict) and (hidden or not v.get("hidden"))}


def _upload_face(u: dict) -> dict:
    return {"upload": u["id"], "file": u["file"], "weight": u["weight"], "style": u.get("style", "normal")}


def fonts_list(data: dict | None = None) -> list[dict]:
    """Every font the kit can pick: the bundled ones, then each uploaded family (its faces)."""
    data = load() if data is None else data
    out = [{"key": k, "name": n, "source": "bundled", "weights": [f[1] for f in faces],
            "italic": any(f[2] == "italic" for f in faces), "bold": _has_bold([f[1] for f in faces])}
           for k, (n, faces) in BUNDLED.items()]
    fams: dict[str, dict] = {}
    for u in sorted(uploads(data).values(), key=lambda u: u.get("at", "")):
        key = upload_key(u["family"])
        fam = fams.setdefault(key, {"key": key, "name": u["family"], "source": "upload", "weights": [], "italic": False,
                                    "faces": []})
        fam["weights"].append(u["weight"])
        fam["italic"] = fam["italic"] or u.get("style") == "italic"
        fam["faces"].append({"id": u["id"], "style_name": u.get("style_name", ""), "weight": u["weight"],
                             "style": u.get("style", "normal"), "bytes": u.get("bytes", 0)})
    for fam in fams.values():
        fam["bold"] = _has_bold(fam["weights"])
    return out + list(fams.values())


def _top_weight(w: str) -> int:
    try:
        return max(int(float(x)) for x in str(w).split())
    except ValueError:
        return 400


def _has_bold(weights) -> bool:
    return any(_top_weight(w) >= 600 for w in weights)


def _font_faces(key: str, data: dict) -> list[dict] | None:
    """A font's faces as snapshot entries ({bundled, n} or {upload, file, weight, style}); None when it isn't there."""
    if key in BUNDLED:
        return [{"bundled": key, "n": n} for n in range(len(BUNDLED[key][1]))]
    if key.startswith("up:"):
        faces = [_upload_face(u) for u in sorted(uploads(data).values(), key=lambda u: u.get("at", ""))
                 if upload_key(u["family"]) == key]
        return faces or None
    return None


def faces(kit: dict | None, role: str, slide_ranges: bool = False) -> list[dict]:
    """A role's faces to load: [{path, weight, style, format}] (path a Path). From a snapshot (resolve); a file
    that's gone (an upload deleted by hand) falls back to the look's own font, so a render never fails on it.
    slide_ranges: the carousel's own weight ranges for its bundled fonts (carousel._CSS)."""
    kit = kit or preset_kit("site")
    out = []
    for f in (kit.get("faces") or {}).get(role) or []:
        if "bundled" in f:
            key = f["bundled"]
            try:
                rel, weight, style = BUNDLED[key][1][int(f.get("n", 0))]
            except (KeyError, IndexError, ValueError):
                continue
            if slide_ranges and key in _SLIDE_RANGES:
                weight = _SLIDE_RANGES[key]
            path = render.RENDER_DIR / rel
        else:
            name = str(f.get("file") or "")
            if not re.fullmatch(r"[0-9a-f]{16}\.(woff2|ttf|otf)", name):
                continue
            path, weight, style = font_dir() / name, str(f.get("weight") or "400"), f.get("style", "normal")
        if path.is_file():
            out.append({"path": path, "weight": weight, "style": "italic" if style == "italic" else "normal",
                        "format": _FORMAT.get(path.suffix.lower(), "woff2")})
    if out:
        return out
    fallback = preset_values(kit.get("preset") or "site")["fonts"].get(role) or "archivo"
    if (kit.get("faces") or {}).get(role):
        log.warning("The brand kit's %s font is missing; using %s", role, BUNDLED[fallback][0])
    return faces({"preset": kit.get("preset"), "faces": {role: _font_faces(fallback, {})}}, role, slide_ranges)


def face_css(family: str, faces_: list[dict], project: Path | None = None, prefix: str = "",
             style_prop: bool = False) -> str:
    """@font-face rules for faces under one family name. project: copy each file next to the page (prefix-N.ext);
    else the rules point at the files themselves (file:// URIs, the carousel's page). style_prop: say
    font-style:normal too (as the Site Notes page always has)."""
    rules = []
    for n, f in enumerate(faces_, 1):
        if project is not None:
            name = f"{prefix or _slug(family)}-{n}{f['path'].suffix.lower()}"
            shutil.copy(f["path"], project / name)
            src = name
        else:
            src = f["path"].resolve().as_uri()
        style = "font-style:italic;" if f["style"] == "italic" else "font-style:normal;" if style_prop else ""
        rules.append(f'@font-face{{font-family:"{family}";src:url("{src}") format("{f["format"]}");'
                     f'font-weight:{f["weight"]};{style}font-display:block}}')
    return "".join(rules)


def family(kit: dict | None, role: str) -> str:
    """The family name a page gives a role's font: a bundled font's own name, an uploaded one "Kit <name>"
    (letters, digits and dashes only: never the file's own words in the page's styles)."""
    key = ((kit or {}).get("fonts") or {}).get(role) or "archivo"
    return BUNDLED[key][0] if key in BUNDLED else "Kit " + _slug(key[3:])


def file_prefix(key: str) -> str:
    return key if key in BUNDLED else "kit-" + _slug(key[3:])


# Measuring Site Notes' labels (editor._site_width) in the draft's own titles font: set around a render or a
# measure of a draft (final_check) with measuring(kit). Without one, the bundled Archivo, as always.
_MEASURE = contextvars.ContextVar("brand_kit_measure", default=None)
_measure_fonts: dict = {}


@contextmanager
def measuring(kit: dict | None):
    token = _MEASURE.set(kit)
    try:
        yield
    finally:
        _MEASURE.reset(token)


def _nearest(faces_: list[dict], weight: str) -> tuple[Path, float | None]:
    """The face CSS would pick for a weight (normal style first), and the weight to set on a variable one."""
    want = float(weight)
    pool = [f for f in faces_ if f["style"] == "normal"] or faces_
    for f in pool:
        parts = [float(x) for x in str(f["weight"]).split()]
        if len(parts) == 2 and parts[0] <= want <= parts[1]:
            return f["path"], want
        if len(parts) == 1 and parts[0] == want:
            return f["path"], None
    def top(f):
        return max(float(x) for x in str(f["weight"]).split())
    heavier = sorted((f for f in pool if top(f) >= want), key=top)
    lighter = sorted((f for f in pool if top(f) < want), key=top, reverse=True)
    f = (heavier + lighter)[0] if want > 500 else (lighter + heavier)[0]
    parts = str(f["weight"]).split()
    return f["path"], (float(parts[1]) if len(parts) == 2 else None)


def measure_font(weight: str, default: Path):
    """A Pillow font at 200 px for measuring Site Notes' titles at a weight: the kit's titles face when measuring()
    is on (and the look is Site Notes), else default (the bundled Archivo face)."""
    from PIL import ImageFont
    kit = _MEASURE.get()
    path, var = default, None
    if kit and kit.get("preset") == "site":
        fs = faces(kit, "titles")
        if fs:
            path, var = _nearest(fs, weight)
    key = (str(path), weight if var is not None else None)
    if key not in _measure_fonts:
        font = ImageFont.truetype(str(path), 200)
        if var is not None:
            try:
                axes = font.get_variation_axes()
                values = [a.get("default", 0) for a in axes]
                for i, a in enumerate(axes):
                    name = a.get("name")
                    name = name.decode("latin-1", "replace") if isinstance(name, bytes) else str(name)
                    if name.lower().startswith("weight") or name == "wght":
                        values[i] = max(a.get("minimum", var), min(a.get("maximum", var), var))
                font.set_variation_by_axes(values)
            except (OSError, AttributeError):
                pass
        _measure_fonts[key] = font
    return _measure_fonts[key]


# ── The kit for a post ────────────────────────────────────────────────────────

def _clean_colours(v) -> dict:
    return {k: c for k, c in ((k, clean_colour(x)) for k, x in (v or {}).items() if k in ROLES) if c} \
        if isinstance(v, dict) else {}


def _clean_fonts(v, data: dict) -> dict:
    if not isinstance(v, dict):
        return {}
    known = {f["key"] for f in fonts_list(data)}
    return {k: x for k, x in v.items() if k in FONT_ROLES and isinstance(x, str) and x in known}


def _logo_ok(name) -> str | None:
    name = str(name or "")
    return name if re.fullmatch(r"logo-[0-9a-f]{16}\.png", name) and (kit_dir() / name).is_file() else None


def resolve(kind: str | None = None, brand: str = "urban_makers", data: dict | None = None) -> dict | None:
    """The kit for a post of this kind, as a snapshot a draft keeps (edl["look"]["kit"], a carousel version's
    "kit"): {v, preset, kind, colours (every role), fonts (role -> the font used), asked (role -> the font asked
    for, when it fell back), faces (role -> the faces to load), logo}. None for an account other than Urban Makers."""
    if brand and brand != "urban_makers":
        return None
    data = load() if data is None else data
    preset = preset_of(data)
    base = preset_values(preset)
    kinds = data.get("kinds") if isinstance(data.get("kinds"), dict) else {}
    over = kinds.get(kind) if kind in KINDS and isinstance(kinds.get(kind), dict) else {}
    colours = {**base["colours"], **_clean_colours(data.get("colours")), **_clean_colours(over.get("colours"))}
    asked = {**base["fonts"], **_clean_fonts(data.get("fonts"), data), **_clean_fonts(over.get("fonts"), data)}
    if preset == "classic":
        asked["titles"] = asked["captions"]   # classic sets its titles in the captions font
    fonts, fell, face_map = {}, {}, {}
    weights = {f["key"]: f["weights"] for f in fonts_list(data)}
    for role, key in asked.items():
        fs = _font_faces(key, data)
        if fs is None or (role in NEEDS_BOLD[preset] and not _has_bold(weights.get(key, []))):
            fell[role] = key
            key = base["fonts"][role]
            fs = _font_faces(key, data)
        fonts[role], face_map[role] = key, fs
    logo = _logo_ok(data.get("logo"))
    return {"v": VERSION, "preset": preset, "kind": kind if kind in KINDS else None, "colours": colours,
            "fonts": fonts, "asked": fell, "faces": face_map, "logo": logo}


def preset_kit(preset: str) -> dict:
    """A look's own kit (no edits): what a draft made before the kit was drawn with."""
    return resolve(None, "urban_makers", {"preset": preset if preset in PRESETS else "site"})


def same_look(a: dict | None, b: dict | None) -> bool:
    """Two snapshots draw the same (the kind they were for aside)."""
    keys = ("preset", "colours", "fonts", "faces", "logo")
    return bool(a) and bool(b) and all(a.get(k) == b.get(k) for k in keys)


def is_default(kit: dict | None) -> bool:
    return kit is None or same_look(kit, preset_kit(kit.get("preset") or "site"))


# ── What the renders read ─────────────────────────────────────────────────────

def theme_vars(kit: dict | None, style: str) -> dict:
    """The CSS custom properties a Reel's page takes from the kit over editor.THEME's (editor._theme_css), only
    where the kit differs from the look's own (so an unedited kit's page is the page it always was). Site Notes:
    --label-rgb, --brass, --brass-ink, --end-rgb (its words' colour is set in its styles: editor.kit_css). Gallery:
    --ink, --shade-rgb, --key and the cards' and end card's --card-lo/--card-hi. Classic: --ink, --deep-rgb,
    --accent and the same cards."""
    if not kit or style not in PRESETS:
        return {}
    c, own = kit["colours"], _PRESET_COLOURS[style]
    def changed(role):
        return c[role].upper() != own[role].upper()
    out = {}
    if style == "site":
        for role, var, value in (("label", "label-rgb", rgb_text(c["label"])), ("key", "brass", c["key"]),
                                 ("key_ink", "brass-ink", c["key_ink"]), ("end", "end-rgb", rgb_text(c["end"]))):
            if changed(role):
                out[var] = value
        return out
    if changed("text"):
        out["ink"] = c["text"]
    if changed("label"):
        out["shade-rgb" if style == "gallery" else "deep-rgb"] = rgb_text(c["label"])
    if changed("key"):
        out["key" if style == "gallery" else "accent"] = c["key"]
    if changed("end"):   # the radial's lighter middle, from the new colour
        out.update({"card-lo": c["end"], "card-hi": mix(c["end"], c["text"], 0.12)})
    return out


def slide_colours(kit: dict | None) -> dict:
    """carousel._CSS's colours: bg, deep, ink, soft, accent."""
    c = (kit or preset_kit("site"))["colours"]
    return {"bg": c["slide_bg"].lower(), "deep": c["slide_end"].lower(), "ink": c["slide_heading"].lower(),
            "soft": c["slide_body"].lower(), "accent": c["slide_accent"].lower()}


def backdrop_vars(kit: dict | None) -> dict:
    """A slideshow Reel's backdrop (behind the slides) in the kit's slide colours, once they've been changed."""
    if not kit:
        return {}
    c = kit["colours"]
    if c["slide_bg"] == _SLIDE_COLOURS["slide_bg"] and c["slide_end"] == _SLIDE_COLOURS["slide_end"]:
        return {}
    return {"card-hi": c["slide_bg"], "card-lo": c["slide_end"]}


def logo_path(kit: dict | None) -> Path | None:
    name = _logo_ok((kit or {}).get("logo"))
    return kit_dir() / name if name else None


LOGO_CSS = (".endcard .mk img{display:block;max-width:440px;max-height:170px;width:auto;height:auto}"
            ".mark img.logo{display:block;height:34px;width:auto}.sign img.logo{display:block;height:64px;width:auto}")


# ── Warnings, in plain words ──────────────────────────────────────────────────

def warnings(kit: dict) -> list[dict]:
    """What the kit would draw badly: text under 4.5:1 on what it sits on, and a font that fell back because the
    look needs a bold face it hasn't got. [{role, text}]"""
    c, preset, out = kit["colours"], kit["preset"], []
    def need(a, b, what, role):
        r = contrast(c[a], c[b])
        if r < MIN_CONTRAST:
            out.append({"role": role, "kind": "contrast", "ratio": round(r, 1),
                        "text": f"{what} is hard to read ({r:.1f} to 1; aim for 4.5)."})
    need("text", "label", "Words on labels", "text")
    if preset == "site":
        need("key", "label", "The key word on a label", "key")
        need("key_ink", "key", "Words on a key-colour label", "key_ink")
    need("text", "end", "Words on the end card", "end")
    need("slide_heading", "slide_bg", "Slide headings", "slide_heading")
    need("slide_body", "slide_bg", "Slide body text", "slide_body")
    need("slide_accent", "slide_bg", "The accent on a slide", "slide_accent")
    need("slide_accent", "slide_end", "The ask on the last slide", "slide_end")
    names = {f["key"]: f["name"] for f in fonts_list()}
    for role, key in (kit.get("asked") or {}).items():
        used = BUNDLED.get(kit["fonts"][role], ("",))[0]
        out.append({"role": role, "kind": "font",
                    "text": f"{names.get(key, 'That font')} has no bold, which {FONT_ROLES[role][0].lower()} need. "
                            f"{used} is used instead." if key in names else
                            f"That font isn't there any more. {used} is used instead."})
    return out


# ── Saving ────────────────────────────────────────────────────────────────────

def view(data: dict | None = None) -> dict:
    """Everything the Brand kit page shows: the saved kit, each look's own values, the roles, fonts, kinds, the
    kit each kind resolves to and its warnings."""
    data = load() if data is None else data
    import content_plan
    resolved = {k or "main": resolve(k, data=data) for k in (None, *KINDS)}
    return {"preset": preset_of(data), "saved": bool(data), "updated": data.get("updated"), "by": data.get("by"),
            "colours": _clean_colours(data.get("colours")), "fonts": _clean_fonts(data.get("fonts"), data),
            "logo": _logo_ok(data.get("logo")),
            "kinds": {k: {"colours": _clean_colours((data.get("kinds") or {}).get(k, {}).get("colours")),
                          "fonts": _clean_fonts((data.get("kinds") or {}).get(k, {}).get("fonts"), data)}
                      for k in KINDS},
            "presets": {p: {"name": PRESET_NAMES[p], **preset_values(p)} for p in PRESETS},
            "roles": [{"key": k, "name": n, "group": g, "for": w,
                       "notes": {p: ROLE_NOTES.get(p, {}).get(k) for p in PRESETS if ROLE_NOTES.get(p, {}).get(k)}}
                      for k, (n, g, w) in ROLES.items()],
            "font_roles": [{"key": k, "name": n, "group": g, "for": w} for k, (n, g, w) in FONT_ROLES.items()],
            "fonts_list": fonts_list(data),
            "kind_names": [{"key": k, "name": content_plan.KINDS[k]["label"]} for k in KINDS],
            "resolved": resolved, "warnings": {k: warnings(v) for k, v in resolved.items()},
            "limits": {"font_mb": FONT_MAX_BYTES // (1024 * 1024), "logo_mb": LOGO_MAX_BYTES // (1024 * 1024)}}


def save(body: dict, who: str = "") -> dict:
    """Save the kit from the page: {preset, colours, fonts, kinds: {kind: {colours, fonts}}} (merged). A value equal
    to the look's own isn't stored, so a later change to a look's defaults still reaches it."""
    with _lock:
        data = merged(body, load())
        data.update(v=VERSION, updated=_now(), by=who[:80])
        _write(data)
    log.info("Brand kit saved by %s (%s, %d colours and %d fonts changed, %d kinds of post with their own)",
             who or "?", data["preset"], len(data["colours"]), len(data["fonts"]), len(data["kinds"]))
    return view(data)


def merged(body: dict, data: dict) -> dict:
    """The saved kit (data) with the page's edits (body) in it, checked: unknown roles, colours that aren't
    #RRGGBB and fonts that aren't there are refused (ValueError). Not saved: the preview uses it as it is."""
    data = json.loads(json.dumps(data))
    preset = body.get("preset") if body.get("preset") in PRESETS else preset_of(data)
    base = preset_values(preset)
    known = {f["key"] for f in fonts_list(data)}

    def colours(v, under):
        if not isinstance(v, dict):
            return {}
        bad = [k for k, x in v.items() if k not in ROLES or not clean_colour(x)]
        if bad:
            raise ValueError(f"not a colour: {', '.join(sorted(map(str, bad)))[:80]}")
        return {k: clean_colour(x) for k, x in v.items() if clean_colour(x) != under.get(k)}

    def fonts(v, under):
        if not isinstance(v, dict):
            return {}
        bad = [k for k, x in v.items() if k not in FONT_ROLES or x not in known]
        if bad:
            raise ValueError(f"no such font for {', '.join(sorted(map(str, bad)))[:80]}")
        return {k: x for k, x in v.items() if x != under.get(k)}

    main_c = colours(body.get("colours"), base["colours"])
    main_f = fonts(body.get("fonts"), base["fonts"])
    under_c, under_f = {**base["colours"], **main_c}, {**base["fonts"], **main_f}
    kinds = {}
    for k, v in (body.get("kinds") or {}).items() if isinstance(body.get("kinds"), dict) else ():
        if k not in KINDS or not isinstance(v, dict):
            raise ValueError("no such kind of post")
        kc, kf = colours(v.get("colours"), under_c), fonts(v.get("fonts"), under_f)
        if kc or kf:
            kinds[k] = {"colours": kc, "fonts": kf}
    data.update(preset=preset, colours=main_c, fonts=main_f, kinds=kinds)
    if "logo" in body:   # a logo added (add_logo) or None: back to the Urban Makers mark
        if body["logo"] and not _logo_ok(body["logo"]):
            raise ValueError("That logo isn't there. Add it again.")
        if body["logo"]:
            data["logo"] = body["logo"]
        else:
            data.pop("logo", None)
    return data


# ── Uploads ───────────────────────────────────────────────────────────────────

_WEIGHT_WORDS = [("thin", 100), ("hairline", 100), ("extralight", 200), ("ultralight", 200), ("light", 300),
                 ("semibold", 600), ("demibold", 600), ("extrabold", 800), ("ultrabold", 800), ("black", 900),
                 ("heavy", 900), ("bold", 700), ("medium", 500), ("regular", 400), ("book", 400), ("normal", 400)]
_FAMILY = re.compile(r"^[^\x00-\x1f\x7f<>\"'\\{};]{1,60}$")


def _sniff_font(raw: bytes) -> str | None:
    head = raw[:4]
    if head == b"wOF2":
        return ".woff2"
    if head == b"OTTO":
        return ".otf"
    if head in (b"\x00\x01\x00\x00", b"true"):
        return ".ttf"
    return None


def check_font(raw: bytes, filename: str = "") -> dict:
    """A font file checked from its own bytes (never its name): a .ttf, .otf or .woff2 by its first bytes, at most
    FONT_MAX_BYTES, that FreeType opens, with a sane family name and Latin letters. Returns {ext, family,
    style_name, weight, style}; raises ValueError in plain words."""
    from PIL import ImageFont
    ext_named = Path(filename or "").suffix.lower()
    if ext_named and ext_named not in FONT_EXTS:
        raise ValueError("Only .ttf, .otf or .woff2 fonts.")
    if len(raw) > FONT_MAX_BYTES:
        raise ValueError(f"That font is too big (over {FONT_MAX_BYTES // (1024 * 1024)} MB).")
    ext = _sniff_font(raw)
    if not ext or len(raw) < 1024:
        raise ValueError("That isn't a .ttf, .otf or .woff2 font.")
    try:
        font = ImageFont.truetype(io.BytesIO(raw), 64)
        family, style_name = font.getname()
    except Exception:
        raise ValueError("That font couldn't be opened. Try another file of it.")
    family = re.sub(r"\s+", " ", str(family or "")).strip()
    style_name = re.sub(r"\s+", " ", str(style_name or "Regular")).strip()[:40]
    if not _FAMILY.match(family):
        raise ValueError("That font has no usable name.")
    try:
        marks = [bytes(font.getmask(ch)) for ch in "AaBbOo"]
        blank = sum(1 for m in marks if not any(m))
    except Exception:
        raise ValueError("That font couldn't draw letters.")
    if blank > 1 or len(set(marks)) < 5:
        raise ValueError("That font has no Latin letters, so it can't set English words.")
    weight = None
    try:
        for a in font.get_variation_axes():
            name = a.get("name")
            name = name.decode("latin-1", "replace") if isinstance(name, bytes) else str(name)
            if name.lower().startswith("weight") or name == "wght":
                lo, hi = int(a.get("minimum", 100)), int(a.get("maximum", 900))
                if 1 <= lo < hi <= 1000:
                    weight = f"{lo} {hi}"
    except (OSError, AttributeError):
        pass
    if weight is None:
        words = style_name.lower().replace(" ", "").replace("-", "")
        weight = str(next((w for word, w in _WEIGHT_WORDS if word in words), 400))
    style = "italic" if re.search(r"italic|oblique", style_name, re.I) else "normal"
    return {"ext": ext, "family": family, "style_name": style_name, "weight": weight, "style": style}


def add_font(raw: bytes, filename: str = "", who: str = "") -> dict:
    """Keep an uploaded font (checked: check_font) in Studio/BrandKit/fonts/<hash>.<ext>. The same file twice is
    one font. Returns the upload's entry."""
    info = check_font(raw, filename)
    fid = hashlib.sha256(raw).hexdigest()[:16]
    with _lock:
        data = load()
        ups = data.setdefault("uploads", {})
        if len([u for u in ups.values() if not u.get("hidden")]) >= UPLOADS_MAX and fid not in ups:
            raise ValueError(f"The kit holds {UPLOADS_MAX} fonts at most. Remove one first.")
        font_dir().mkdir(parents=True, exist_ok=True)
        name = f"{fid}{info['ext']}"
        path = font_dir() / name
        if not path.is_file():
            path.write_bytes(raw)
        entry = {"id": fid, "file": name, "family": info["family"], "style_name": info["style_name"],
                 "weight": info["weight"], "style": info["style"], "bytes": len(raw), "at": _now(), "by": who[:80]}
        ups[fid] = entry
        _write(data)
    log.info("Brand kit: %s added the font %s %s (%d KB)", who or "?", info["family"], info["style_name"], len(raw) // 1024)
    return entry


def remove_font(fid: str) -> None:
    """Take an uploaded face off the list (and out of the kit where it was picked). Its file stays, so drafts
    made with it keep their look."""
    with _lock:
        data = load()
        u = (data.get("uploads") or {}).get(fid)
        if not u:
            raise KeyError(fid)
        u["hidden"] = True
        key = upload_key(u["family"])
        if not any(upload_key(x["family"]) == key for x in uploads(data).values()):
            for box in [data] + list((data.get("kinds") or {}).values()):
                fonts = box.get("fonts") if isinstance(box.get("fonts"), dict) else {}
                for role in [r for r, k in fonts.items() if k == key]:
                    fonts.pop(role)
        _write(data)


def check_logo(raw: bytes) -> bytes:
    """A logo checked from its own bytes: a PNG, at most LOGO_MAX_BYTES and LOGO_MAX_SIDE, saved again by Pillow
    (only the picture is kept). Returns the new PNG's bytes."""
    from PIL import Image
    if len(raw) > LOGO_MAX_BYTES:
        raise ValueError(f"That logo is too big (over {LOGO_MAX_BYTES // (1024 * 1024)} MB).")
    if not raw.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("The logo must be a PNG.")
    try:
        with Image.open(io.BytesIO(raw)) as img:
            if img.format != "PNG":
                raise ValueError
            w, h = img.size
            if not (16 <= w <= LOGO_MAX_SIDE and 16 <= h <= LOGO_MAX_SIDE):
                raise ValueError("size")
            img.load()
            clean = img.convert("RGBA")
    except ValueError as e:
        if str(e) == "size":
            raise ValueError(f"The logo should be 16 to {LOGO_MAX_SIDE} pixels a side.")
        raise ValueError("That PNG couldn't be opened.")
    except Exception:
        raise ValueError("That PNG couldn't be opened.")
    box = clean.getbbox()   # trim the empty edges, so the logo sits where the mark sat
    if box:
        clean = clean.crop(box)
    out = io.BytesIO()
    clean.save(out, "PNG", optimize=True)
    return out.getvalue()


def add_logo(raw: bytes, who: str = "") -> str:
    """Keep an uploaded logo (checked: check_logo) as Studio/BrandKit/logo-<hash>.png. It's the kit's once the
    kit is saved with it (save, "logo"); the file stays after, for drafts made with it. Returns its name."""
    png = check_logo(raw)
    name = f"logo-{hashlib.sha256(png).hexdigest()[:16]}.png"
    with _lock:
        kit_dir().mkdir(parents=True, exist_ok=True)
        path = kit_dir() / name
        if not path.is_file():
            path.write_bytes(png)
    log.info("Brand kit: %s added a logo (%d KB)", who or "?", len(png) // 1024)
    return name


def font_file(name: str) -> Path | None:
    """An uploaded font's file by its stored name (never a path from outside)."""
    if not re.fullmatch(r"[0-9a-f]{16}\.(woff2|ttf|otf)", name or ""):
        return None
    p = font_dir() / name
    return p if p.is_file() else None


def bundled_file(key: str, n: int) -> Path | None:
    try:
        p = render.RENDER_DIR / BUNDLED[key][1][n][0]
    except (KeyError, IndexError):
        return None
    return p if p.is_file() else None


def logo_file(name: str) -> Path | None:
    return kit_dir() / name if _logo_ok(name) else None


# ── For the planner (laptop_status.info.brand_kit) ────────────────────────────

def planner_info() -> dict | None:
    """The slide colours, main and per kind, for the planner's small slide previews. Never a file or a name."""
    try:
        data = load()
        if not data:
            return None
        out = {k or "main": {r: v for r, v in resolve(k, data=data)["colours"].items() if r.startswith("slide_")}
               for k in (None, *KINDS)}
        return out
    except Exception as e:
        log.debug("No brand kit for the planner: %s", e)
        return None
