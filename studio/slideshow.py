"""
Slideshow Reels: a carousel's slides made into a 9:16 Reel with one of Bryan's Epidemic Sound tracks in it
(Bryan, 2026-10-21: he wants carousels posted with music, and Instagram's API can't add music to a carousel:
"both — build slideshow Reels too"). Studio reviews, schedules and posts it like any Reel. The carousel stays as
it is (he still posts some from the Instagram app, with its own music).

  1. make(): Review's "Make a slideshow Reel" (Bryan, on a carousel waiting for review or approved) files a new
     production of the same idea, format 'slideshow', queued for the editing worker. The carousel's PNGs and
     words are copied into the Reel's own folder (Renders/slideshow_<id>), so the carousel's later versions and
     its clean-up never change it. One live slideshow per carousel: pressing it again opens that one. The
     editor's first run lays the slides out again for the Reel (reel_layout: no "Swipe" cue or "2/6" counter,
     the Urban Makers mark kept); a slide whose picture can't be had again keeps the carousel's own PNG.
  2. run(), in the editing worker: each slide's time from its words (READ_BASE + READ_PER_CHAR a character,
     MIN_SECONDS to MAX_SECONDS, the cover COVER_EXTRA longer), the music picked as for any Reel
     (music.find_track with Bryan's taste; a track link pasted in a note, on this draft or on the carousel, is
     exactly that track), then one HyperFrames page: each 1080x1350 slide in a 1080x1920 frame on the end card's
     brand background, inside Instagram's Reel safe zones (clear of the top bar, the caption and buttons at the
     bottom, the buttons on the right), short crossfades, a very slow push-in (and back out on the next slide)
     that only ever scales the whole slide (nothing is cropped), then the close (editor: the beat, the end card
     dissolving in, the music fading out). mix_final sets the music for no voice and encodes H.264/AAC like
     every Reel. No captions (no voice); the first frame is the cover.
  3. A send-back: Bryan's notes change the slides' times, their order (or drop a slide) and the music, read in one
     short Claude call (plan_changes); a note that's only a track link needs no call. Edit it again re-times the
     slides and picks the music again; Start over goes back to the slides' first order too.
  4. Studio's read, its watch-through (final_check: no voice, so no listen-back) and its own fix rounds (self_fix:
     the music level as a patch, times and order as a re-plan) run as on any Reel, then To review.

No beat matching: music.py doesn't give a track's beat times, so slides keep their reading times. Cost: the music
pick and Studio's read, as any Reel's first draft (no edit plan); a send-back with notes adds one short call.
"""
import json
import logging
import shutil
import tempfile
from pathlib import Path

import catalog
import editor
import extract
import music
import render
import settings

log = logging.getLogger("studio.slideshow")

FORMAT = "slideshow"
RENDERS = editor.RENDERS
W, H, FPS = editor.W, editor.H, editor.FPS
# Instagram's own buttons and words on a Reel: the top bar, the caption, name and audio line at the bottom,
# the like / comment / share column on the right (kept as wide on the left, so the slide sits centred).
SAFE_TOP, SAFE_BOTTOM, SAFE_SIDE = 220, 420, 120
PUSH = 1.03            # the slow push-in: the whole slide grows to 103% over its time (the next one eases back
                       # from there, so a crossfade never shows two sizes), never cropped
# The slide's size and place: at full push it still fits inside the safe zones.
SLIDE_W = int((W - 2 * SAFE_SIDE) / PUSH) // 2 * 2
SLIDE_H = round(SLIDE_W * 1350 / 1080)
SLIDE_X = (W - SLIDE_W) // 2
SLIDE_Y = SAFE_TOP + (H - SAFE_TOP - SAFE_BOTTOM - SLIDE_H) // 2
# Time per slide from its words (Bryan's reading pace: final_check's 0.3 s plus 0.06 s a letter).
READ_BASE, READ_PER_CHAR = 0.3, 0.06
MIN_SECONDS, MAX_SECONDS = 2.5, 6.0
COVER_EXTRA = 0.8      # the cover a little longer: it's the hook
NOTE_MIN, NOTE_MAX = 1.5, 8.0   # what a note may ask a slide's time to be
FADE = 0.45            # the crossfade from one slide to the next
# The music when nothing else says: Bryan's taste (music.TASTE), carrying the post with no voice over it.
MUSIC = {"mood": "soft, warm and unhurried", "search": "gentle warm acoustic instrumental, airy, light",
         "tempo_bpm": 90, "volume_db": -16}
NO_VOICE = "No voice over it: the music carries the post on its own, start to finish"


def seconds_for(slide: dict, cover: bool = False) -> float:
    """How long a slide stays up: long enough to read its words, the cover a little longer."""
    t = READ_BASE + READ_PER_CHAR * len(slide_text(slide))
    return round(min(max(t, MIN_SECONDS), MAX_SECONDS) + (COVER_EXTRA if cover else 0.0), 2)


def slide_text(slide: dict) -> str:
    return " ".join(x for x in ((slide.get("heading") or "").strip(), (slide.get("body") or "").strip()) if x)


# ── Making one from a carousel ────────────────────────────────────────────────

