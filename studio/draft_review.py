"""
Studio's read of a draft: after the editor renders a draft, Claude watches it (a few
stills of the final video, the captions and words on its own clock, the cut list and
the checks) and writes a short review for Bryan: a one-line verdict, a good/fix score
for hook, pace, sound, captions and picture, the few changes that matter most (each
pinned to a moment), and what already works.

A carousel (format 'carousel') gets its own read: Claude looks at every slide image and the
slide text, and checks the words are big enough to read on a phone, the spelling, that the
claims are sensible, that the ask uses only the offers listed in the planner, and that no
place name or address appears. Its fixes name a slide instead of a moment.

One Claude call per draft version, a few cents. A failed review never fails the draft:
it's logged, the draft stays ready, and the dashboard offers "Get Studio's read".
Stored as productions.review (JSON) with review_version, the version it describes.
"""
import base64
import json
import logging
import re
import tempfile
import threading
from pathlib import Path

import catalog
import settings

log = logging.getLogger("studio.review")

AREAS = ["hook", "pace", "sound", "captions", "picture", "story", "other"]
SHOT_KINDS = ["talking", "broll", "detail", "before_after", "reaction", "screen", "none"]
STILLS = 6
MAX_FIXES, MAX_WORKS = 5, 4

_score = {"type": "string", "enum": ["good", "fix"]}
_seconds = {"anyOf": [{"type": "number"}, {"type": "null"}]}

SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string"},
        "scores": {
            "type": "object",
            "properties": {k: _score for k in ("hook", "pace", "sound", "captions", "picture")},
            "required": ["hook", "pace", "sound", "captions", "picture"],
            "additionalProperties": False,
        },
        "fixes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "what": {"type": "string"},
                    "why": {"type": "string"},
                    "start": _seconds,
                    "end": _seconds,
                    "area": {"type": "string", "enum": AREAS},
                    "when": {"type": "string", "enum": ["this_draft", "next_shoot"]},
                    "kind": {"type": "string", "enum": SHOT_KINDS},
                    "editor_can_fix": {"type": "boolean"},
                    "rule": {"type": "string"},
                    "same_rule": {"type": "integer"},
                    "illustrate": {"type": "string"},
                },
                "required": ["what", "why", "start", "end", "area", "when", "kind", "editor_can_fix", "rule",
                             "same_rule", "illustrate"],
                "additionalProperties": False,
            },
        },
        "works": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["verdict", "scores", "fixes", "works"],
    "additionalProperties": False,
}

_SYSTEM = """You review draft Instagram Reels for Bryan, who owns Urban Makers, a renovation \
company in Singapore. He isn't technical and often reads this on his phone. Give him a quick, \
honest read of the draft: is it ready to post, and what are the few changes that would make it \
better. You get stills of the finished draft, what's on screen and said at each moment (on the \
draft's own clock), the cut list, the checks the editor ran, and the playbook the editor follows.

<playbook>
{playbook}
</playbook>

How to answer:
- verdict: one short line. "Ready to post" when nothing in this draft needs changing; \
"Nearly ready: N things to fix" (N = your this_draft fixes) for small fixes; "Needs work: <the \
main problem in a few words>" when it shouldn't go out as it is.
- scores: "good" or "fix" for the hook (first 2-3 seconds), pace (dead air, drag, length), sound \
(voice clear, music level, loudness), captions (readable, timed, correct) and picture (framing, \
light, focus, graphics).
- fixes: at most 5, the most important first. "what" is the action in at most 12 words, starting \
with a verb ("Turn the music down at 0:12-0:18"). "why" is one plain reason in at most 18 words. \
start and end are seconds on the draft's clock, or null when it isn't about one moment. area is \
what it's about.
- when: "this_draft" only for things the editor can change from the footage it already has \
(trim, cut, reorder, captions, hook text, graphics, music level). Anything that needs new footage \
is "next_shoot", and its "what" says what to film. kind is the kind of shot to film for a \
next_shoot fix (talking, broll, detail, before_after, reaction, screen), otherwise "none".
{fixable}
{illustrate}
- works: 2 to 4 short points on what already works, so it's kept in the next version.
- Place names stay internal: if an estate, condo, street or building name, an address or a \
client's name appears in the on-screen text, captions or hook, that is always a this_draft fix \
(area captions) to remove it.
- Sound: the checks say how Studio levelled the voice and how far the music sits under it. Only \
call the voice quiet or the music too loud when those numbers or the words say so.
- Only say what the material shows. Never invent facts, numbers, times or problems; if you're \
unsure something is wrong, leave it out. Use the timestamps given, don't guess new ones.
- Plain words, no jargon (no "LUFS", "dB", "EDL", "B-roll" or "SNR"). Short sentences."""


