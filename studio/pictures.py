"""
Picture inserts in Reels (Bryan, 2026-10-20: "there are many instances where an animation just doesn't cut it and
we should be using an AI image instead"; 2026-10-22: "Gemini OK", at about US$0.04 a picture, capped per draft).
Where a real-looking thing explains what Bryan says better than a diagram (a glass panel's style, a fluted panel,
a terrazzo finish, a joinery detail, a finished look), the edit plan lays a picture over the footage instead of a
drawing (graphics style "picture"); processes, steps, comparisons and numbers stay drawings (editor.place_scenes).

  1. The plan writes the picture's brief (rule(): the one thing it shows, its material and light, the Urban
     Makers look). A note of Bryan's asking for an image, picture or photo of something ("use an image of a
     decorative glass panel here instead of the animation") makes that insert a picture at that moment, whatever
     the plan did (asks_from_notes, apply_asks), and so does the read's graphic fix "picture" (fix_actions).
  2. place(): on the draft's clock like a drawn insert (timed to his words, 2.5 to 6 s, never in the opening
     seconds, about one insert per INSERTS_EVERY seconds with the drawings, clear of the cards), at most cap().
  3. make(): Gemini draws it through concepts.reel_picture (9:16, one picture, checked by Claude, drawn once more
     only when the check finds it wrong; cached by its brief, so a re-render or a fix round never pays twice; at
     most settings.REEL_PICTURES_MAX new pictures a draft version), then it's rendered as a transparent WebM like
     a drawn insert (kept in editor.INSERTS): full screen (cutaway) or a card over the top of the picture (panel,
     only clear of his face), a slow push-in, a dissolve, and a small "Concept" mark so nobody takes it for a
     finished project. Laid in under the captions in the draft's one encode (editor.mix_final).
  4. Anything that stops a picture (the setting off, no image key, a Finished homes post, Gemini down or refusing,
     the check failing twice, the budget spent) and the insert is drawn instead (fallback: the plan's own drawing
     for it, else a headline of what it shows); "What the editor did" says so once (summary).
"""
import hashlib
import json
import logging
import math
import re
import subprocess
from pathlib import Path

import numpy as np

import editor
import settings

log = logging.getLogger("studio.pictures")

STYLE = "picture"
EVERY = 12.0            # about one picture per this many seconds of draft (at most settings.REEL_PICTURES_MAX)
INSERTS_EVERY = 10.0    # pictures and drawings together: about one insert per this many seconds
SEE_SECONDS = 2.0       # a picture needs this long on screen to be seen (final_check's too_fast)
PUSH = 0.07             # the slow push-in: 100% to 107% over the insert
FADE_IN, FADE_OUT, RISE = 0.35, 0.3, 0.5
PANEL_X0, PANEL_X1 = 64, editor.W - 64     # a panel: this wide, editor.PANEL_TOP to PANEL_BOTTOM high
LABEL = "Concept"       # as on the carousels' concept slides (slide_images.Concept)
LOOK = "picture-1"      # bump when the look changes: the inserts are rendered again (the pictures aren't drawn again)
NEAR_NOTE = 3.0         # seconds: a note this close to an insert's anchor is about that insert
FONT = Path(__file__).resolve().parent / "render" / "captions" / "fonts" / "Inter-latin-600.woff2"


def available(post_type: str | None = None) -> str | None:
    """None when Studio can show pictures in this draft, else why not (plain words)."""
    if not settings.REEL_PICTURES or settings.REEL_PICTURES_MAX <= 0:
        return "pictures are turned off (STUDIO_REEL_PICTURES)"
    import concepts
    if post_type == concepts.FINISHED:
        return concepts.NOT_IN_FINISHED
    why = concepts.available()
    return f"Studio can't make pictures yet: it {why}" if why else None


def post_type(conn, production: dict) -> str | None:
    """The post's kind now (as carousel.post_type: it can change in the planner after it's made)."""
    plan = json.loads(production.get("plan") or "{}")
    try:
        row = conn.execute("SELECT post_type FROM ideas WHERE id = ?", (plan.get("idea_id"),)).fetchone()
    except Exception:
        row = None
    return (row["post_type"] if row and row["post_type"] else None) or plan.get("post_type")


