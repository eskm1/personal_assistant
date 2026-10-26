"""
YouTube references (cloud/CONTRACT.md, "Reference guides"): YouTube blocks downloads
from servers, so a YouTube link (youtube.com/watch, youtu.be, youtube.com/shorts) is not
downloaded. Gemini watches it from the link instead (generateContent with the video as
file_data, file_uri = the link; Studio's GEMINI_API_KEY, sent only to GEMINI_URL in a
header, never logged) and answers in the shape style_study keeps for a reel: the beat
timeline, the structure read (arc), the gaps, three observations, Claude's usual look
(layouts, captions, hook, B-roll, design) and the numbers, estimated by watching rather
than measured (measured.estimated_by: the style guide and the taste report keep them out
of their medians).

One call per reference, priced at STUDIO_REFERENCE_VIDEO_COST_USD (an estimate) under
usage kind 'style', so it counts toward the reference budget. Only the first
STUDIO_REFERENCE_VIDEO_MAX_SECONDS are watched. A refusal (Gemini's filter, a private or
removed video) fails the reference for good with a plain reason; no key, a refused key or
an unknown model fail it with what to do. YouTube's own thumbnail is the one still, and the
channel (from YouTube's oEmbed) the account; both are nice to have and never fail it.
"""
import json
import logging
import re
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests

import settings

log = logging.getLogger("studio.youtube")