def made_from(conn, carousel_id: int):
    """The live slideshow Reel made from this carousel (not rejected or removed), if any."""
    return conn.execute(f"""SELECT * FROM productions WHERE format = '{FORMAT}' AND status <> 'rejected'
                            AND removed_at IS NULL AND json_extract(plan, '$.from_carousel') = ?
                            ORDER BY id DESC LIMIT 1""", (carousel_id,)).fetchone()


def can_make(conn, row) -> str | None:
    """Why this carousel can't be made into a slideshow Reel now (None when it can)."""
    import carousel
    if row is None:
        return "That draft is gone."
    if row["format"] != "carousel":
        return "Only a carousel can be made into a slideshow Reel."
    if row["removed_at"]:
        return "Its slides were removed in the clean-up."
    if row["status"] not in ("ready", "approved"):
        return "Wait until its slides are laid out and waiting for review (or approved)."
    if len(carousel.files(row)) < 2 or len(carousel.files(row)) != len(carousel.current(row)["slides"]):
        return "Its slides aren't laid out yet."
    return None


def make(conn, carousel_id: int) -> tuple[int, bool]:
    """A new production of the carousel's idea as a slideshow Reel, queued for the editor; the carousel itself
    isn't touched. Returns (its id, True), or (the live one's id, False) when there already is one."""
    import carousel
    row = conn.execute("SELECT * FROM productions WHERE id = ?", (carousel_id,)).fetchone()
    why = can_make(conn, row)
    if why:
        raise RuntimeError(why)
    live = made_from(conn, carousel_id)
    if live is not None:
        return live["id"], False
    data, pngs = carousel.current(row), carousel.files(row)
    cplan = json.loads(row["plan"] or "{}")
    idea = conn.execute("SELECT music_plan FROM ideas WHERE id = ?", (cplan.get("idea_id"),)).fetchone()
    try:
        music_plan = json.loads(idea["music_plan"] or "{}") if idea else {}
    except ValueError:
        music_plan = {}
    shown = {x.get("n"): x.get("source") for x in data.get("pictures") or [] if isinstance(x, dict)}
    plan = {"idea_id": cplan.get("idea_id"), "title": row["title"], "brand": row["brand"], "format": FORMAT,
            "post_type": carousel.post_type(conn, row), "hook": cplan.get("hook") or "",
            "purpose": cplan.get("purpose") or "", "cta": cplan.get("cta") or "", "from_carousel": carousel_id,
            "from_version": row["version"], "music_plan": music_plan if isinstance(music_plan, dict) else {},
            # Each slide's words and picture (to lay it out again for the Reel), what the carousel showed, and the
            # carousel's own PNG (kept as it was; shown only when the Reel's layout can't be made).
            "slides": [{"n": n, "role": s.get("role", ""), "heading": s.get("heading", ""), "body": s.get("body", ""),
                        "image": s.get("image") or {"source": "text"},
                        "shown": shown.get(n) or (s.get("image") or {}).get("source") or "text",
                        "carousel_file": str(p), "file": str(p)}
                       for n, (s, p) in enumerate(zip(data["slides"], pngs), 1)]}
    stamp = catalog.now()
    brief = (f"A slideshow Reel of the carousel \"{row['title']}\" (v{row['version']}): its {len(pngs)} slides "
             f"with music.\n\n{plan['hook']}").strip()
    with conn:
        pid = conn.execute(
            """INSERT INTO productions (title, brand, format, status, version, idea, source_clips, caption, plan,
                   created_at, updated_at) VALUES (?, ?, ?, 'planned', 0, ?, '[]', ?, ?, ?, ?)""",
            (row["title"], row["brand"], FORMAT, brief, row["caption"], json.dumps(plan, ensure_ascii=False),
             stamp, stamp)).lastrowid
    _own_slides(conn, pid, plan, layout=False)  # copied now, while the carousel's files are surely there
    log.info("Slideshow Reel %s queued from carousel %s v%s (%d slides)", pid, carousel_id, row["version"], len(pngs))
    return pid, True


def folder(production_id: int) -> Path:
    return RENDERS / f"slideshow_{production_id}"


def _own_slides(conn, production_id: int, plan: dict, layout: bool = True) -> list[dict]:
    """The slides with their PNGs in this production's own folder (copied there the first time, or after a
    Start over made it from another production): the carousel's own PNG (carousel_NN.png) and, once laid out
    for the Reel (layout: reel_layout, the editor's first run), NN.png. The plan is saved with the new paths."""
    out_dir = folder(production_id)
    slides, changed = [], False
    for s in plan.get("slides") or []:
        n = int(s["n"])
        files = {}
        for key, name in (("carousel_file", f"carousel_{n:02d}.png"), ("file", f"{n:02d}.png")):
            src = Path(s.get(key) or s.get("file") or "")
            if key == "file" and src.resolve() == Path(s.get("carousel_file") or "").resolve():
                name = f"carousel_{n:02d}.png"  # still the carousel's own (not laid out for the Reel)
            dest = out_dir / name
            if src.resolve() != dest.resolve():
                if not src.is_file():
                    raise RuntimeError(f"slide {n}'s picture is gone: make the slideshow again from the carousel")
                out_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)
                changed = True
            elif not dest.is_file():
                raise RuntimeError(f"slide {n}'s picture is gone: make the slideshow again from the carousel")
            files[key] = str(dest)
        slides.append({**s, **files})
    if len(slides) < 2:
        raise RuntimeError("this slideshow has fewer than 2 slides")
    if layout and not isinstance(plan.get("reel_layout"), dict):
        slides, fell = reel_layout(conn, out_dir, slides)
        plan["reel_layout"] = {"fallback": fell}
        changed = True
    if changed:
        plan["slides"] = slides
        conn.execute("UPDATE productions SET plan = ? WHERE id = ?", (json.dumps(plan, ensure_ascii=False), production_id))
        conn.commit()
    return slides