def cap(total: float) -> int:
    """How many pictures a draft this long may show."""
    return min(settings.REEL_PICTURES_MAX, max(1, int(total // EVERY))) if settings.REEL_PICTURES_MAX > 0 else 0


def inserts_cap(total: float) -> int:
    """Pictures and drawings together, so Bryan is still mostly on screen."""
    return max(1, int(total // INSERTS_EVERY))


# ── The plan ──────────────────────────────────────────────────────────────────

_RULE = """- Pictures (style "picture"): an AI picture laid over the footage while Bryan keeps talking, marked \
"Concept" on screen so nobody takes it for a finished project. Choose a picture instead of a drawing where a \
real-looking thing explains what he says better than a diagram: a material or finish (a reeded or fluted glass \
panel, a fluted timber panel, a terrazzo floor, a limewash wall), a fitting or a joinery detail, or a finished \
look or style ("a warm minimalist kitchen", a before/after look as an idea, not real work). Processes, steps, \
layers, measurements, comparisons and numbers stay drawn animations. Never a person, a client's home, real \
workmanship or a finished project of Urban Makers' (those must be filmed).
  - A note of Bryan's asking for an image, picture or photo of something makes that insert a picture (also \
where a drawn animation was before), at the moment the note is pinned to.
  - Limits: at most {cap} per Reel; pictures and drawn animations together about one per 10 seconds, none in \
the first 2 seconds, each 2.5 to 6 seconds, not overlapping a card, a callout or another insert. Anchor it like a \
drawn animation (clip_id, "at" when he starts the words it shows, duration to when he finishes them).
  - title: what it shows in at most 6 words ("Reeded glass panel"), for Bryan's list (it isn't shown). image: \
the picture's brief for the image model, 40 to 120 words: the one thing to show, close enough to see its texture \
or detail, its material and colour, soft daylight, in a Singapore home where a room shows (HDB proportions, no \
views of named buildings), in the Urban Makers look (the house style; else warm and calm: light oak, warm white, \
matte finishes, brushed brass). Never words, letters, numbers, logos, brand names, people, faces or real places. \
lines: []. look: "cutaway" (full screen, the default) or "panel" (a card over the top of the picture that keeps \
him in view, only when he is framed low). scene: a drawing to show instead if the picture can't be made (scene \
JSON as for a drawn animation, usually a headline of what it shows), or "". role and why as for a drawn animation.
  - For every other graphic, image is "".
  - Re-planning a draft: keep a picture that works exactly as it was (the same image brief), so it isn't drawn \
again.
"""


def rule(post_type: str | None = None) -> str:
    """The plan prompt's part on pictures, or "" when Studio can't make them."""
    if available(post_type):
        return ""
    return _RULE.format(cap=settings.REEL_PICTURES_MAX)


def schema(base: dict) -> dict:
    """The edit plan's schema with pictures: the style, and each graphic's image brief."""
    out = json.loads(json.dumps(base))
    item = out["properties"]["graphics"]["items"]
    if STYLE not in item["properties"]["style"]["enum"]:
        item["properties"]["style"]["enum"].append(STYLE)
    item["properties"]["image"] = {"type": "string"}
    if "image" not in item["required"]:
        item["required"].append("image")
    return out


def brief_for(subject: str) -> str:
    """A brief from just what to show (Bryan's note asked for it; the plan didn't write one)."""
    subject = re.sub(r"\s+", " ", str(subject or "")).strip().rstrip(".")
    return (f"A close, calm photograph of {subject}, as it would look in a newly renovated home in Singapore: its "
            "material, texture and colour clear, in soft daylight, in the Urban Makers look (warm white walls, light "
            "oak, matte finishes, brushed brass), uncluttered around it.")


# Bryan's notes asking for a picture: "an image of a decorative glass panel", "a photo of fluted glass here",
# "use a picture here instead of the animation". "The picture is too dark" (the video) is not one.
_NOUN = r"(?:ai\s+|real(?:istic)?[- ]?(?:looking\s+)?|actual\s+|nice\s+)?(?:image|picture|photo(?:graph)?|pic|render)"
_OF = re.compile(rf"\b(?:an?|some|one)\s+{_NOUN}s?\s+of\s+(?P<subject>.+)", re.I)
_THING = r"(?:animation|drawing|diagram|graphic|cartoon|illustration)"
_INSTEAD = re.compile(rf"\b(?:use|show|put|add|want|need|try|give|have)\b\s+(?:me\s+|us\s+)?(?:an?|some|one)\s+{_NOUN}"
                      rf"|\b{_NOUN}s?\b[^.]*\b(?:instead of|rather than|not)\s+(?:the\s+|this\s+|that\s+|an?\s+)?{_THING}"
                      rf"|\b{_THING}s?\b[^.]*\b(?:with|into|to|for)\s+(?:an?\s+)?{_NOUN}", re.I)
_SUBJECT_END = re.compile(r"\s+(?:here|there|instead|rather|in place|at this|at that|at \d|for this|not|please)\b|[.;!?,(]", re.I)


def note_ask(text: str) -> dict | None:
    """What a note asks for: {"subject": what the picture shows, or "" (the insert's own subject)}, or None."""
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    m = _OF.search(text)
    if m:
        subject = _SUBJECT_END.split(m.group("subject"), 1)[0].strip()
        subject = re.sub(r"^(?:the|a|an)\s+", "", subject, flags=re.I).strip()[:80]
        if subject:
            return {"subject": subject}
    if _INSTEAD.search(text):
        return {"subject": ""}
    return None


def _offsets(cuts: list[dict]) -> list[float]:
    out, off = [], 0.0
    for c in cuts:
        out.append(off)
        off += c["end"] - c["start"]
    return out


def _to_source(cuts: list[dict], t: float):
    """A moment of a draft (its cuts) as (clip_id, seconds in the clip)."""
    for c, off in zip(cuts, _offsets(cuts)):
        if off - 0.001 <= t <= off + c["end"] - c["start"] + 0.001:
            return c["clip_id"], round(c["start"] + t - off, 3)
    return None


INSERT_STYLES = ("scene", STYLE)


def asks_from_notes(notes: list[dict], previous: dict) -> list[dict]:
    """Bryan's notes asking for a picture, each worked out against the draft they were made on: [{note_id,
    subject, clip_id, at (seconds in the clip), was (the insert there before, if any)}]. A note with no time is
    about the draft's only insert, when it has just one."""
    cuts = previous.get("cuts") or []
    inserts = [g for g in previous.get("graphics") or [] if g.get("style") in INSERT_STYLES + ("card",) and "start" in g]
    out = []
    for n in notes or []:
        ask = note_ask(n.get("body"))
        if ask is None:
            continue
        t = n.get("at_seconds")
        was = None
        if t is None:
            drawn = [g for g in inserts if g.get("style") in INSERT_STYLES]
            was = drawn[0] if len(drawn) == 1 else None
        else:
            near = [g for g in inserts if g["start"] - 1.0 <= t <= g["start"] + g["duration"] + 1.0]
            was = min(near, key=lambda g: (g.get("style") not in INSERT_STYLES, abs(g["start"] - t))) if near else None
        if was is not None:
            src = (was.get("clip_id"), was.get("at")) if was.get("at") is not None else _to_source(cuts, was["start"])
        else:
            src = _to_source(cuts, float(t)) if t is not None else None
        if not src or src[0] is None:
            continue
        subject = ask["subject"] or (was or {}).get("title") or ""
        if not subject:
            continue
        out.append({"note_id": n.get("id"), "subject": subject, "clip_id": src[0], "at": float(src[1]),
                    "was": {k: was.get(k) for k in ("style", "title", "scene", "look", "role", "why", "duration")}
                    if was else None})
    return out


def apply_asks(edl: dict, asks: list[dict]) -> list[str]:
    """Bryan's asks made on a new plan (its graphics are still anchored in the clips): the insert at that
    moment becomes a picture of what he asked for, or one is added there. Returns what was done, in plain
    words (kept for the draft's checks)."""
    graphics = edl.setdefault("graphics", [])
    did = []
    for ask in asks:
        def near(g):
            return g.get("clip_id") == ask["clip_id"] and abs(float(g.get("at") or -99) - ask["at"]) <= NEAR_NOTE
        here = [g for g in graphics if near(g)]
        pic = next((g for g in here if g.get("style") == STYLE), None)
        if pic is not None:  # the plan made it a picture already: what it shows is Bryan's
            if ask["subject"].lower() not in (pic.get("title") or "").lower() and ask["subject"] != (ask.get("was") or {}).get("title"):
                pic.update(title=ask["subject"][:80], image=brief_for(ask["subject"]))
            pic["asked"] = True
            continue
        g = next((g for g in here if g.get("style") == "scene"), None) or next(iter(here), None)
        was = ask.get("was") or {}
        scene = (g or {}).get("scene") if (g or {}).get("style") == "scene" else was.get("scene") or ""
        new = {"clip_id": ask["clip_id"], "at": ask["at"],
               "duration": max(float((g or was).get("duration") or 3.5), 3.0) if (g or was) else 3.5,
               "style": STYLE, "title": ask["subject"][:80], "lines": [], "image": brief_for(ask["subject"]),
               "scene": scene if isinstance(scene, (str, dict)) else "", "look": (g or was).get("look") or "cutaway",
               "role": (g or was).get("role") or "explain", "why": "you asked for a picture here", "asked": True}
        if g is not None:
            new.update(clip_id=g.get("clip_id"), at=g.get("at"))
            graphics[graphics.index(g)] = new
            did.append(f"showed a picture of {ask['subject']} where the {'drawn animation' if g.get('style') == 'scene' else g.get('style', 'card')} "
                       f"\"{editor.unmark(g.get('title') or '')}\" was, as your note asked")
        else:
            graphics.append(new)
            did.append(f"added a picture of {ask['subject']} where your note asked for it")
    return did


# ── On the draft's clock ──────────────────────────────────────────────────────

def place(graphics: list[dict], cuts: list[dict], clips: dict[int, dict], total: float, words: list[dict],
          hook_text: str, cards: list[dict] = (), drawn: list[dict] = (), room: int | None = None
          ) -> tuple[list[dict], list[str]]:
    """The plan's pictures on the draft's clock, checked like drawn inserts (editor.place_scenes): timed to his
    words, clear of the hook, the cards (cards) and the drawings (drawn, SCENE_GAP apart), at most room (cap()
    and the inserts' pace). Bryan's asks go first. Returns the placed pictures (time order) and, in plain words,
    why any was left out."""
    wanted = sorted([g for g in graphics if g.get("style") == STYLE], key=lambda g: not g.get("asked"))
    if not wanted:
        return [], []
    most = cap(total) if room is None else min(cap(total), room)
    offsets = _offsets(cuts)
    hook = bool((hook_text or "").strip())
    placed, notes = [], []
    for g in wanted:
        name = (editor.unmark(g.get("title")) or "a picture")[:80]

        def drop(why):
            notes.append(f"left out the picture \"{name}\": {why}")
        at, cid = g.get("at"), g.get("clip_id")
        start = next((off + at - c["start"] for c, off in zip(cuts, offsets)
                      if at is not None and c["clip_id"] == cid and c["start"] <= at < c["end"]), None)
        if start is None and at is not None:  # its moment fell in a pause that was cut
            start = next((off for c, off in zip(cuts, offsets) if c["clip_id"] == cid and at <= c["start"] <= at + 1.5), None)
        if start is None:
            drop("the moment it was for was cut")
            continue
        role = g.get("role") if g.get("role") in ("explain", "gap", "hook") else "explain"
        earliest = editor.HOOK_SECONDS if hook else editor.SCENE_CLEAR
        start = round(max(editor._snap_start(start, words), earliest), 3)
        want = min(max(float(g.get("duration") or editor.SCENE_MIN), editor.SCENE_MIN), editor.SCENE_LONGEST)
        end = round(min(editor._snap_end(start, want, words), total - 0.2), 3)
        if end - start < editor.SCENE_MIN:
            drop("there wasn't room for it before the end")
            continue
        if len(placed) >= max(most, 1 if g.get("asked") else 0) or len(placed) >= cap(total):
            drop(f"Studio keeps to {most} picture{'s' if most != 1 else ''} in a Reel this long")
            continue
        if any(start < o["start"] + o["duration"] + 0.3 and end > o["start"] - 0.3 for o in cards):
            drop("it would have clashed with a card")
            continue
        if any(start < p["start"] + p["duration"] + editor.SCENE_GAP and end > p["start"] - editor.SCENE_GAP
               for p in list(drawn) + placed):
            drop("it came too soon after another insert")
            continue
        look, changed = editor.choose_look(g.get("look", "cutaway"), cuts, clips, start, end)
        placed.append({"clip_id": cid, "at": at, "style": STYLE, "start": start, "duration": round(end - start, 3),
                       "title": name, "lines": [], "image": re.sub(r"\s+", " ", str(g.get("image") or "")).strip()[:2000]
                       or brief_for(name), "scene": g.get("scene") or "", "look": look, "role": role,
                       "why": editor.unmark(g.get("why") or "")[:120], **({"asked": True} if g.get("asked") else {}),
                       **({"look_changed": changed} if changed else {})})
        if changed:
            notes.append(f"showed the picture \"{name}\" full screen instead of as a panel ({changed})")
    return sorted(placed, key=lambda p: p["start"]), notes


# ── Drawing and rendering ─────────────────────────────────────────────────────

def file_key(g: dict) -> str:
    """The rendered insert's cache key: the picture, its look and length, the Reel's frame, the look's version."""
    blob = json.dumps({"picture": g.get("picture"), "look": g.get("look"), "duration": round(g["duration"], 3),
                       "frame": [editor.W, editor.H, editor.FPS], "v": LOOK}, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:24]


def fallback(g: dict, why: str) -> dict | None:
    """The drawing shown instead of a picture: the plan's own drawing for it when it passes the motion library's
    checks, else a headline of what the picture would have shown. None when neither fits."""
    import motion
    raw = g.get("scene")
    if isinstance(raw, str) and raw.strip():
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = None
    tries = [raw] if isinstance(raw, dict) and raw.get("type") else []
    tries.append({"type": "headline", "text": editor.unmark(g.get("title") or "")[:90] or "Concept"})
    for scene in tries:
        scene = editor._no_pictures(scene)
        try:
            spec = motion.validate({"scenes": [{**scene, "duration": round(g["duration"], 2)}]}, insert=True)
        except motion.SpecError:
            continue
        s = spec["scenes"][0]
        return {k: g[k] for k in ("clip_id", "at", "start", "duration") if k in g} | {
            "style": "scene", "title": g.get("title") or s.get("title") or s.get("text") or "", "lines": [],
            "type": s["type"], "scene": scene, "look": g.get("look") or "cutaway", "role": g.get("role") or "explain",
            "why": g.get("why") or "", "instead_of_picture": why[:160]}
    return None


def make(pics: list[dict], conn, production: dict, version: int, ref: str, client=None
         ) -> tuple[list[dict], list[dict], list[str], dict]:
    """Each placed picture drawn (or from the cache) and rendered as a transparent WebM in editor.INSERTS.
    Returns the pictures that made it (each with its "picture" and "file"), the drawings shown instead of the
    rest (fallback), what happened in plain words, and the tally {placed, new, drawn, reused, usd, paid, instead}.
    paid: new pictures this draft version has drawn so far (carried across Studio's own fix rounds, which
    render the same version), at most settings.REEL_PICTURES_MAX."""
    import concepts
    previous = json.loads(production.get("edl") or "{}")
    paid = int((previous.get("pictures") or {}).get("paid") or 0) if production.get("version") == version else 0
    why_off = available(post_type(conn, production)) if pics else None
    done, instead, notes = [], [], []
    tally = {"placed": 0, "new": 0, "drawn": 0, "reused": 0, "usd": 0.0, "paid": paid, "instead": []}
    editor.INSERTS.mkdir(parents=True, exist_ok=True)
    for g in pics:
        why = why_off
        res = None
        if not why:
            g["image"] = g.get("image") or brief_for(g.get("title"))
            res = concepts.reel_picture(g["image"], g.get("title") or "", ref, client,
                                        may_draw=tally["paid"] < settings.REEL_PICTURES_MAX)
            tally["drawn"] += res["drawn"]
            tally["usd"] += res["usd"]
            if res["drawn"]:
                tally["new"] += 1
                tally["paid"] += 1
            why = None if res["path"] else (res["why"] or "the picture couldn't be made")
        if not why:
            g["picture"] = res["key"]
            key = file_key(g)
            out = editor.INSERTS / f"{key}.webm"
            try:
                if out.exists() and out.stat().st_size > 0:
                    out.touch()
                else:
                    part = editor.INSERTS / f"{key}.part.webm"
                    render_one(res["path"], g["look"], g["duration"], part)
                    part.replace(out)
                tally["reused"] += res["cached"]
                done.append({**g, "file": key})
                continue
            except Exception as e:  # a draft with the drawing beats no draft
                log.warning("Picture insert \"%s\" didn't render: %s: %s", g.get("title"), type(e).__name__, e)
                why = "the picture didn't render"
        drawing = fallback(g, why)
        tally["instead"].append({"title": g.get("title") or "", "why": why})
        if drawing:
            instead.append(drawing)
            notes.append(f"drew \"{g.get('title')}\" instead of showing a picture ({why})")
        else:
            notes.append(f"left out the picture \"{g.get('title')}\" ({why})")
    tally["placed"] = len(done)
    tally["usd"] = round(tally["usd"], 4)
    return done, instead, notes, tally


def ensure_file(g: dict) -> bool:
    """A picture insert's WebM there (rendered again from the kept picture if the cache was cleared; never drawn
    again), for the Resolve project."""
    out = editor.INSERTS / f"{g.get('file') or file_key(g)}.webm"
    if out.exists():
        return True
    import concepts
    path = concepts.reel_cached(g.get("image") or "")
    if not path:
        return False
    try:
        render_one(path, g.get("look") or "cutaway", g["duration"], out)
        return True
    except Exception as e:
        log.warning("Picture insert \"%s\" couldn't be rendered again: %s", g.get("title"), e)
        return False


def _font(size: int):
    from PIL import ImageFont
    try:
        return ImageFont.truetype(str(FONT), size)
    except OSError:  # a Pillow without WOFF2: its own font, still a label
        return ImageFont.load_default(size)


def label_image(scale: float = 1.0):
    """The "Concept" mark: small letter-spaced capitals on a soft dark tag, in the gallery look's warm white."""
    from PIL import Image, ImageDraw
    font = _font(round(22 * scale))
    text = LABEL.upper()
    track = 0.14 * 22 * scale
    widths = [font.getlength(ch) for ch in text]
    tw = sum(widths) + track * (len(text) - 1)
    pad_x, pad_y, th = round(16 * scale), round(11 * scale), round(22 * scale)
    img = Image.new("RGBA", (round(tw) + 2 * pad_x, th + 2 * pad_y), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, img.width - 1, img.height - 1), radius=round(8 * scale), fill=(14, 18, 20, 150))
    x = pad_x
    for ch, w in zip(text, widths):
        d.text((x, pad_y + th * 0.82), ch, font=font, fill=(246, 244, 238, 240), anchor="ls")
        x += w + track
    return img


def _ease(x: float) -> float:
    x = min(max(x, 0.0), 1.0)
    return 0.5 - 0.5 * math.cos(math.pi * x)


def _sparse(img, x: int = 0, y: int = 0) -> tuple:
    """A mostly transparent RGBA layer as its visible pixels only (rows, columns, colour, alpha), so laying it
    over a frame touches just those (a frame is 1080x1920: whole-frame float maths would take most of a second)."""
    a = np.asarray(img.convert("RGBA"))
    ys, xs = np.nonzero(a[..., 3])
    return ys + y, xs + x, a[ys, xs, :3].astype(np.uint16), a[ys, xs, 3].astype(np.uint16)[:, None]


def _lay(frame, layer: tuple, dy: int = 0) -> None:
    """A _sparse layer over an RGBA frame (uint8) whose pixels under it are opaque (the picture), in place."""
    ys, xs, rgb, a = layer
    ys = ys + dy
    keep = (ys >= 0) & (ys < frame.shape[0])
    ys, xs, rgb, a = ys[keep], xs[keep], rgb[keep], a[keep]
    under = frame[ys, xs, :3].astype(np.uint16)
    frame[ys, xs, :3] = ((rgb * a + under * (255 - a)) // 255).astype(np.uint8)


def render_one(picture: Path, look: str, duration: float, out: Path) -> None:
    """One picture insert as a transparent VP9 WebM at the Reel's size and frame rate: the picture cover-fitted
    (full screen for a cutaway; a rounded card from PANEL_TOP to PANEL_BOTTOM for a panel, with a hairline and a
    soft shadow), a slow push-in over its whole length (an exact sub-pixel scale each frame, so it never
    shimmers), a dissolve in and out (a panel also rises into place), a soft shade at the top and the Concept
    mark."""
    import cv2
    from PIL import Image, ImageDraw, ImageFilter
    import extract
    W, H, FPS = editor.W, editor.H, editor.FPS
    frames = max(1, round(duration * FPS))
    src = np.asarray(Image.open(picture).convert("RGB"))
    panel = look == "panel"
    x0, y0, x1, y1 = (PANEL_X0, editor.PANEL_TOP, PANEL_X1, editor.PANEL_BOTTOM) if panel else (0, 0, W, H)
    bw, bh = x1 - x0, y1 - y0
    # Scaled once to the closest the push-in gets, so every frame only shrinks it a little (smooth and fast).
    k = max(bw / src.shape[1], bh / src.shape[0]) * (1 + PUSH)
    big = cv2.resize(src, (max(1, round(src.shape[1] * k)), max(1, round(src.shape[0] * k))), interpolation=cv2.INTER_CUBIC)
    bcx, bcy = big.shape[1] / 2, big.shape[0] / 2
    tag = label_image()
    if panel:
        mask = Image.new("L", (bw, bh), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, bw - 1, bh - 1), radius=28, fill=255)
        coverage = np.asarray(mask)
        inside = coverage == 255
        ey, ex = np.nonzero((coverage > 0) & (coverage < 255))  # the rounded corners' soft edge
        edge = (ey, ex, coverage[ey, ex].astype(np.uint16)[:, None])
        shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(shadow).rounded_rectangle((x0, y0 + 14, x1, y1 + 14), radius=32, fill=(0, 0, 0, 90))
        shadow = np.asarray(shadow.filter(ImageFilter.GaussianBlur(26))).copy()
        rim = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
        ImageDraw.Draw(rim).rounded_rectangle((0, 0, bw - 1, bh - 1), radius=28, outline=(246, 244, 238, 110), width=2)
        rim.alpha_composite(tag, (28, 28))
        rim = _sparse(rim, x0, y0)
    else:
        shade = Image.new("RGBA", (W, 520), (14, 18, 20, 0))
        shade.putalpha(Image.fromarray((115 * (1 - np.linspace(0, 1, 520)) ** 1.6)[:, None].repeat(W, 1).astype(np.uint8)))
        shade.alpha_composite(tag, (92, 236))
        top = _sparse(shade)
    cmd = [extract.resolve(settings.FFMPEG), "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgba",
           "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-b:v", "0",
           "-crf", "18", "-deadline", "realtime", "-cpu-used", "8", "-row-mt", "1", "-auto-alt-ref", "0", "-an", str(out)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        for i in range(frames):
            t = i / FPS
            f = (1 + PUSH * _ease(t / max(duration, 0.01))) / (1 + PUSH)
            m = np.float32([[f, 0, bw / 2 - f * bcx], [0, f, bh / 2 - f * bcy]])
            pic = cv2.warpAffine(big, m, (bw, bh), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
            fade = max(0.0, min(1.0, t / FADE_IN, (duration - t) / FADE_OUT))
            if panel:
                dy = round(40 * (1 - _ease(t / RISE)))
                frame = np.zeros((H, W, 4), dtype=np.uint8)
                frame[max(dy, 0):, :] = shadow[:H - max(dy, 0)]
                card = frame[y0 + dy:y1 + dy, x0:x1]
                card[inside, :3] = pic[inside]
                card[inside, 3] = 255
                ey, ex, ea = edge
                under = card[ey, ex].astype(np.uint16)
                card[ey, ex, :3] = ((pic[ey, ex].astype(np.uint16) * ea + under[:, :3] * (255 - ea)) // 255).astype(np.uint8)
                card[ey, ex, 3] = (ea[:, 0] + under[:, 3] * (255 - ea[:, 0]) // 255).astype(np.uint8)
                _lay(frame, rim, dy)
            else:
                frame = cv2.cvtColor(pic, cv2.COLOR_RGB2RGBA)
                _lay(frame, top)
            if fade < 1.0:
                frame[..., 3] = cv2.convertScaleAbs(frame[..., 3], alpha=fade)
            proc.stdin.write(frame.tobytes())
        proc.stdin.close()
        err = proc.stderr.read().decode("utf-8", "replace")
        if proc.wait() != 0 or not out.exists():
            raise RuntimeError("ffmpeg couldn't encode the picture insert: " + err.strip()[-300:])
    except BaseException:
        proc.kill()
        proc.wait()
        out.unlink(missing_ok=True)
        raise


def bright(pics: list[dict]) -> list[list[float]]:
    """While a full-screen picture is up the captions are over a photo: the stronger glow (editor.lit_captions)."""
    return [[g["start"], round(g["start"] + g["duration"], 3)] for g in pics if g.get("look") != "panel"]


def summary(pics: list[dict], instead: list[dict]) -> str:
    """For "What the editor did": the pictures shown (AI, marked Concept), and once, why any insert was drawn
    instead of a picture."""
    out = []
    if pics:
        parts = [f"{g['start']:.0f}-{g['start'] + g['duration']:.0f} s: {g['title']}"
                 + (f" ({g['why']})" if g.get("why") else "") for g in pics]
        n = len(pics)
        out.append(f"Studio showed {'an AI picture' if n == 1 else f'{n} AI pictures'} over the footage, marked "
                   f"Concept ({'; '.join(parts)}).")
    if instead:
        whys = list(dict.fromkeys(x["why"] for x in instead))
        names = ", ".join(f"\"{x['title']}\"" for x in instead[:4])
        out.append(f"Studio drew {names} instead of showing {'a picture' if len(instead) == 1 else 'pictures'}: "
                   f"{'; '.join(whys[:2])}.")
    return " ".join(out)


def off_note(conn, production: dict) -> str:
    """Once in "What the editor did", when the draft's inserts are drawn because Studio can't make pictures at
    all (turned off, no image key): why. "" otherwise (a Finished homes post never has pictures: nothing to say)."""
    import concepts
    why = available(post_type(conn, production))
    if not why or why == concepts.NOT_IN_FINISHED:
        return ""
    return f"{why[0].upper()}{why[1:]}, so the inserts are drawn."


def listed(edl: dict) -> list[dict]:
    """The draft's pictures, for the read and Review: what was shown (AI, not filmed) and when."""
    return [{"start": g["start"], "end": round(g["start"] + g["duration"], 2), "title": g.get("title", ""),
             "look": g.get("look", "cutaway"), "role": g.get("role", "explain"), "why": g.get("why", ""),
             "asked": bool(g.get("asked"))}
            for g in (edl or {}).get("graphics", []) if g.get("style") == STYLE and "start" in g]