_FIXABLE = """\
- editor_can_fix: true when Studio's editor can make the change itself from what it already has, \
with no decision from Bryan: {examples}. false for anything that needs new footage or photos, \
spending (drawing new pictures), and judgement calls only Bryan can make (whether to post it at \
all, a change of message or offer, a claim only he can confirm, choosing between two good options). \
Studio fixes the editor_can_fix ones before Bryan sees the draft, so mark them carefully.
- rule: for an editor_can_fix fix, the general editing rule the problem breaks, as an instruction \
for any future post in at most 14 words ("End on the last full sentence, never on a word that \
starts the next"); same_rule is the number of the rule in "Editing rules Studio learned" it repeats \
(R3 = 3), else 0. For other fixes rule is "" and same_rule 0."""
_REEL_FIXABLE = ("trims and cuts, the order of the opening, the hook text, pace, caption words and timing, a "
                 "graphic's or drawn animation's wording, placement or timing (or removing it), the music level")
# Drawn inserts (editor.place_scenes): what the read says about them, and "Draw it for now".
_ILLUSTRATE = """\
- Drawn animations: the material lists any animation Studio drew over the footage (drawn, not \
filmed). Judge them like graphics: do they show what Bryan says while he says it, are they quick to \
read, do they cover his face or the captions, are there too many? Removing or retiming one is an \
editor_can_fix fix.
- illustrate: only on a next_shoot fix for a missing shot that a simple drawing could stand in for \
until it's filmed (a build-up of layers, a dimension, a layout, a process, a comparison): what to \
draw, in at most 12 words ("a cross-section: old tiles, adhesive, new tiles on top"). Studio then \
draws it for now in this draft and your next_shoot fix stays so Bryan still films it. Never for a \
person, a client's home, a finished result or real workmanship: those must be filmed. Otherwise "", \
and "" on every other fix."""
_NO_ILLUSTRATE = '- illustrate: always "".'
ILLUSTRATE_PREFIX = "Draw it for now: "
_CAROUSEL_FIXABLE = ("slide wording, spelling, length, order, the cover's words, the ask's wording (within the listed "
                     "offers), removing a place name, the caption, swapping a slide to text only")


# ── Carousels ─────────────────────────────────────────────────────────────────

CAROUSEL_AREAS = ["cover", "text", "legibility", "spelling", "claims", "ask", "picture", "caption", "other"]
CAROUSEL_SCORES = ("cover", "legibility", "spelling", "claims", "ask")
_slide_no = {"anyOf": [{"type": "integer"}, {"type": "null"}]}
CAROUSEL_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string"},
        "scores": {
            "type": "object",
            "properties": {k: _score for k in CAROUSEL_SCORES},
            "required": list(CAROUSEL_SCORES),
            "additionalProperties": False,
        },
        "fixes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "what": {"type": "string"},
                    "why": {"type": "string"},
                    "slide": _slide_no,
                    "area": {"type": "string", "enum": CAROUSEL_AREAS},
                    "editor_can_fix": {"type": "boolean"},
                    "rule": {"type": "string"},
                    "same_rule": {"type": "integer"},
                },
                "required": ["what", "why", "slide", "area", "editor_can_fix", "rule", "same_rule"],
                "additionalProperties": False,
            },
        },
        "works": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["verdict", "scores", "fixes", "works"],
    "additionalProperties": False,
}