TIMEOUT = 300                 # seconds: watching a video takes Gemini a while
BACKOFF = 10.0                # before the one retry when Gemini is busy or down
OEMBED_URL = "https://www.youtube.com/oembed"
THUMB_URL = "https://i.ytimg.com/vi/{id}/hqdefault.jpg"
_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_HOSTS = ("youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be", "www.youtu.be")
_REFUSED = {"SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "RECITATION", "IMAGE_SAFETY"}


def video_id(url: str) -> str | None:
    """The video's id from a watch, youtu.be, shorts, live or embed link; None if it isn't one."""
    try:
        u = urlparse(str(url or "").strip())
    except ValueError:
        return None
    host = (u.hostname or "").lower()
    if u.scheme not in ("http", "https") or host not in _HOSTS:
        return None
    parts = [p for p in u.path.split("/") if p]
    if host.endswith("youtu.be"):
        vid = parts[0] if parts else ""
    elif parts[:1] == ["watch"]:
        vid = (parse_qs(u.query).get("v") or [""])[0]
    elif len(parts) >= 2 and parts[0] in ("shorts", "live", "embed"):
        vid = parts[1]
    else:
        return None
    return vid if _ID.match(vid) else None


def is_youtube(url: str) -> bool:
    return video_id(url) is not None


def canonical(url: str) -> str:
    """The link as Gemini's docs give it (https://www.youtube.com/watch?v=<id>)."""
    return f"https://www.youtube.com/watch?v={video_id(url)}"


# ── What Gemini answers ───────────────────────────────────────────────────────

def _schema() -> dict:
    """The answer's shape, in JSON Schema (the same pieces as Claude's reel read): Claude's look,
    the beats as "t | on screen | said | changed" strings (no transcript of our own here; read by
    style_study.read_parts), the numbers and the transcript. Kept small like Claude's: Gemini refuses
    a schema with too many states."""
    import style_study
    look = style_study.LOOK_SCHEMA
    props = dict(look["properties"])
    num = {"anyOf": [{"type": "number"}, {"type": "null"}]}
    props["numbers"] = {"type": "object", "properties": {
        "duration": {"type": "number"}, "cuts": {"type": "integer"}, "first_cut": num, "shot_median": num,
        "words": {"type": "integer"}, "speech_start": num, "speech_end": num},
        "required": ["duration", "cuts", "first_cut", "shot_median", "words", "speech_start", "speech_end"],
        "additionalProperties": False}
    props["transcript"] = {"type": "string"}
    return {"type": "object", "properties": props, "required": list(look["required"]) + ["numbers", "transcript"],
            "additionalProperties": False}


def gemini_schema(schema: dict) -> dict:
    """JSON Schema as Gemini's responseSchema takes it (its OpenAPI subset): types in capitals,
    a null choice as nullable, no additionalProperties, the properties kept in order."""
    if not isinstance(schema, dict):
        return schema
    alts = schema.get("anyOf")
    if alts:
        real = [a for a in alts if a.get("type") != "null"]
        out = gemini_schema(real[0]) if real else {"type": "STRING"}
        if len(real) < len(alts):
            out["nullable"] = True
        return out
    out = {}
    for k, v in schema.items():
        if k == "type":
            out["type"] = str(v).upper()
        elif k == "properties":
            out["properties"] = {name: gemini_schema(p) for name, p in v.items()}
            out["propertyOrdering"] = list(v)
        elif k == "items":
            out["items"] = gemini_schema(v)
        elif k in ("required", "enum", "description"):
            out[k] = list(v) if isinstance(v, (list, tuple)) else v
    return out


_SYSTEM = """You watch a short video (a YouTube Short or video) that an Instagram team saved as a \
reference for their Reels (Urban Makers, a Singapore renovation company), and describe how it is made, \
precisely. Report only what you see or hear; say "unclear" rather than guess.

Go through the video as a timeline before concluding anything:
- beats: 8 to 16, in time order, one each time the picture or the point changes, each the string \
"t | on screen | said | changed": t (seconds), what is on screen (under 15 words), the words spoken from then \
to the next beat (quoted; empty when nothing is said) and what changed since the beat before (under 10 words; \
"first beat" first).
- layouts: one per beat, in order (talking_full = speaker fills the frame; punch_in = a tighter crop of the \
speaker; split_broll_top = footage above, speaker below; broll_full; screen; text_card; before_after).
- captions: words per on-screen chunk (e.g. "1-3"), case, font look, accent colours and what they mark, \
box/outline/shadow, position. hook: what the first 3 seconds say and show. first_frame: the very first frame.
- broll: kinds of cutaway footage. graphics: motion graphics, cards, arrows, counters (or "none"). cta: how \
it ends and asks for something. standout: the one editing choice that makes it work.
- design: palette (names, hex where clear), materials, lighting, camera, framing, shot_types, text_style and \
closest_free_fonts (Google Fonts / SIL Open Font License).
- arc: the structure, read from the beats, each part the string "pattern | at | basis | what". open: how the \
first 3 seconds stop the scroll ({opens}). hold: how it keeps attention through the middle, one or more \
devices split by commas and at "-" ({holds}). turn: the moment it shifts, the reveal, the answer, the after \
({turns}; "none | - | inferred | never turns" when it never turns). close: how it ends ({closes}). at: the \
time in seconds. basis: seen, heard, both, or inferred (your conclusion beyond what is shown or said: mark it so).
- gaps: what you could have missed (you see about one frame a second, so quick cuts and flashes can slip \
by; speech you couldn't make out; parts not shown to you). Each short.
- observations: the three highest-signal observations about why it works or doesn't, most important first, \
each the string "at | basis | observation".
- numbers (estimates from watching, not measurements): duration in seconds of what you watched, cuts \
(hard cuts counted), first_cut (seconds, null if none), shot_median (a typical shot's seconds), words \
(spoken), speech_start and speech_end (seconds, null if no speech).
- transcript: every spoken word, in order (under 600 words)."""


def _system() -> str:
    import style_study
    return _SYSTEM.format(opens=", ".join(style_study.OPEN_PATTERNS), holds=", ".join(style_study.HOLD_DEVICES),
                          turns=", ".join(style_study.TURN_PATTERNS), closes=", ".join(style_study.CLOSE_PATTERNS))


class _Retry(Exception):
    def __init__(self, failure, wait: float | None = None):
        super().__init__(str(failure))
        self.failure, self.wait = failure, wait


def _failed(message: str, problem: str = "other"):
    import style_study
    key = settings.GEMINI_API_KEY
    if key:
        message = message.replace(key, "***")
    return style_study.DownloadFailed(message, problem)


def _wait_of(r) -> float | None:
    try:
        if r.headers.get("Retry-After"):
            return min(float(r.headers["Retry-After"]), 60.0)
    except (TypeError, ValueError):
        pass
    return None


def _error_text(r) -> str:
    try:
        err = r.json().get("error") or {}
        return f"{err.get('status') or ''} {err.get('message') or ''}".strip()[:300]
    except (ValueError, AttributeError):
        return ""


def _answer(r, model: str) -> tuple[dict, dict]:
    """One generateContent answer: (the parsed JSON, usageMetadata). Raises DownloadFailed with a
    plain reason, or _Retry for a busy or broken Gemini (one more try)."""
    text = _error_text(r) if r.status_code != 200 else ""
    low = text.lower()
    if r.status_code == 429:
        if "limit: 0" in low or "free_tier" in low or "freetier" in low:
            raise _failed("Gemini's free plan can't watch videos for Studio: turn on billing for the key in "
                          "Google AI Studio")
        if "per_day" in low or "perday" in low:
            raise _failed("Today's Gemini limit for this key is used up: the reference is tried again tomorrow")
        raise _Retry(_failed("Gemini is busy (too many requests): the reference is tried again later"), _wait_of(r))
    if r.status_code >= 500:
        raise _Retry(_failed(f"Gemini had a problem ({r.status_code}): the reference is tried again later"),
                     _wait_of(r))
    # How Gemini words a video it can't open isn't documented: these words, never a schema or key problem.
    video_words = ("private", "unavailable", "not public", "fetch", "youtube", "cannot access", "not accessible")
    if r.status_code in (400, 403) and any(w in low for w in video_words) and "api key" not in low \
            and "api_key" not in low:
        raise _failed("Gemini couldn't watch this video (private, age-restricted, removed or not public?): "
                      f"{text[:150]}", "gone")
    if r.status_code in (401, 403) or "api key" in low or "api_key" in low:
        raise _failed("Gemini refused the key (GEMINI_API_KEY): check it in Google AI Studio, with billing on")
    if r.status_code == 404:
        raise _failed(f"Gemini doesn't know the model {model} (STUDIO_REFERENCE_VIDEO_MODEL): set one that takes "
                      "video, from Google AI Studio's model list")
    if r.status_code != 200:
        raise _failed(f"Gemini refused the request ({r.status_code}): {text[:200]}")
    try:
        data = r.json()
    except ValueError:
        raise _Retry(_failed("Gemini sent something Studio can't read"))
    meta = data.get("usageMetadata") if isinstance(data.get("usageMetadata"), dict) else {}
    block = (data.get("promptFeedback") or {}).get("blockReason")
    if block:
        raise _failed(f"Gemini wouldn't describe this video (its filter: {block})", "gone")
    cands = data.get("candidates") or []
    reason = (cands[0].get("finishReason") if cands else None) or "no answer"
    parts = ((cands[0].get("content") or {}).get("parts") or []) if cands else []
    raw = "".join(p.get("text") or "" for p in parts if isinstance(p, dict) and not p.get("thought"))
    if reason in _REFUSED:
        raise _failed(f"Gemini wouldn't describe this video ({reason})", "gone")
    if reason == "MAX_TOKENS":
        raise _failed("Gemini's answer about this video was cut off (too long)")
    try:
        got = json.loads(raw)
    except ValueError:
        raise _Retry(_failed(f"Gemini's answer about this video couldn't be read ({reason})"))
    if not isinstance(got, dict):
        raise _Retry(_failed("Gemini's answer about this video couldn't be read"))
    return got, meta


def watch(url: str, ref: str) -> dict:
    """Gemini's read of a YouTube video (the answer's JSON). One call (one more only when Gemini
    is busy, down or answers nonsense), its cost recorded under usage kind 'style'. Raises
    DownloadFailed with a plain reason."""
    import usage
    if not settings.GEMINI_API_KEY:
        raise _failed("YouTube references are watched by Gemini, and the Studio machine has no Gemini key "
                      "(GEMINI_API_KEY; on the server: setup.sh key gemini). Or save the video and drop it in "
                      "OneDrive: Studio Inbox > References > Inspiration")
    model = settings.REFERENCE_VIDEO_MODEL
    endpoint = f"{settings.GEMINI_URL}/models/{model}:generateContent"
    video = {"file_data": {"file_uri": canonical(url)},
             "video_metadata": {"end_offset": f"{settings.REFERENCE_VIDEO_MAX_SECONDS}s"}}
    body = {"systemInstruction": {"parts": [{"text": _system()}]},
            "contents": [{"role": "user", "parts": [video, {"text": "Describe this reference video."}]}],
            "generationConfig": {"responseMimeType": "application/json", "responseSchema": gemini_schema(_schema()),
                                 "maxOutputTokens": 16384}}
    headers = {"x-goog-api-key": settings.GEMINI_API_KEY, "Content-Type": "application/json"}
    err = None
    for attempt in (1, 2):
        try:
            r = requests.post(endpoint, json=body, headers=headers, timeout=TIMEOUT)
        except requests.RequestException as e:
            err = _Retry(_failed(f"couldn't reach Gemini ({type(e).__name__})", "net"))
        else:
            if r.status_code == 200:  # an answer is billed, whatever it says
                try:
                    meta = r.json().get("usageMetadata")
                except (ValueError, AttributeError):
                    meta = None
                usage.record_gemini("style", ref, model, meta, settings.REFERENCE_VIDEO_COST_USD)
            if r.status_code == 400 and "offset" in _error_text(r).lower() and "video_metadata" in video:
                video.pop("video_metadata")  # a model that won't clip: watch it whole, once
                continue
            try:
                return _answer(r, model)[0]
            except _Retry as e:
                err = e
        if attempt == 2:
            raise err.failure
        log.info("Gemini (YouTube reference %s): %s; trying once more", ref, err)
        time.sleep(err.wait if err.wait is not None else BACKOFF)
    raise err.failure if err else _failed("Gemini didn't answer")


# ── Into an analysis ──────────────────────────────────────────────────────────

def _oembed(url: str) -> dict:
    """YouTube's public oEmbed (title, channel, thumbnail), or {}."""
    try:
        r = requests.get(OEMBED_URL, params={"url": canonical(url), "format": "json"}, timeout=(10, 20))
        data = r.json() if r.status_code == 200 else {}
        return data if isinstance(data, dict) else {}
    except (requests.RequestException, ValueError):
        return {}


def _still(url: str, info: dict, stills_dir: Path) -> list[str]:
    """YouTube's thumbnail as the one still (540 px JPEG), or none."""
    import shutil

    import style_study
    if stills_dir.exists():
        shutil.rmtree(stills_dir, ignore_errors=True)
    stills_dir.mkdir(parents=True, exist_ok=True)
    src = info.get("thumbnail_url") if str(info.get("thumbnail_url") or "").startswith(("https://", "http://")) \
        else THUMB_URL.format(id=video_id(url))
    got = style_study._fetch_image(src, stills_dir / "thumb.jpg")
    if not got:
        return []
    frames = style_study.slide_stills([got], stills_dir)
    got.unlink(missing_ok=True)
    return [str(p) for _, p in frames]


def _account(info: dict) -> str:
    m = re.search(r"/@([A-Za-z0-9._-]{1,100})", str(info.get("author_url") or ""))
    return (m.group(1) if m else str(info.get("author_name") or ""))[:100]


def _num(v):
    return round(float(v), 2) if isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and v >= 0 else None


def analyse(url: str, stills_dir: Path, ref: str) -> dict:
    """A YouTube reference analysed by Gemini, in style_study.analyse()'s shape: {kind: video,
    measured (estimated, measured.estimated_by), look (with beats, arc, gaps, observations),
    stills (the thumbnail), engagement (the channel; YouTube's numbers aren't read)}."""
    import style_study
    got = watch(url, ref)
    n = got.get("numbers") if isinstance(got.get("numbers"), dict) else {}
    duration = _num(n.get("duration")) or 0.0
    words = int(n["words"]) if isinstance(n.get("words"), int) and n["words"] >= 0 else None
    start, end = _num(n.get("speech_start")), _num(n.get("speech_end"))
    measured = {"duration": duration, "cuts": int(n["cuts"]) if isinstance(n.get("cuts"), int) and n["cuts"] >= 0 else None,
                "first_cut": _num(n.get("first_cut")), "shot_median": _num(n.get("shot_median")),
                "words": words, "speech_start": start,
                "words_per_sec": round(words / (end - start), 2) if words and start is not None and end and end > start
                else None,
                "transcript": str(got.get("transcript") or "")[:3000], "estimated_by": settings.REFERENCE_VIDEO_MODEL,
                "source": "youtube"}
    measured = {k: v for k, v in measured.items() if v is not None}
    cap = settings.REFERENCE_VIDEO_MAX_SECONDS
    gaps = ["Gemini watched it at about one frame a second: cuts and flashes closer together may be missed or merged"]
    if duration >= cap - 1:
        gaps.append(f"only the first {cap} s were watched (STUDIO_REFERENCE_VIDEO_MAX_SECONDS)")
        measured["watched_to"] = cap
    seen = {k: v for k, v in got.items() if k not in ("numbers", "transcript")}
    look = style_study.tidy_read(seen, [], gaps, duration or None)
    info = _oembed(url)
    try:
        stills = _still(url, info, stills_dir)
    except Exception as e:  # nice to have: never fails the reference
        log.info("No thumbnail for %s: %s", url, e)
        stills = []
    engagement = {"likes": None, "comments": None, "views": None, "account": _account(info), "followers": None,
                  "uploaded": None, "source": "youtube"}
    return {"kind": "video", "measured": measured, "look": look, "stills": stills, "engagement": engagement}