def reel_layout(conn, out_dir: Path, slides: list[dict]) -> tuple[list[dict], list[int]]:
    """The slides laid out again for a Reel (carousel.lay_out, reel=True: no "Swipe" cue or "2/6" counter; the
    Urban Makers mark stays), from the same words and pictures (concept pictures from their cache, so nothing is
    drawn or paid again). A slide whose picture can't be had again keeps the carousel's own PNG, and if the
    layout fails they all do. Returns the slides and the numbers of those that kept the carousel's."""
    import carousel
    work = (out_dir / "reel").resolve()
    try:
        made = carousel.lay_out(conn, [{"role": s.get("role") if s.get("role") in carousel.ROLES else "point",
                                        "heading": s.get("heading") or "", "body": s.get("body") or "",
                                        "image": carousel.clean_image(s.get("image"))} for s in slides], work, reel=True)
    except Exception as e:  # the carousel's own slides beat no Reel
        log.warning("Couldn't lay the slides out for the Reel (%s: %s); the carousel's own are used", type(e).__name__, e)
        shutil.rmtree(work, ignore_errors=True)
        return slides, [s["n"] for s in slides]
    out, fell = [], []
    for s, png, note in zip(slides, made["files"], made["pictures"]):
        if s.get("shown", "text") != "text" and note.get("source") == "text":  # the picture is gone now
            log.warning("Slide %s's picture couldn't be had again (%s); the carousel's own slide is used",
                        s["n"], note.get("missing") or "no picture")
            out.append({**s, "file": s["carousel_file"]})
            fell.append(s["n"])
            continue
        dest = out_dir / f"{int(s['n']):02d}.png"
        shutil.move(png, dest)
        out.append({**s, "file": str(dest)})
    shutil.rmtree(work, ignore_errors=True)
    return out, fell


# ── The cut: which slides, in what order, for how long ────────────────────────

def first_state(slides: list[dict], order: list[int] | None = None, music_plan: dict | None = None) -> dict:
    """The slides' first timing: in order (or the order given), each up for its words, the cover a little longer."""
    by_n = {s["n"]: s for s in slides}
    order = [n for n in (order or [s["n"] for s in slides]) if n in by_n] or [s["n"] for s in slides]
    mp = music_plan or {}
    return {"order": order, "seconds": {str(n): seconds_for(by_n[n], cover=k == 0) for k, n in enumerate(order)},
            "music": {"mood": mp.get("mood") or MUSIC["mood"], "search": mp.get("search") or MUSIC["search"],
                      "tempo_bpm": float(mp.get("tempo_bpm") or MUSIC["tempo_bpm"]), "volume_db": MUSIC["volume_db"]}}


def timeline(slides: list[dict], state: dict) -> list[dict]:
    """The slides on the Reel's clock: [{n, start, seconds, text, file, role}] in the order shown."""
    by_n = {s["n"]: s for s in slides}
    out, at = [], 0.0
    for n in state["order"]:
        s = by_n[n]
        secs = float(state["seconds"].get(str(n)) or seconds_for(s))
        out.append({"n": n, "start": round(at, 3), "seconds": round(secs, 3), "text": slide_text(s), "file": s["file"],
                    "role": s.get("role", "")})
        at += secs
    return out


def slide_at(shown: list[dict], t: float | None) -> int | None:
    """The slide on screen at t seconds (None past the slides or with no time)."""
    if t is None:
        return None
    return next((s["n"] for s in shown if s["start"] <= t < s["start"] + s["seconds"]), None)


# ── A send-back: Bryan's notes (or Studio's own fixes) on times, order and music ──

CHANGE_SCHEMA = {
    "type": "object",
    "properties": {
        "order": {"type": "array", "items": {"type": "integer"}},
        "seconds": {"type": "array", "items": {
            "type": "object", "properties": {"n": {"type": "integer"}, "seconds": {"type": "number"}},
            "required": ["n", "seconds"], "additionalProperties": False}},
        "music": {"type": "object", "properties": {
            "change": {"type": "boolean"}, "mood": {"type": "string"}, "search": {"type": "string"},
            "volume_db": {"type": "number"}},
            "required": ["change", "mood", "search", "volume_db"], "additionalProperties": False},
        "summary": {"type": "string"},
        "notes_addressed": {"type": "array", "items": {
            "type": "object", "properties": {"note_id": {"type": "integer"}, "how": {"type": "string"}},
            "required": ["note_id", "how"], "additionalProperties": False}},
    },
    "required": ["order", "seconds", "music", "summary", "notes_addressed"],
    "additionalProperties": False,
}

