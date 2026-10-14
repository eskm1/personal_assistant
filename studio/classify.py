"""
Claude looks at a clip — sampled frames plus the timestamped transcript — and
tags it: which brand it belongs to, what format it is, what it's about, and the
moments worth cutting to.

The answer comes back as JSON constrained to SCHEMA (structured outputs), so the
catalogue never has to parse free text. Brand definitions live in brands.md so
Bryan can tune them without touching code.
"""
import base64
import json
import logging
from pathlib import Path

import anthropic

from settings import ANTHROPIC_API_KEY, BRANDS_FILE, CLAUDE_EFFORT, CLAUDE_MODEL

log = logging.getLogger("studio.classify")

BRANDS = ["personal", "urban_makers", "unsure"]
FORMATS = ["talking_head", "day_in_the_life", "compilation", "other"]
MOMENT_KINDS = ["hook", "key_point", "quote", "visual", "reaction"]

SCHEMA = {
    "type": "object",
    "properties": {
        "brand": {"type": "string", "enum": BRANDS},
        "brand_reason": {"type": "string"},
        "format": {"type": "string", "enum": FORMATS},
        "title": {"type": "string"},
        "summary": {"type": "string"},
        "topics": {"type": "array", "items": {"type": "string"}},
        "setting": {"type": "string"},
        "usable": {"type": "boolean"},
        "quality_notes": {"type": "string"},
        "suggested_hook": {"type": "string"},
        "moments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "start": {"type": "number"},
                    "end": {"type": "number"},
                    "kind": {"type": "string", "enum": MOMENT_KINDS},
                    "description": {"type": "string"},
                },
                "required": ["start", "end", "kind", "description"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["brand", "brand_reason", "format", "title", "summary", "topics", "setting",
                 "usable", "quality_notes", "suggested_hook", "moments"],
    "additionalProperties": False,
}

_SYSTEM = """You catalogue raw phone footage for Bryan, who posts to two Instagram accounts. \
Each clip you see is unedited. Your tags decide which account it is filed under and \
which moments get cut into posts later, so be concrete and use real timestamps.

{brands}

Formats:
- talking_head: someone speaking to camera; the words carry the clip.
- day_in_the_life: a slice of a day — moving between places or tasks, light narration or none.
- compilation: short visual footage with little or no speech, best used inside montages \
and compilations (process shots, before/after, scenery, details).
- other: none of the above.

Field guidance:
- brand: "unsure" when the clip could reasonably go either way; say why in brand_reason.
- title: 3-8 words, specific enough to find the clip again ("Kitchen tiling day 2 progress").
- topics: 2-6 short lowercase tags.
- usable: false only if the clip is unusable (accidental recording, black screen, \
unwatchable shake, inaudible speech that matters).
- quality_notes: lighting, audio, stability, framing — one line; "fine" if nothing stands out.
- suggested_hook: the strongest opening line or visual for a Reel, quoted from the \
transcript when possible.
- moments: up to 6 segments worth cutting to, with start/end in seconds within the \
clip's duration, taken from the transcript timestamps or frame times. Empty if none."""


def _brands_text() -> str:
    path = Path(BRANDS_FILE)
    return path.read_text(encoding="utf-8").strip() if path.exists() else ""


def _image_block(jpg: Path) -> dict:
    data = base64.standard_b64encode(jpg.read_bytes()).decode("ascii")
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": data}}


def _transcript_text(transcript: dict | None) -> str:
    if not transcript or not transcript.get("segments"):
        return "(no speech detected)"
    lines = [f"[{s['start']:.1f}-{s['end']:.1f}s] {s['text']}" for s in transcript["segments"]]
    return f"Language: {transcript.get('language')}\n" + "\n".join(lines)


def build_content(meta: dict, frames: list[tuple[float, Path]], transcript: dict | None,
                  source_name: str) -> list[dict]:
    content: list[dict] = [{
        "type": "text",
        "text": (f"Clip: {source_name}\nDuration: {meta['duration']:.1f}s, {meta['orientation']} "
                 f"({meta['width']}x{meta['height']}), recorded {meta.get('recorded_at') or 'unknown'}.\n"
                 f"{len(frames)} frames follow, each labelled with its time in the clip."),
    }]
    for t, jpg in frames:
        content.append({"type": "text", "text": f"Frame at {t:.1f}s"})
        content.append(_image_block(jpg))
    content.append({"type": "text", "text": "Transcript:\n" + _transcript_text(transcript)})
    return content


def classify(meta: dict, frames: list[tuple[float, Path]], transcript: dict | None,
             source_name: str, client: anthropic.Anthropic | None = None) -> dict:
    client = client or anthropic.Anthropic(api_key=ANTHROPIC_API_KEY or None)
    response = client.beta.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=16000,
        # If a safety classifier declines, the API reruns the request on
        # Anthropic's recommended fallback model instead of returning a refusal.
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={
            "effort": CLAUDE_EFFORT,
            "format": {"type": "json_schema", "schema": SCHEMA},
        },
        system=_SYSTEM.replace("{brands}", _brands_text()),  # not .format: brands.md may hold braces
        messages=[{"role": "user", "content": build_content(meta, frames, transcript, source_name)}],
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("Claude declined to classify this clip")
    if response.stop_reason == "max_tokens":
        raise RuntimeError("Claude's answer was cut off (max_tokens)")
    text = next((b.text for b in response.content if b.type == "text"), "")
    result = json.loads(text)
    log.info("Classified %s as %s / %s (%s)", source_name, result["brand"], result["format"],
             response.model)
    return result