_CAROUSEL_SYSTEM = """You review draft Instagram carousels for Bryan, who owns Urban Makers, a \
renovation company in Singapore. He isn't technical and often reads this on his phone. You get \
every slide as an image (1080x1350, shown on a phone about 390 points wide), the words on each \
slide, the post caption, the kind of post, and the offers Urban Makers really makes. Give him a \
quick, honest read: is it ready to post, and what are the few changes that would make it better.

<content_plan>
{content_plan}
</content_plan>

How to answer:
- verdict: one short line. "Ready to post" when nothing needs changing; "Nearly ready: N things \
to fix" (N = your fixes) for small fixes; "Needs work: <the main problem in a few words>" when it \
shouldn't go out as it is.
- scores: "good" or "fix" for the cover (does slide 1 stop the scroll and say what the post \
gives), legibility (every word big and clear enough on a phone, enough contrast over pictures, \
nothing cut off or crowded), spelling (spelling and grammar), claims (sensible and true for a \
Singapore renovation company: no made-up figures, guarantees or rules) and ask (the last slide's \
call to action fits the kind of post and offers only what's listed under Offers).
- fixes: at most 5, the most important first. "what" is the action in at most 12 words, starting \
with a verb ("Shorten slide 3's text to two lines"). "why" is one plain reason in at most 18 \
words. slide is the slide number it's about (1 = the cover), or null for the whole post or the \
caption. area is what it's about.
{fixable}
- An offer, price, discount, promotion or deadline that isn't in the listed offers is always a \
fix (area ask). A place name (estate, condo, street, block, town), an address or a client's name \
on a slide or in the caption is always a fix to remove it.
- A slide marked "Concept" shows an AI concept picture, not real work. Its words must never say \
it's a home Urban Makers built or a client's home (a fix, area picture), and a concept picture in \
a Finished homes post is always a fix (area picture: use a real photo).
- works: 2 to 4 short points on what already works, so it's kept in the next version.
- Only say what the slides show. Never invent problems; if you're unsure something is wrong, \
leave it out. Plain words, no jargon. Short sentences."""


def _slide_jpegs(paths: list[Path], work: Path) -> list[Path]:
    """Each slide as a small JPEG for Claude to look at (540 px wide is plenty to judge a phone)."""
    from PIL import Image
    out = []
    for n, png in enumerate(paths, 1):
        jpg = work / f"slide_{n}.jpg"
        with Image.open(png) as img:
            img = img.convert("RGB")
            img.thumbnail((540, 675), Image.LANCZOS)
            img.save(jpg, "JPEG", quality=85)
        out.append(jpg)
    return out


def carousel_context(production: dict, data: dict) -> str:
    import carousel
    import content_plan
    plan = json.loads(production.get("plan") or "{}")
    kind = content_plan.KINDS.get(plan.get("post_type") or "")
    lines = [f"Post: {production['title']}, a carousel of {len(data['slides'])} slides, draft v{production['version']}.",
             "Kind of post: " + (f"{kind['label']} (its ask: {kind['ask']})" if kind else "not set"),
             "Slides (the words on each, and its picture):"]
    for n, s in enumerate(data["slides"], 1):
        lines.append(f"  {n}. [{s['role']}] {s['heading']}" + (f" | {s['body']}" if s.get("body") else "")
                     + f" | picture: {carousel.picture_text(s['image'])}")
    missing = [p for p in data.get("pictures", []) if p.get("missing")]
    for p in missing:
        lines.append(f"  (slide {p['n']} asked for a picture Studio couldn't get, so it's text only: {p['missing']})")
    lines.append(f"Caption:\n{production.get('caption') or '(none yet)'}")
    import learning
    rules = learning.rules_text("carousel")
    if rules:
        lines.append(rules)
    return "\n".join(lines)


def _learnable(f: dict) -> dict:
    """editor_can_fix, and the rule it teaches (only for the ones the editor can fix)."""
    can = f.get("editor_can_fix") is True and f.get("when", "this_draft") != "next_shoot"
    rule = re.sub(r"\s+", " ", str(f.get("rule") or "")).strip()[:200] if can else ""
    same = f.get("same_rule") if can and isinstance(f.get("same_rule"), int) and f.get("same_rule") > 0 else 0
    return {"editor_can_fix": can, "rule": rule, "same_rule": same}