_CHANGE_SYSTEM = """You adjust a slideshow Reel for Bryan, who owns Urban Makers, a renovation company in \
Singapore. The Reel is his carousel's slides shown one after another with music and no voice, then the Urban \
Makers end card. You get the slides (their words and how long each is up now) and his notes. Change only what \
the notes ask for:
- order: the slide numbers in the order to show them. Keep the order as it is unless a note asks to move or \
drop a slide; never add a number that isn't listed, and keep at least two slides. The first slide is the cover.
- seconds: each shown slide's time on screen, {lo} to {hi} seconds. Keep a slide's time unless a note is about \
it or about the whole pace ("faster" is about a quarter shorter, "slower" a quarter longer). A slide needs about \
0.3 s plus 0.06 s a letter to be read: never go under that unless a note insists.
- music: change = true only when a note asks for different music (then mood and search describe what he asked \
for, in his taste: {taste}); otherwise false with the mood and search as they are. volume_db is the music's \
level (now {volume}; -16 is usual): change it only when a note asks (about 3 dB a step).
- A note about a slide's words, spelling or picture can't be changed here (they're the carousel's): leave the \
slides as they are and say so in that note's "how".
- summary: one short sentence on what changed. notes_addressed: every note by its id, with how, in a few words.
Plain words, no jargon."""


def _link_only(body: str) -> bool:
    return bool(music.track_link(body)) and not music.TRACK_LINK.sub("", body or "").strip(" .,:;-\n\t")


def plan_changes(production: dict, slides: list[dict], state: dict, notes: list[dict], client=None,
                 ref: str | None = None) -> dict:
    """Bryan's notes (or Studio's fixes) as a new state: {state, new_music, summary, notes_addressed}. A note
    that's only a track link needs no Claude call; otherwise one short one (recorded as "edit")."""
    if not notes:
        return {"state": state, "new_music": False, "summary": "", "notes_addressed": []}
    if all(_link_only(n.get("body", "")) for n in notes):
        return {"state": state, "new_music": True, "summary": "Used the track you linked.",
                "notes_addressed": [{"note_id": n["id"], "how": "Used the track you linked."} for n in notes]}
    import anthropic

    import llm
    import usage
    shown = timeline(slides, state)
    by_n = {s["n"]: s for s in slides}
    lines = [(f"Post: {production['title']}, a slideshow Reel of {len(slides)} carousel slides, "
              f"{sum(s['seconds'] for s in shown):.1f}s of slides then the end card."),
             "Slides as they are now (number, role, words; shown in this order, start and seconds):"]
    lines += [f"  {s['n']}. [{by_n[s['n']].get('role') or 'slide'}] {s['text']} | from {s['start']:.1f}s for "
              f"{s['seconds']:.1f}s" for s in shown]
    hidden = [s for s in slides if s["n"] not in state["order"]]
    if hidden:
        lines.append("Slides left out now: " + "; ".join(f"{s['n']}. {slide_text(s)}" for s in hidden))
    m = state["music"]
    lines.append(f"Music now: {m['mood']} (search: {m['search']}), level {m['volume_db']:.0f} dB.")
    lines.append("Notes:")
    for n in notes:
        on = n.get("slide") or slide_at(shown, n.get("at_seconds"))
        where = (f" (at {n['at_seconds']:.1f}s" + (f", slide {on} on screen" if on else "") + ")"
                 if n.get("at_seconds") is not None else f" (slide {on})" if on else "")
        lines.append(f"  [{n['id']}]{where} {n['body']}")
    client = client or anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY or None)
    response = llm.final_message(
        client, model=settings.CLAUDE_MODEL, max_tokens=4000,
        betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": CHANGE_SCHEMA}},
        system=_CHANGE_SYSTEM.replace("{lo}", f"{NOTE_MIN:g}").replace("{hi}", f"{NOTE_MAX:g}")
        .replace("{taste}", music.taste()["summary"]).replace("{volume}", f"{m['volume_db']:.0f} dB"),
        messages=[{"role": "user", "content": "\n".join(lines)}],
    )
    usage.record("edit", ref or f"production:{production['id']}", response)
    if response.stop_reason in ("refusal", "max_tokens"):
        raise RuntimeError(f"Claude couldn't read the notes for this slideshow ({response.stop_reason})")
    answer = json.loads(next(b.text for b in response.content if b.type == "text"))
    return _apply_changes(state, slides, answer, notes)


