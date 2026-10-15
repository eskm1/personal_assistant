"""
Post ideas from the library (phase 4). Claude reads every usable clip (summary,
timestamped transcript, best moments), the brand descriptions and the playbook,
and proposes posts built only from footage that exists, each with the exact clip
segments to use. Ideas land on the dashboard's Ideas tab; approving one turns it
into a planned production for the editor.
"""
import json
import logging
import uuid
from pathlib import Path

import anthropic

import playbook
from settings import ANTHROPIC_API_KEY, BRANDS_FILE, CLAUDE_MODEL

log = logging.getLogger("studio.ideas")

# Newest clips first until the listing reaches this size (~75k tokens), so a big
# library can't blow up the cost of one "Suggest posts" press.
LIBRARY_CHAR_BUDGET = 300_000

SCHEMA = {
    "type": "object",
    "properties": {
        "ideas": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "brand": {"type": "string", "enum": ["personal", "urban_makers"]},
                    "format": {"type": "string", "enum": ["talking_head", "day_in_the_life", "compilation"]},
                    "title": {"type": "string"},
                    "hook": {"type": "string"},
                    "angle": {"type": "string"},
                    "beats": {"type": "array", "items": {"type": "string"}},
                    "segments": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "clip_id": {"type": "integer"},
                                "start": {"type": "number"},
                                "end": {"type": "number"},
                                "purpose": {"type": "string"},
                            },
                            "required": ["clip_id", "start", "end", "purpose"],
                            "additionalProperties": False,
                        },
                    },
                    "caption": {"type": "string"},
                    "est_seconds": {"type": "number"},
                    "wishlist": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "shot": {"type": "string"},
                                "why": {"type": "string"},
                            },
                            "required": ["shot", "why"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["brand", "format", "title", "hook", "angle", "beats", "segments", "caption",
                             "est_seconds", "wishlist"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["ideas"],
    "additionalProperties": False,
}

_SYSTEM = """You plan short-form vertical posts (Instagram Reels) for Bryan from footage he has \
already shot. The library below is everything available; the editor will cut each idea from \
exactly the segments you list.

{brands}

Follow this playbook, which holds Bryan's editing and story rules:

<playbook>
{playbook}
</playbook>

How to plan:
- Propose up to {count} ideas, spread across both accounts when the footage supports it. \
Fewer, stronger ideas beat stretching thin footage; return none for an account with nothing worth posting.
- Every idea is a complete post of about 15 to 60 seconds with one clear point.
- hook: the opening line or visual for the first 3 seconds, quoted from the transcript when possible.
- angle: one or two sentences on why a viewer would stay to the end.
- beats: the post's structure in order (hook, context, proof, payoff, call to action).
- segments: the exact pieces of footage, in playback order, by clip id with start and end \
seconds inside that clip. Use the transcript timestamps so speech starts and ends on whole \
phrases. A clip may appear in several ideas.
- caption: a ready-to-edit Instagram caption in Bryan's voice with 3 to 6 relevant hashtags.
- est_seconds: the finished length.
- wishlist: up to 4 specific shots Bryan could still film that would make this post noticeably \
better, each with why (e.g. "5-second close-up of the membrane being rolled onto the wall corner: \
shows the step the post is about instead of only describing it"). Think about what's missing: a \
strong opening visual, a before or after shot, close-ups of the work being explained, a clean \
retake of a fumbled line, a call to action. Empty if the footage already has everything.
- Use only what the footage shows and Bryan says. Never invent results, prices, clients or claims.
- Don't repeat posts that already exist (listed below)."""


def _segments_text(clip_dir: Path, fallback: str | None) -> str:
    """Timestamped transcript lines from the clip's sidecar, else the plain text."""
    path = clip_dir / "transcript.json"
    if path.exists():
        segments = json.loads(path.read_text(encoding="utf-8")).get("segments", [])
        if segments:
            return "\n".join(f"    [{s['start']:.1f}-{s['end']:.1f}] {s['text']}" for s in segments)
    return f"    {fallback}" if fallback else "    (no speech)"


def library_text(conn) -> tuple[str, int, list[int]]:
    """The library as the prompt sees it: (text, clips omitted for budget, ids included)."""
    rows = conn.execute(
        """SELECT * FROM clips WHERE status = 'tagged' AND COALESCE(usable, 1) = 1
           ORDER BY COALESCE(recorded_at, ingested_at) DESC""").fetchall()
    parts, used, ids = [], 0, []
    for r in rows:
        moments = json.loads(r["moments"] or "[]")
        block = "\n".join([
            f"Clip {r['id']}: {r['title']}",
            f"  account: {r['brand']} | format: {r['format']} | {r['duration'] or 0:.1f}s | "
            f"{r['orientation']} | recorded {r['recorded_at'] or 'unknown'}",
            f"  summary: {r['summary']}",
            f"  suggested hook: {r['suggested_hook'] or '-'}",
            "  best moments: " + ("; ".join(f"{m['start']:.1f}-{m['end']:.1f}s {m['kind']}: {m['description']}"
                                            for m in moments) or "-"),
            f"  quality: {r['quality_notes'] or '-'}",
            "  transcript:",
            _segments_text(Path(r["clip_dir"]), r["transcript"]),
        ])
        if used + len(block) > LIBRARY_CHAR_BUDGET and parts:
            break
        parts.append(block)
        used += len(block)
        ids.append(r["id"])
    return "\n\n".join(parts), len(rows) - len(ids), ids


def _clean(ideas: list[dict], durations: dict[int, float]) -> list[dict]:
    """Keep only segments that point at real clips and real times."""
    kept = []
    for idea in ideas:
        segments = []
        for seg in idea["segments"]:
            duration = durations.get(seg["clip_id"])
            if duration is None:
                continue
            start, end = max(0.0, seg["start"]), min(duration, seg["end"])
            if end - start >= 0.3:
                segments.append({**seg, "start": round(start, 2), "end": round(end, 2)})
        if segments:
            kept.append({**idea, "segments": segments})
        else:
            log.warning("Dropped idea %r: none of its segments match the library", idea["title"])
    return kept


def generate(conn, focus: str = "", count: int = 6, client: anthropic.Anthropic | None = None) -> tuple[str, int]:
    """Ask Claude for ideas and store them. Returns (batch id, ideas saved)."""
    import catalog

    library, omitted, ids = library_text(conn)
    if not ids:
        raise RuntimeError("The library has no tagged, usable clips yet")
    existing = [r["title"] for r in conn.execute("SELECT title FROM productions")]
    brands = Path(BRANDS_FILE).read_text(encoding="utf-8").strip() if Path(BRANDS_FILE).exists() else ""
    user = (
        (f"Focus for this batch: {focus}\n\n" if focus.strip() else "")
        + "Existing posts: " + ("; ".join(existing) if existing else "none yet") + "\n\n"
        + f"Library ({len(ids)} clips"
        + (f"; {omitted} older clips left out to keep this request small" if omitted else "") + "):\n\n"
        + library
    )
    client = client or anthropic.Anthropic(api_key=ANTHROPIC_API_KEY or None)
    batch = uuid.uuid4().hex[:8]
    import llm
    response = llm.final_message(
        client,
        model=CLAUDE_MODEL,
        max_tokens=32000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        # Planning is judgment-heavy and runs only when Bryan asks, so think harder than tagging.
        output_config={"effort": "high", "format": {"type": "json_schema", "schema": SCHEMA}},
        system=_SYSTEM.replace("{brands}", brands).replace("{playbook}", playbook.text())
                      .replace("{count}", str(count)),
        messages=[{"role": "user", "content": user}],
    )
    import usage
    usage.record("ideas", f"ideas:{batch}", response)
    if response.stop_reason == "refusal":
        raise RuntimeError("Claude declined to suggest ideas")
    if response.stop_reason == "max_tokens":
        raise RuntimeError("Claude's answer was cut off (max_tokens)")
    text = next((b.text for b in response.content if b.type == "text"), "")
    durations = {r["id"]: r["duration"] or 0 for r in conn.execute("SELECT id, duration FROM clips")}
    ideas = _clean(json.loads(text)["ideas"], durations)
    catalog.save_ideas(conn, batch, ideas)
    log.info("Saved %d ideas (batch %s) from %d clips", len(ideas), batch, len(ids))
    return batch, len(ideas)