def _clean_carousel(result: dict, slides: int) -> dict:
    fixes = []
    for f in result.get("fixes", [])[:MAX_FIXES]:
        n = f.get("slide")
        fixes.append({"what": f.get("what", ""), "why": f.get("why", ""),
                      "slide": n if isinstance(n, int) and 1 <= n <= slides else None,
                      "area": f.get("area") if f.get("area") in CAROUSEL_AREAS else "other",
                      # Everything in a carousel can change in this draft.
                      "when": "this_draft", "start": None, "end": None, "kind": None,
                      **_learnable(f)})
    works = [w.strip() for w in result.get("works", []) if w.strip()][:MAX_WORKS]
    return {"kind": "carousel", "verdict": result.get("verdict", "").strip(),
            "scores": {k: v for k, v in (result.get("scores") or {}).items() if k in CAROUSEL_SCORES},
            "fixes": fixes, "works": works}


def concept_fixes(production: dict, data: dict, kind: str | None) -> list[dict]:
    """Studio's own fixes about concept pictures, whatever Claude says: a concept in a Finished
    homes post, and slides left as text because their concept pictures didn't work out."""
    import concepts
    out = []

    def fix(what, why, n):
        # Pictures are Bryan's call (a redraw spends), so these wait for him.
        return {"what": what, "why": why, "slide": n, "area": "picture", "when": "this_draft",
                "start": None, "end": None, "kind": None, "editor_can_fix": False, "rule": "", "same_rule": 0}
    shown = {p["n"]: p for p in data.get("pictures", [])}
    for n, s in enumerate(data["slides"], 1):
        if s["image"].get("source") != "concept":
            continue
        p = shown.get(n, {})
        if kind == concepts.FINISHED:
            out.append(fix(f"Use a real photo on slide {n}, not a concept",
                           "Finished homes posts show only real work, never AI concept pictures.", n))
        elif p.get("missing"):
            out.append(fix(f"Redraw slide {n}'s concept, or give it a photo",
                           f"It's text only for now: {p['missing']}."[:160], n))
    return out


def run_carousel(conn, production: dict, client=None, ref: str | None = None) -> dict:
    """Studio's read of a carousel's current slides (vision). Raises on failure."""
    import anthropic

    import carousel
    import llm
    import usage
    data = carousel.current(production)
    pngs = carousel.files(production)
    if not pngs or len(pngs) != len(data["slides"]):
        raise RuntimeError("the carousel's slides haven't been laid out")
    with tempfile.TemporaryDirectory(prefix="studio-review-") as tmp:
        content = []
        for n, jpg in enumerate(_slide_jpegs(pngs, Path(tmp)), 1):
            content.append({"type": "text", "text": f"Slide {n} of {len(pngs)}"})
            content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                        "data": base64.standard_b64encode(jpg.read_bytes()).decode()}})
    content.append({"type": "text", "text": carousel_context(production, data)})
    client = client or anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY or None)
    response = llm.final_message(
        client, model=settings.CLAUDE_MODEL, max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": CAROUSEL_SCHEMA}},
        system=_CAROUSEL_SYSTEM.replace("{content_plan}", carousel._context_text())
        .replace("{fixable}", _FIXABLE.replace("{examples}", _CAROUSEL_FIXABLE)),
        messages=[{"role": "user", "content": content}],
    )
    usage.record("review", ref or f"production:{production['id']}", response)
    if response.stop_reason in ("refusal", "max_tokens"):
        raise RuntimeError(f"Claude couldn't review these slides ({response.stop_reason})")
    review = _clean_carousel(json.loads(next(b.text for b in response.content if b.type == "text")), len(pngs))
    own = concept_fixes(production, data, carousel.post_type(conn, production))
    if own:
        review["fixes"] = (own + review["fixes"])[:MAX_FIXES]
        if not re.match(r"(?i)needs work", review["verdict"]):
            k = len(review["fixes"])
            review["verdict"] = f"Nearly ready: {k} thing{'s' if k != 1 else ''} to fix"
    return review


# ── What the draft contains ───────────────────────────────────────────────────

def _clips(conn, edl: dict) -> dict[int, dict]:
    ids = list(dict.fromkeys(c["clip_id"] for c in edl.get("cuts", [])))
    if not ids:
        return {}
    rows = conn.execute(f"SELECT * FROM clips WHERE id IN ({','.join('?' * len(ids))})", ids).fetchall()
    return {r["id"]: dict(r) for r in rows}