def _apply_changes(state: dict, slides: list[dict], answer: dict, notes: list[dict]) -> dict:
    """Claude's answer made safe: real slide numbers once each (at least two), times in range, the music level
    in fix_actions' range."""
    import fix_actions
    have = {s["n"] for s in slides}
    order = list(dict.fromkeys(n for n in answer.get("order") or [] if n in have))
    if len(order) < 2:
        order = list(state["order"])
    seconds = dict(state["seconds"])
    asked = {int(x["n"]): float(x["seconds"]) for x in answer.get("seconds") or []
             if isinstance(x, dict) and x.get("n") in have and isinstance(x.get("seconds"), (int, float))}
    by_n = {s["n"]: s for s in slides}
    for n in order:
        t = asked.get(n, seconds.get(str(n)) or seconds_for(by_n[n]))
        seconds[str(n)] = round(min(max(float(t), NOTE_MIN), NOTE_MAX), 2)
    m = dict(state["music"])
    am = answer.get("music") or {}
    lo, hi = fix_actions.MUSIC_RANGE
    if isinstance(am.get("volume_db"), (int, float)):
        m["volume_db"] = round(min(max(float(am["volume_db"]), lo), hi), 1)
    new_music = bool(am.get("change"))
    if new_music:
        m["mood"] = str(am.get("mood") or m["mood"])[:120]
        m["search"] = str(am.get("search") or m["search"])[:200]
    ids = {n["id"] for n in notes}
    return {"state": {"order": order, "seconds": seconds, "music": m}, "new_music": new_music,
            "summary": str(answer.get("summary") or "").strip()[:300],
            "notes_addressed": [{"note_id": a["note_id"], "how": str(a.get("how") or "")[:300]}
                                for a in answer.get("notes_addressed") or [] if a.get("note_id") in ids]}


# ── Drawing it ────────────────────────────────────────────────────────────────

_CSS = """
html,body{margin:0;width:1080px;height:1920px;overflow:hidden;background:var(--card-lo)}
#slideshow{position:relative;width:1080px;height:1920px;overflow:hidden;background:var(--card-lo)}
.backdrop{position:absolute;inset:0;overflow:hidden}
.backdrop .bg{position:absolute;inset:-60px;background:radial-gradient(circle at 50%% 40%%,var(--card-hi),var(--card-lo) 74%%)}
.sl{position:absolute;left:%(x)dpx;top:%(y)dpx;width:%(w)dpx;height:%(h)dpx}
.sl .frame{position:absolute;inset:0;border-radius:10px;overflow:hidden;transform-origin:50%% 50%%;
  box-shadow:0 28px 70px rgba(0,0,0,.38),0 0 0 1px rgba(255,255,255,.12)}
.sl img{display:block;width:100%%;height:100%%}
""" % {"x": SLIDE_X, "y": SLIDE_Y, "w": SLIDE_W, "h": SLIDE_H}


def page(shown: list[dict], total: float, end: dict | None, push: bool, style: str | None = None
         ) -> tuple[list[str], list[str]]:
    """The composition's clips and GSAP lines: the backdrop, each slide (the first one up from the first frame,
    the rest crossfading in over the one before; slowly pushed in, the next eased back out, and so on), and the
    end card (in the Reel's look, style)."""
    clips = [(f'<div id="bd" class="backdrop clip" data-start="0" data-duration="{total:.3f}" data-track-index="0">'
              '<div class="bg"></div></div>')]
    anims = []
    for k, s in enumerate(shown):
        start = s["start"]
        until = shown[k + 1]["start"] + FADE if k + 1 < len(shown) else total
        clips.append(f'<div id="s{k}" class="sl clip" data-start="{start:.3f}" data-duration="{until - start:.3f}" '
                     f'data-track-index="{1 + k % 2}"><div class="frame"><img src="s{k:02d}.png" alt=""></div></div>')
        if k:
            anims.append(f"tl.from('#s{k}',{{opacity:0,duration:{FADE},ease:'power1.inOut'}},{start:.3f});")
        if push:  # in, then back out on the next: at each crossfade both slides are the same size
            a, b = (1, PUSH) if k % 2 == 0 else (PUSH, 1)
            span = s["seconds"] + (0.0 if k + 1 < len(shown) else editor.END_TAIL)
            anims.append(f"tl.fromTo('#s{k} .frame',{{scale:{a}}},{{scale:{b},duration:{span:.3f},"
                         f"ease:'none'}},{start:.3f});")
    if end:
        markup, anim = editor.end_card_html(end, style)
        clips.append(markup)
        anims.append(anim)
    return clips, anims


def compose(shown: list[dict], total: float, end: dict | None, out: Path, accent: str, font: str | None = None,
            push: bool = True, style: str | None = None) -> None:
    """The slides and the end card as one 1080x1920 video (HyperFrames, headless Chrome), no sound. style: the
    Reel's look (editor.title_style), for its end card (Site Notes': over the last slide, Archivo)."""
    style = editor.title_style(style)
    site = style == "site"
    with tempfile.TemporaryDirectory(prefix="studio-slideshow-") as tmp:
        project = Path(tmp) / "slideshow"
        project.mkdir()
        css = editor._overlay_assets(project, font, serif=bool(end) and not site, site=site and bool(end))
        for k, s in enumerate(shown):
            shutil.copy(s["file"], project / f"s{k:02d}.png")
        clips, anims = page(shown, total, end, push, style)
        (project / "index.html").write_text(
            '<!doctype html><html><head><meta charset="utf-8"><script src="gsap.min.js"></script>'
            f"<style>{''.join(css)}{editor._theme_css(accent, style)}{_CSS}{editor.end_css(style) if end else ''}</style>"
            f'</head><body><div id="slideshow" data-composition-id="slideshow" data-start="0" '
            f'data-duration="{total:.3f}" data-width="{W}" data-height="{H}">' + "".join(clips) + "</div>"
            "<script>const tl=gsap.timeline({paused:true});" + "".join(anims) +
            "window.__timelines=window.__timelines||{};window.__timelines['slideshow']=tl;</script></body></html>",
            encoding="utf-8")
        (project / "meta.json").write_text(json.dumps({"id": "slideshow", "name": "slideshow", "width": W,
                                                       "height": H, "fps": FPS}), encoding="utf-8")
        result = render.hyperframes(["render", str(project), "--format", "mp4", "--quality", "standard",
                                     "--fps", str(FPS), "--output", str(out)])
        if result.returncode != 0 or not out.exists():
            raise RuntimeError("slideshow render failed: " + ((result.stderr or "") + (result.stdout or "")).strip()[-400:])


