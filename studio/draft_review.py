"""
Studio's read of a draft: after the editor renders a draft, Claude watches it (a few
stills of the final video, the captions and words on its own clock, the cut list and
the checks) and writes a short review for Bryan: a one-line verdict, a good/fix score
for hook, pace, sound, captions and picture, the few changes that matter most (each
pinned to a moment), and what already works.

One Claude call per draft version, a few cents. A failed review never fails the draft:
it's logged, the draft stays ready, and the dashboard offers "Get Studio's read".
Stored as productions.review (JSON) with review_version, the version it describes.
"""
import base64
import json
import logging
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
                },
                "required": ["what", "why", "start", "end", "area", "when", "kind"],
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
- works: 2 to 4 short points on what already works, so it's kept in the next version.
- Place names stay internal: if an estate, condo, street or building name, an address or a \
client's name appears in the on-screen text, captions or hook, that is always a this_draft fix \
(area captions) to remove it.
- Only say what the material shows. Never invent facts, numbers, times or problems; if you're \
unsure something is wrong, leave it out. Use the timestamps given, don't guess new ones.
- Plain words, no jargon (no "LUFS", "dB", "EDL", "B-roll" or "SNR"). Short sentences."""


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


def _stills(video: Path, total: float, work: Path) -> list[tuple[float, Path]]:
    """About six small stills across the final draft, 512 px wide."""
    import extract
    out = []
    for i in range(STILLS):
        t = total * (i + 0.5) / STILLS
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
    graphics = edl.get("graphics", [])
    lines.append("Graphics: " + ("; ".join(
        f"{g['start']:.1f}-{g['start'] + g['duration']:.1f}s {g['style']} \"{g['title']}\""
        + (f" ({' / '.join(g['lines'])})" if g.get("lines") else "") for g in graphics) or "none"))
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

    quality = []
    for cid, clip in clips.items():
        q = json.loads(clip.get("quality") or "{}")
        flags = "; ".join(f["text"] for f in q.get("flags", []))
        quality.append(f"  clip {cid} ({clip.get('title') or clip.get('source_name')}): "
                       f"{q.get('verdict', 'not checked')}" + (f": {flags}" if flags else ""))
    if quality:
        lines.append("Quality checks on the source clips:\n" + "\n".join(quality))
    return "\n\n".join(lines)


def _clean(result: dict, total: float) -> dict:
    """Keep the answer inside the limits the schema can't enforce."""
    def secs(v):
        return None if v is None else round(min(max(float(v), 0.0), total), 2)
    fixes = []
    for f in result.get("fixes", [])[:MAX_FIXES]:
        f = {**f, "start": secs(f.get("start")), "end": secs(f.get("end"))}
        if f["start"] is not None and f["end"] is not None and f["end"] < f["start"]:
            f["end"] = None
        if f.get("kind") == "none" or f.get("when") != "next_shoot":
            f["kind"] = None
        fixes.append(f)
    works = [w.strip() for w in result.get("works", []) if w.strip()][:MAX_WORKS]
    return {"verdict": result.get("verdict", "").strip(), "scores": result.get("scores", {}),
            "fixes": fixes, "works": works}


# ── Running it ────────────────────────────────────────────────────────────────

def run(conn, production_id: int, client=None) -> dict:
    """Review the production's current draft and store it. Raises on failure."""
    import anthropic

    import llm
    import playbook
    import usage
    row = conn.execute("SELECT * FROM productions WHERE id = ?", (production_id,)).fetchone()
    if row is None or not row["edl"] or not row["video_path"]:
        raise RuntimeError("there's no finished draft to review")
    production = dict(row)
    video = Path(production["video_path"])
    if not video.exists():
        raise RuntimeError("the draft's video file is missing")
    total = production["duration"] or 0
    text = _context(conn, production)
    with tempfile.TemporaryDirectory(prefix="studio-review-") as tmp:
        content = []
        for t, jpg in _stills(video, total, Path(tmp)):
            content.append({"type": "text", "text": f"Still at {t:.1f}s"})
            content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                        "data": base64.standard_b64encode(jpg.read_bytes()).decode()}})
    content.append({"type": "text", "text": text})
    client = client or anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY or None)
    response = llm.final_message(
        client, model=settings.CLAUDE_MODEL, max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
        system=_SYSTEM.replace("{playbook}", playbook.text()),
        messages=[{"role": "user", "content": content}],
    )
    usage.record("review", f"production:{production_id}", response)
    if response.stop_reason in ("refusal", "max_tokens"):
        raise RuntimeError(f"Claude couldn't review this draft ({response.stop_reason})")
    review = _clean(json.loads(next(b.text for b in response.content if b.type == "text")), total)
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


def safe_run(conn, production_id: int, client=None) -> dict | None:
    """run(), but a failure is logged and remembered instead of raised."""
    with _lock:
        if production_id in running:
            return None
        running.add(production_id)
    try:
        review = run(conn, production_id, client)
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
        rows = conn.execute("""SELECT id FROM productions WHERE status = 'ready' AND edl IS NOT NULL
                                   AND video_path IS NOT NULL
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