def draft_words(conn, production: dict) -> list[dict]:
    """Every word of the draft on its own clock (from the cut list and the clips' transcripts)."""
    import editor
    edl = json.loads(production.get("edl") or "{}")
    clips = _clips(conn, edl)
    cuts = [c for c in edl.get("cuts", []) if c["clip_id"] in clips]
    return editor.timeline(cuts, clips) if cuts else []


def _stills(video: Path, total: float, work: Path, extra: list[float] = ()) -> list[tuple[float, Path]]:
    """About six small stills across the final draft, 512 px wide, plus one at each of `extra`
    (the middle of each drawn animation no still lands in)."""
    import extract
    out = []
    times = sorted([total * (i + 0.5) / STILLS for i in range(STILLS)] + list(extra))
    for i, t in enumerate(times):
        jpg = work / f"still_{i}.jpg"
        try:
            extract._run([settings.FFMPEG, "-y", "-v", "error", "-ss", f"{t:.2f}", "-i", str(video),
                          "-frames:v", "1", "-vf", "scale=512:-2", "-q:v", "4", str(jpg)])
        except RuntimeError as e:
            log.warning("No still at %.1fs of %s: %s", t, video.name, e)
            continue
        if jpg.exists():
            out.append((round(t, 1), jpg))
    return out