def _with_silence(video: Path, out: Path, total: float) -> None:
    """The picture with a silent sound track, so mix_final has a 'voice' to level (there is none)."""
    extract._run([settings.FFMPEG, "-y", "-v", "error", "-i", str(video), "-f", "lavfi", "-i",
                  f"anullsrc=r={editor.RATE}:cl=stereo", "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac",
                  "-t", f"{total:.3f}", str(out)])


def _silent_final(cut: Path, out: Path, total: float) -> None:
    """No music (none found, or this account adds it in Instagram): the same encode as a Reel, silent sound."""
    extract._run([settings.FFMPEG, "-y", "-v", "error", "-i", str(cut), "-map", "0:v", "-map", "0:a",
                  "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-profile:v", "high", "-pix_fmt", "yuv420p",
                  "-r", str(FPS), "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart",
                  "-t", f"{total:.3f}", str(out)])


def _linked(conn, production: dict, plan: dict) -> dict | None:
    """A track Bryan pasted in a note, on this draft or (before it was made) on the carousel: the newest wins."""
    bodies = [r["body"] for r in conn.execute(
        "SELECT body FROM feedback WHERE target = 'production' AND target_id = ? ORDER BY id",
        (plan.get("from_carousel"),))]
    bodies += [r["body"] for r in conn.execute(
        "SELECT body FROM feedback WHERE target = 'production' AND target_id = ? ORDER BY id", (production["id"],))]
    return music.track_link("\n".join(bodies))


def _music(conn, production: dict, plan: dict, state: dict, total: float, find_track, keep: bool,
           ref: str) -> tuple[Path | None, dict]:
    """The track: the draft's own when it's kept (and still the one Bryan linked, if he linked one), else
    picked as for any Reel (exactly the linked or planned track, else Bryan's taste)."""
    m = state["music"]
    info = {"mood": m["mood"], "tempo_bpm": m["tempo_bpm"]}
    linked = _linked(conn, production, plan)
    before = json.loads(production.get("music") or "{}")
    if keep and before.get("file") and Path(before["file"]).is_file() and (
            not linked or before.get("link") == linked["url"]):
        return Path(before["file"]), {**before, "mood": m["mood"], "tempo_bpm": m["tempo_bpm"]}
    chosen = linked or (plan.get("music_plan") or {}).get("chosen")
    if not (music.allowed_for(production["brand"]) and settings.EPIDEMIC_SOUND_API_KEY):
        info.update(source="instagram", note=f"Add music in the Instagram app: {m['mood']}, around "
                                             f"{m['tempo_bpm']:.0f} BPM. There's no voice, so it carries the post.")
        return None, info
    brief = editor.music_brief(m, chosen) + ". " + NO_VOICE
    try:
        path, track = (find_track or music.find_track)(brief, total, settings.ROOT / "Music", ref=ref)
        info.update(source="epidemic", title=track["title"], artist=track["artist"],
                    recording_id=track["recording_id"], file=str(path),
                    **({"link": linked["url"]} if linked else {}))
        return path, info
    except Exception as e:  # a draft without music beats no draft (Review says why)
        log.warning("Music step failed for slideshow %s: %s", production["id"], e)
        info.update(source="none", error=f"{type(e).__name__}: {e}")
        return None, info


def _summary(shown: list[dict], total: float, push: bool, card: dict | None, extra: str = "") -> str:
    secs = [s["seconds"] for s in shown]
    text = (f"Made from the carousel's slides: {len(shown)} slides, each up long enough to read its words "
            f"({min(secs):.1f} to {max(secs):.1f}s; the cover {secs[0]:.1f}s), with crossfades"
            + (" and a slow push-in" if push else "") + (", then the end card" if card else "")
            + f". {total:.0f}s in all, no voice or captions: the music carries it.")
    return (text + (" " + extra if extra else "")).strip()