def _context(conn, production: dict) -> str:
    """Everything but the stills, as text."""
    import editor
    edl = json.loads(production.get("edl") or "{}")
    plan = json.loads(production.get("plan") or "{}")
    qa = json.loads(production.get("qa") or "{}")
    music_info = json.loads(production.get("music") or "{}")
    clips = _clips(conn, edl)
    cuts = [c for c in edl.get("cuts", []) if c["clip_id"] in clips]
    total = production.get("duration") or sum(c["end"] - c["start"] for c in cuts)
    words = editor.timeline(cuts, clips) if cuts else []
    chunks = editor.caption_chunks(words, total)

    lines = [f"Post: {production['title']} ({production.get('format') or 'unknown format'}), "
             f"draft v{production['version']}, {total:.1f} seconds."]
    if production.get("idea"):
        lines.append(f"Brief:\n{production['idea']}")
    if plan.get("hook"):
        lines.append(f"Planned hook: {plan['hook']}")
    if plan.get("beats"):
        lines.append("Planned structure: " + " -> ".join(plan["beats"]))
    if plan.get("cta"):
        lines.append(f"Call to action: {plan['cta']}")
    if plan.get("storyboard"):
        lines.append("Storyboard the team planned:\n" + "\n".join(
            f"  {i}. {s['description']}" + (f" | says: {s['line']}" if s.get("line") else "")
            + (f" | on screen: {s['on_screen']}" if s.get("on_screen") else "")
            for i, s in enumerate(plan["storyboard"], 1)))

    offset, placed = 0.0, []
    for c in cuts:
        length = c["end"] - c["start"]
        placed.append(f"  {offset:.1f}-{offset + length:.1f}s: clip {c['clip_id']} {c['start']:.2f}-{c['end']:.2f}s"
                      + (f" ({c['why']})" if c.get("why") else ""))
        offset += length
    lines.append("Cut list (draft time: source clip and its seconds):\n" + ("\n".join(placed) or "  (none)"))
    lines.append(f"Hook text on screen for the first ~2.5s: {edl.get('hook_text') or '(none)'}")
    graphics = [g for g in edl.get("graphics", []) if g.get("style") != "scene"]
    lines.append("Graphics: " + ("; ".join(
        f"{g['start']:.1f}-{g['start'] + g['duration']:.1f}s {g['style']} \"{g['title']}\""
        + (f" ({' / '.join(g['lines'])})" if g.get("lines") else "") for g in graphics) or "none"))
    drawn = inserts(edl)
    if drawn:
        import motion
        lines.append("Drawn animations over the footage (drawn by Studio, not filmed; Bryan keeps talking under them):\n"
                     + "\n".join(f"  {d['start']:.1f}-{d['end']:.1f}s {d['look']} {d['type']} \"{d['title']}\""
                                 + (f" ({d['why']})" if d.get("why") else "")
                                 + f": shows \"{motion.scene_text(d['scene'])[:200]}\"" for d in drawn))
    lines.append("Captions on screen (start-end seconds: text):\n"
                 + ("\n".join(f"  {c['start']:.2f}-{c['end']:.2f}: {c['text']}" for c in chunks) or "  (none)"))
    lines.append("Words spoken in the draft (start seconds, word):\n"
                 + (" ".join(f"[{w['start']:.1f}] {w['word']}" for w in words) or "  (no speech)"))

    checks = []
    if qa.get("lufs") is not None:
        checks.append(f"loudness {qa['lufs']:.1f} LUFS (target -14)")
    if qa.get("true_peak") is not None:
        checks.append(f"true peak {qa['true_peak']:.1f} dBTP (limit -1.5)")
    if qa.get("match") is not None:
        checks.append(f"{qa['match']:.0%} of the planned words heard again after the cut")
    if qa.get("missing"):
        checks.append("possibly clipped or misheard words: " + ", ".join(qa["missing"]))
    sound = qa.get("sound") or {}
    if sound.get("voice_gain_db") is not None and sound.get("voice_lufs_recorded") is not None:
        checks.append(f"voice recorded at {sound['voice_lufs_recorded']:.0f} LUFS, levelled by "
                      f"{sound['voice_gain_db']:+.0f} dB with background noise reduced")
    if sound.get("gap_db") is not None:
        checks.append(f"while Bryan talks the music sits {sound['gap_db']:.0f} dB under his voice"
                      + (f", {sound['bed_below_db']:.0f} dB under it where nobody talks" if sound.get("bed_below_db") is not None else ""))
    did = [c["did"] if isinstance(c, dict) else str(c) for c in qa.get("checks") or []]
    if did:
        checks.append("Studio's own checks already " + "; ".join(did))
    lines.append("Checks: " + ("; ".join(checks) or "none ran"))
    music_line = f"music level before it dips under the voice: {edl.get('music', {}).get('volume_db', 'n/a')} dB"
    if music_info.get("source") == "epidemic":
        music_line += f"; track \"{music_info.get('title')}\" by {music_info.get('artist')} ({music_info.get('mood')})"
    elif music_info.get("source") == "instagram":
        music_line += "; no music in the file, Bryan adds it in Instagram"
    else:
        music_line += f"; no music this time ({music_info.get('error') or 'none found'})"
    lines.append("Music: " + music_line)
    if edl.get("missing_shots"):
        lines.append("Footage the editor wished it had:\n" + "\n".join(
            f"  - {s['shot']} ({s['why']})" for s in edl["missing_shots"]))
    if edl.get("summary"):
        lines.append(f"The editor's own summary: {edl['summary']}")
    asked = conn.execute("""SELECT body FROM feedback WHERE target = 'production' AND target_id = ?
                            AND version = ? ORDER BY id""", (production["id"], production["version"] - 1)).fetchall()
    if asked:
        lines.append("Bryan asked for these changes on the last version (done in this draft; never suggest "
                     "undoing them):\n" + "\n".join(f"  - {r['body']}" for r in asked))

    quality = []
    for cid, clip in clips.items():
        q = json.loads(clip.get("quality") or "{}")
        flags = "; ".join(f["text"] for f in q.get("flags", []))
        quality.append(f"  clip {cid} ({clip.get('title') or clip.get('source_name')}): "
                       f"{q.get('verdict', 'not checked')}" + (f": {flags}" if flags else ""))
    if quality:
        lines.append("Quality checks on the source clips:\n" + "\n".join(quality))
    return "\n\n".join(lines)


def inserts(edl: dict) -> list[dict]:
    """The draft's drawn animations (editor.place_scenes), for the read and for Review: what was
    drawn, not filmed, and when."""
    return [{"start": g["start"], "end": round(g["start"] + g["duration"], 2), "title": g.get("title", ""),
             "type": g.get("type") or (g.get("scene") or {}).get("type", ""), "look": g.get("look", "cutaway"),
             "role": g.get("role", "explain"), "why": g.get("why", ""), "scene": g.get("scene") or {}}
            for g in (edl or {}).get("graphics", []) if g.get("style") == "scene" and "start" in g]