def _render(conn, production: dict, plan: dict, slides: list[dict], state: dict, version: int, find_track=None,
            suffix: str = "", address_notes: bool = True, keep_music: bool = False, ref: str | None = None,
            extra: str = "", notes_addressed: list[dict] = ()) -> Path:
    """Render a state to the next draft (or a fix round's file, suffix): the slides, the close, the music,
    loudness. Stored like any Reel's draft and left 'producing' for self_fix.deliver."""
    production_id = production["id"]
    ref = ref or f"production:{production_id}"
    shown = timeline(slides, state)
    card = editor.end_card(production, plan)
    close_at = round(sum(s["seconds"] for s in shown) + editor.END_TAIL, 3)  # the beat on the last slide
    total = round(close_at + (card["seconds"] if card else 0.0), 3)
    end = {"start": close_at, "duration": card["seconds"], "ask": card["ask"], "handle": card["handle"]} if card else None
    push = settings.SLIDESHOW_PUSH_IN
    look = editor.look_now()
    music_file, music_info = _music(conn, production, plan, state, total, find_track, keep_music, ref)
    checks = [{"did": f"slide {s['n']} has a lot of words for a Reel: it's up for the longest a slide gets "
                      f"({MAX_SECONDS:.0f}s)", "counted": False}
              for s in shown if s["seconds"] >= MAX_SECONDS and READ_BASE + READ_PER_CHAR * len(s["text"]) > MAX_SECONDS]
    fell = (plan.get("reel_layout") or {}).get("fallback") or []
    if fell:
        checks.append({"did": "used the carousel's own slide " + ", ".join(str(n) for n in fell) + " (it couldn't be laid "
                              "out again for the Reel), so it still has the carousel's slide number", "counted": False})
    RENDERS.mkdir(parents=True, exist_ok=True)
    final = RENDERS / f"production_{production_id}_v{version}{suffix}.mp4"
    with tempfile.TemporaryDirectory(prefix="studio-slides-") as tmp:
        work = Path(tmp)
        compose(shown, total, end, work / "slides.mp4", editor.ACCENT.get(production["brand"], editor.ACCENT["urban_makers"]),
                font=look["caption_font"], push=push, style=look["titles"])
        _with_silence(work / "slides.mp4", work / "cut.mp4", total)
        if music_file:
            sound = editor.mix_final(work / "cut.mp4", None, music_file, state["music"]["volume_db"], total, final)
            if sound.get("music_gain_db") is not None:
                music_info["gain_db"] = sound["music_gain_db"]
        else:
            _silent_final(work / "cut.mp4", final, total)
            sound = {"music": False}
        loud = editor.check_loudness(final)
        if music_file and editor.fix_loudness(final, loud):
            loud = editor.check_loudness(final)
            checks.append({"did": "set the loudness to Instagram's level", "counted": False})
    # The tile and Review's still: the cover, as the Reel opens on it.
    thumb = final.with_suffix(".jpg")
    extract._run([settings.FFMPEG, "-y", "-v", "error", "-ss", "0.3", "-i", str(final), "-frames:v", "1", "-q:v", "3",
                  str(thumb)])
    edl = {"slideshow": {"slides": [{k: s[k] for k in ("n", "start", "seconds", "text", "role")} for s in shown],
                         "order": state["order"], "seconds": state["seconds"], "fade": FADE,
                         "push_in": PUSH if push else 1.0, "box": [SLIDE_X, SLIDE_Y, SLIDE_W, SLIDE_H],
                         "from_carousel": plan.get("from_carousel"), "from_version": plan.get("from_version")},
           "cuts": [], "graphics": [], "hook_text": "", "key_words": [], "look": look, "missing_shots": [],
           "music": dict(state["music"]), "notes_addressed": list(notes_addressed),
           "close": {"tail": editor.END_TAIL, "hold": editor.END_TAIL, "speech_end": None, "added": 0.0,
                     "at": close_at, "card": card},
           "summary": _summary(shown, total, push, card, extra)}
    qa = {**loud, "sound": sound, "checks": checks, "slideshow": True}
    conn.execute(
        """UPDATE productions SET version = ?, video_path = ?, thumb_path = ?, duration = ?, edl = ?, qa = ?,
               music = ?, error = NULL, updated_at = ?, self_fix = ? WHERE id = ?""",
        (version, str(final), str(thumb), total, json.dumps(edl, ensure_ascii=False), json.dumps(qa),
         json.dumps(music_info), catalog.now(), json.dumps({"version": version, "pending": True}), production_id))
    if address_notes:
        conn.execute("""UPDATE feedback SET status = 'addressed'
                        WHERE target = 'production' AND target_id = ? AND status = 'open'""", (production_id,))
    conn.commit()
    log.info("Slideshow %s v%d%s rendered: %d slides, %.1fs, %s LUFS", production_id, version, suffix, len(shown),
             total, loud.get("lufs"))
    return final


def _row(conn, production_id: int) -> dict:
    row = conn.execute("SELECT * FROM productions WHERE id = ?", (production_id,)).fetchone()
    if row is None:
        raise KeyError(production_id)
    return dict(row)


def run(conn, production_id: int, client=None, find_track=None) -> Path:
    """Make the next draft of a slideshow Reel (the editing worker): the first one, a send-back (Bryan's notes
    and Studio's read change the times, order or music), Edit it again (re-timed, music picked again) or Start
    over (the first order too). Then Studio's read, its own fixes and To review (self_fix.deliver)."""
    import restyle
    import self_fix
    production = _row(conn, production_id)
    was = production["status"]
    plan = json.loads(production["plan"] or "{}")
    slides = _own_slides(conn, production_id, plan)
    catalog.set_production_status(conn, production_id, "producing")
    notes = [dict(r) for r in conn.execute(
        "SELECT * FROM feedback WHERE target = 'production' AND target_id = ? AND status = 'open' ORDER BY id",
        (production_id,))]
    previous = json.loads(production.get("edl") or "{}")
    before = previous.get("slideshow") or {}
    fresh = was == "revising" and production.get("restyle") == restyle.FRESH
    again = was == "revising" and production.get("restyle") == restyle.AGAIN
    if before and not fresh:
        state = {"order": [n for n in before.get("order") or [] if any(s["n"] == n for s in slides)],
                 "seconds": dict(before.get("seconds") or {}), "music": {**MUSIC, **(previous.get("music") or {})}}
        if len(state["order"]) < 2:
            state = first_state(slides, music_plan=plan.get("music_plan"))
        if again:  # today's timings for the same order; the music picked again
            state = {**first_state(slides, state["order"], plan.get("music_plan")), "music": state["music"]}
    else:
        state = first_state(slides, music_plan=plan.get("music_plan"))
    # Studio's read of the last version rides along with a send-back, as on any Reel.
    read = [{**f, "body": f"From Studio's read: {f['body']}"} for f in self_fix.read_notes(production, notes)] \
        if before and not fresh and was == "revising" else []
    change = plan_changes(production, slides, state, notes + read, client) if before and not fresh else None
    if change:
        state = change["state"]
    keep = bool(before) and not fresh and not again and not (change or {}).get("new_music")
    _render(conn, production, plan, slides, state, production["version"] + 1, find_track, keep_music=keep,
            extra=(change or {}).get("summary", ""), notes_addressed=(change or {}).get("notes_addressed", []))
    return Path(self_fix.deliver(conn, production_id, client, find_track)["video_path"])


# ── Studio's own fix rounds (self_fix.py) ─────────────────────────────────────

def _current(production: dict, slides: list[dict]) -> dict:
    edl = json.loads(production.get("edl") or "{}")
    before = edl.get("slideshow") or {}
    if not before:
        raise RuntimeError("this slideshow has no draft to fix")
    return {"order": list(before["order"]), "seconds": dict(before["seconds"]), "music": {**MUSIC, **(edl.get("music") or {})}}


def self_revise(conn, production_id: int, notes: list[dict], round_no: int, client=None, find_track=None) -> Path:
    """One of Studio's own fix rounds: the read's fixes (as notes) change the times, order or music, rendered
    as the same version in a file of its own. Its cost is counted apart (self_fix.ref)."""
    import self_fix
    production = _row(conn, production_id)
    plan = json.loads(production["plan"] or "{}")
    slides = _own_slides(conn, production_id, plan)
    change = plan_changes(production, slides, _current(production, slides), notes, client, self_fix.ref(production_id))
    return _render(conn, production, plan, slides, change["state"], production["version"], find_track,
                   suffix=f"_fix{round_no}", address_notes=False, keep_music=not change["new_music"],
                   ref=self_fix.ref(production_id), extra=change["summary"], notes_addressed=change["notes_addressed"])


def patch(conn, production_id: int, actions: list[dict], round_no: int, client=None,
          find_track=None) -> tuple[Path | None, list[dict]]:
    """One of Studio's own fix rounds made as patches: the music level (fix_actions' own change); anything
    else needs a re-plan. Returns the render (None when nothing could be made) and each action's result."""
    import fix_actions
    import self_fix
    production = _row(conn, production_id)
    plan = json.loads(production["plan"] or "{}")
    slides = _own_slides(conn, production_id, plan)
    state = _current(production, slides)
    results = []
    for item in actions:
        a = item["action"]
        res = {"n": item["n"], "type": a.get("type")}
        try:
            if a.get("type") != "music_level":
                raise fix_actions.NotApplied("that needs a new edit")
            box = {"music": state["music"]}
            res.update(ok=True, did=fix_actions._music(box, a))
        except fix_actions.NotApplied as e:
            res.update(ok=False, why=str(e))
        results.append(res)
    if not any(r.get("ok") for r in results):
        return None, results
    final = _render(conn, production, plan, slides, state, production["version"], find_track, suffix=f"_fix{round_no}",
                    address_notes=False, keep_music=True, ref=self_fix.ref(production_id))
    return final, results


def detail(conn, row) -> dict:
    """What Review shows on a slideshow Reel: the carousel it came from and each slide's time."""
    plan = json.loads(row["plan"] or "{}")
    source = conn.execute("SELECT id, title, status, version FROM productions WHERE id = ?",
                          (plan.get("from_carousel"),)).fetchone()
    shown = (json.loads(row["edl"] or "{}").get("slideshow") or {}).get("slides") or []
    by_n = {s.get("n"): s for s in plan.get("slides") or []}
    return {"from": {"id": source["id"], "title": source["title"], "status": source["status"],
                     "version": plan.get("from_version")} if source else None,
            "slides": [{"n": s["n"], "start": s["start"], "seconds": s["seconds"],
                        "heading": (by_n.get(s["n"]) or {}).get("heading", "")} for s in shown],
            "count": len(plan.get("slides") or [])}


def carousel_detail(conn, row) -> dict:
    """What Review shows on a carousel about its slideshow Reel: can one be made, and the one made."""
    live = made_from(conn, row["id"])
    return {"why_not": can_make(conn, row), "made": {"id": live["id"], "status": live["status"],
                                                      "version": live["version"]} if live else None}