def _overlaps(f: dict, d: dict) -> bool:
    if f.get("start") is None:
        return False
    end = f["end"] if f.get("end") is not None else f["start"] + 2.0
    return f["start"] < d["end"] + 0.5 and end > d["start"] - 0.5


def _clean(result: dict, total: float, edl: dict | None = None) -> dict:
    """Keep the answer inside the limits the schema can't enforce. A next_shoot fix with something
    to illustrate gets a companion the editor can make ("Draw it for now: ..."), so Studio's own
    fix rounds draw it while the next_shoot fix stays for Bryan; a next_shoot fix a drawn
    animation already stands in for is marked drawn. The drawn animations are listed (inserts)."""
    def secs(v):
        return None if v is None else round(min(max(float(v), 0.0), total), 2)
    import editor
    drawn = inserts(edl or {})
    room = editor.scene_cap(total) - len(drawn)  # how many more the editor may draw in this draft
    fixes, added = [], 0
    for f in result.get("fixes", [])[:MAX_FIXES]:
        f = {**f, "start": secs(f.get("start")), "end": secs(f.get("end"))}
        if f["start"] is not None and f["end"] is not None and f["end"] < f["start"]:
            f["end"] = None
        if f.get("kind") == "none" or f.get("when") != "next_shoot":
            f["kind"] = None
        draw = re.sub(r"\s+", " ", str(f.pop("illustrate", "") or "")).strip().rstrip(".")[:120]
        f = {**f, **_learnable(f)}
        if f.get("when") == "next_shoot":
            covered = next((d for d in drawn if d["role"] == "gap" and _overlaps(f, d)), None)
            if covered:
                f["drawn"] = {"start": covered["start"], "end": covered["end"], "title": covered["title"]}
        fixes.append(f)
        if draw and added < room and f.get("when") == "next_shoot" and not f.get("drawn"):
            added += 1
            fixes.append({"what": ILLUSTRATE_PREFIX + draw, "why": "Until it's filmed, a drawing can show it.",
                          "start": f["start"], "end": f["end"], "area": "picture", "when": "this_draft", "kind": None,
                          "editor_can_fix": True, "rule": "", "same_rule": 0, "illustrates": f["what"]})
    works = [w.strip() for w in result.get("works", []) if w.strip()][:MAX_WORKS]
    verdict = result.get("verdict", "").strip()
    if added and re.match(r"(?i)\s*(ready to post|nearly ready)", verdict):
        k = sum(1 for f in fixes if f.get("when") == "this_draft")
        verdict = f"Nearly ready: {k} thing{'s' if k != 1 else ''} to fix"
    return {"verdict": verdict, "scores": result.get("scores", {}),
            "fixes": fixes, "works": works,
            "inserts": [{k: v for k, v in d.items() if k != "scene"} for d in drawn]}


# ── Running it ────────────────────────────────────────────────────────────────

def run(conn, production_id: int, client=None, ref: str | None = None) -> dict:
    """Review the production's current draft and store it. Raises on failure. ref: where its
    cost is recorded (Studio's own fix rounds are counted apart)."""
    import anthropic

    import llm
    import playbook
    import usage
    row = conn.execute("SELECT * FROM productions WHERE id = ?", (production_id,)).fetchone()
    if row is not None and row["format"] == "carousel":
        review = run_carousel(conn, dict(row), client, ref)
        review["at"] = catalog.now()
        conn.execute("UPDATE productions SET review = ?, review_version = ? WHERE id = ?",
                     (json.dumps(review, ensure_ascii=False), row["version"], production_id))
        conn.commit()
        log.info("Studio's read of carousel %s v%s: %s", production_id, row["version"], review["verdict"])
        return review
    if row is None or not row["edl"] or not row["video_path"]:
        raise RuntimeError("there's no finished draft to review")
    production = dict(row)
    video = Path(production["video_path"])
    if not video.exists():
        raise RuntimeError("the draft's video file is missing")
    total = production["duration"] or 0
    text = _context(conn, production)
    edl = json.loads(production["edl"])
    grid = [total * (i + 0.5) / STILLS for i in range(STILLS)]
    extra = [round((d["start"] + d["end"]) / 2, 2) for d in inserts(edl)
             if not any(d["start"] <= t <= d["end"] for t in grid)]
    with tempfile.TemporaryDirectory(prefix="studio-review-") as tmp:
        content = []
        for t, jpg in _stills(video, total, Path(tmp), extra):
            content.append({"type": "text", "text": f"Still at {t:.1f}s"})
            content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                        "data": base64.standard_b64encode(jpg.read_bytes()).decode()}})
    content.append({"type": "text", "text": text})
    client = client or anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY or None)
    response = llm.final_message(
        client, model=settings.CLAUDE_MODEL, max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
        system=_SYSTEM.replace("{playbook}", playbook.text())
        .replace("{fixable}", _FIXABLE.replace("{examples}", _REEL_FIXABLE))
        .replace("{illustrate}", _ILLUSTRATE if settings.EDIT_SCENES and settings.EDIT_SCENES_MAX > 0 else _NO_ILLUSTRATE),
        messages=[{"role": "user", "content": content}],
    )
    usage.record("review", ref or f"production:{production_id}", response)
    if response.stop_reason in ("refusal", "max_tokens"):
        raise RuntimeError(f"Claude couldn't review this draft ({response.stop_reason})")
    review = _clean(json.loads(next(b.text for b in response.content if b.type == "text")), total, edl)
    review["at"] = catalog.now()
    conn.execute("UPDATE productions SET review = ?, review_version = ? WHERE id = ?",
                 (json.dumps(review, ensure_ascii=False), production["version"], production_id))
    conn.commit()
    log.info("Studio's read of production %s v%s: %s", production_id, production["version"], review["verdict"])
    return review


# Reviews running now (from the editor, a backfill or the dashboard), and the last error.
running: set[int] = set()
errors: dict[int, str] = {}
_lock = threading.Lock()


def safe_run(conn, production_id: int, client=None, ref: str | None = None) -> dict | None:
    """run(), but a failure is logged and remembered instead of raised."""
    with _lock:
        if production_id in running:
            return None
        running.add(production_id)
    try:
        review = run(conn, production_id, client, ref)
        errors.pop(production_id, None)
        return review
    except Exception as e:  # a draft without a review beats no draft
        log.warning("Studio's read failed for production %s: %s: %s", production_id, type(e).__name__, e)
        errors[production_id] = f"{type(e).__name__}: {e}"[:300]
        return None
    finally:
        with _lock:
            running.discard(production_id)


def start(production_id: int) -> bool:
    """Review in the background (the dashboard's "Get Studio's read"). False if one is running."""
    if production_id in running:
        return False

    def go():
        conn = catalog.connect()
        try:
            safe_run(conn, production_id)
        finally:
            conn.close()
    threading.Thread(target=go, name="review", daemon=True).start()
    return True


def busy() -> bool:
    return bool(running)


def current(row) -> dict | None:
    """The stored review, if it describes the draft's current version."""
    if not row["review"] or row["review_version"] != row["version"]:
        return None
    try:
        return json.loads(row["review"])
    except ValueError:
        return None


def state(row) -> str:
    """ready | running | failed | none"""
    if current(row) is not None:
        return "ready"
    if row["id"] in running:
        return "running"
    return "failed" if row["id"] in errors else "none"


def backfill(limit: int = 10, client=None) -> int:
    """Drafts waiting for review with no read of their current version (at most `limit`,
    one at a time). Run once when the worker starts. Never raises."""
    done = 0
    try:
        conn = catalog.connect()
    except Exception:
        log.exception("Review backfill couldn't open the catalogue")
        return 0
    try:
        rows = conn.execute("""SELECT id FROM productions WHERE status = 'ready'
                                   AND ((edl IS NOT NULL AND video_path IS NOT NULL)
                                        OR (format = 'carousel' AND slides IS NOT NULL))
                                   AND (review IS NULL OR review_version IS NULL OR review_version <> version)
                               ORDER BY updated_at DESC LIMIT ?""", (limit,)).fetchall()
        if rows:
            log.info("Writing Studio's read for %d draft(s) waiting for review", len(rows))
        for r in rows:
            if safe_run(conn, r["id"], client) is not None:
                done += 1
    except Exception:
        log.exception("Review backfill stopped")
    finally:
        conn.close()
    return done
