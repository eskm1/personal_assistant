"""
AI concept pictures for carousel slides (cloud/CONTRACT.md, "Concept pictures"), drawn by
Google's Gemini image model ("Nano Banana") over its plain HTTPS API with Studio's own key
(GEMINI_API_KEY in studio/.env: sent only to GEMINI_URL, in a header, never logged).

  1. Claude writes each picture's brief from the slide (its heading and words, and the
     team's note on what it should show), the kind of post, Singapore flat proportions and
     the Urban Makers look (brands.md and the house style in the playbook). One call for
     the whole carousel.
  2. Gemini draws two options of each (4:5, a photograph, no words or faces).
  3. Claude looks at every picture and rejects the obviously wrong ones (warped furniture,
     rooms that couldn't be built, lettering, faces, extra limbs, the wrong room). A rejected
     picture is drawn once more with the problem named; still wrong, and that option goes.
     When neither option is left, the slide is laid out as text and Studio's read says why.
  4. Bryan picks between the two in Review (carousel.choose(): the slide is laid out again,
     nothing is drawn). Pictures are cached by their brief, so a new layout or version never
     pays twice; "redraw slide 3, warmer" redraws only slide 3.

Nothing is drawn until Bryan presses Make this (the carousel job is the owner's), sends a
draft back, or presses Redraw in Review. Every concept slide carries the "Concept" mark
(slide_images.Concept), and a concept never goes in a Finished homes post.
"""
import base64
import hashlib
import io
import json
import logging
import re
import shutil
import time
from pathlib import Path

import requests

import settings

log = logging.getLogger("studio.concepts")

CONCEPTS = settings.ROOT / "Concepts"   # <brief hash>_<option>.jpg and <brief hash>.json
OPTIONS = ("a", "b")                    # two of each, so Bryan has a choice
ASPECT = "4:5"                          # the slide is 1080x1350
MAX_CONCEPTS = 6                        # concept slides drawn per carousel; more are text
BACKOFF = 5.0                           # seconds before the one retry when Gemini is busy or down
TIMEOUT = 120                           # seconds for one picture
_HASH = re.compile(r"^[0-9a-f]{24}$")
FINISHED = "finished"
NOT_IN_FINISHED = "concept pictures never go in a Finished homes post (real work only)"

# Added to every picture's prompt, whatever Claude wrote: the rules hold in code too.
DRAW_RULES = ("A realistic photograph of a home interior in Singapore, for a design idea: natural daylight, "
              "real materials at real sizes, eye level, vertical 4:5 framing. No text, letters, numbers, signs, "
              "logos or watermarks anywhere in the picture. No people and no faces. No recognisable brands or "
              "products. Not a real address, estate or building.")
_SECOND = " Frame it from a different angle than the obvious one (this is a second option to choose from)."


def available() -> str | None:
    """None when Studio can draw concept pictures, else why not (plain words)."""
    if not settings.GEMINI_API_KEY:
        return "needs the image key (GEMINI_API_KEY) on the Studio machine"
    return None


def info() -> dict:
    """For the planner (laptop_status.info.concepts): can Studio draw them, and the price.
    Never the key."""
    return {"on": available() is None, "model": settings.IMAGE_MODEL, "per_image_usd": settings.IMAGE_COST_USD,
            "options": len(OPTIONS)}


def key_of(brief: str, round_: int = 0, aspect: str = ASPECT) -> str:
    """The cache key of a brief's pictures (the model and shape are part of it)."""
    raw = f"{settings.IMAGE_MODEL}\n{aspect}\n{int(round_)}\n{brief.strip()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _meta_path(key: str) -> Path:
    return CONCEPTS / f"{key}.json"


def meta(key) -> dict | None:
    if not isinstance(key, str) or not _HASH.match(key):
        return None
    try:
        return json.loads(_meta_path(key).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _save_meta(key: str, data: dict) -> None:
    CONCEPTS.mkdir(parents=True, exist_ok=True)
    tmp = _meta_path(key).with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(_meta_path(key))


def option_path(key: str, option: str) -> Path:
    if not _HASH.match(str(key or "")) or option not in OPTIONS:
        raise ValueError("not one of Studio's concept pictures")
    return CONCEPTS / f"{key}_{option}.jpg"


def chosen_path(image: dict) -> Path | None:
    """The picture Bryan picked (or the first that passed), if it's drawn and kept."""
    options = image.get("options") or []
    chosen = image.get("chosen") if image.get("chosen") in options else (options[0] if options else None)
    if not chosen or not image.get("concept"):
        return None
    try:
        path = option_path(image["concept"], chosen)
    except ValueError:
        return None
    return path if path.is_file() else None


def thumb_path(key: str, option: str) -> str:
    """Its preview in the planner's "thumbs" bucket."""
    return f"stills/concept_{key}_{option}.jpg"


# ── Gemini ────────────────────────────────────────────────────────────────────

class GeminiError(RuntimeError):
    """A picture Gemini didn't draw. retry: worth one more try (busy, down, no picture)."""

    def __init__(self, message: str, retry: bool = False, blocked: bool = False, wait: float | None = None):
        super().__init__(_scrub(message))
        self.retry, self.blocked, self.wait = retry, blocked, wait


def _scrub(text: str) -> str:
    key = settings.GEMINI_API_KEY
    return text.replace(key, "***") if key and text else text


_BLOCKED = {"SAFETY", "IMAGE_SAFETY", "PROHIBITED_CONTENT", "IMAGE_PROHIBITED_CONTENT", "BLOCKLIST", "SPII",
            "RECITATION", "IMAGE_RECITATION"}


def _retry_delay(r) -> float | None:
    """How long Gemini asked us to wait (Retry-After, or the error's RetryInfo), capped."""
    try:
        if r.headers.get("Retry-After"):
            return min(float(r.headers["Retry-After"]), 30.0)
        for d in (r.json().get("error") or {}).get("details") or []:
            if str(d.get("@type", "")).endswith("RetryInfo") and str(d.get("retryDelay", "")).endswith("s"):
                return min(float(d["retryDelay"][:-1]), 30.0)
    except (ValueError, TypeError, AttributeError):
        pass
    return None


def _busy(r) -> "GeminiError":
    """Gemini's 429 says which limit it hit; Review says which, so the fix is plain (Bryan, 2026-10-23: every
    slide read "Gemini is busy ... or the key's quota is used up" and nobody could tell which). A free-plan key
    has no picture quota at all ("limit: 0"), and a day's quota doesn't come back by retrying: neither retries."""
    try:
        text = json.dumps(r.json().get("error") or {})
    except (ValueError, TypeError, AttributeError):
        text = ""
    low = text.lower()
    if "limit: 0" in low or "free_tier" in low or "freetier" in low:
        return GeminiError("Gemini's free plan can't make pictures: turn on billing for this key in Google AI Studio")
    if "per_day" in low or "perday" in low:
        return GeminiError("Today's picture limit for this Gemini key is used up; it comes back tomorrow")
    return GeminiError("Gemini is busy (too many pictures at once); Redraw in a minute",
                       retry=True, wait=_retry_delay(r))


def _answer(r) -> tuple[bytes, dict]:
    """One generateContent answer: the picture as a JPEG and its usageMetadata."""
    if r.status_code == 429:
        raise _busy(r)
    if r.status_code >= 500:
        raise GeminiError(f"Gemini had a problem ({r.status_code})", retry=True, wait=_retry_delay(r))
    if r.status_code in (401, 403):
        raise GeminiError("Gemini refused the image key (check it in Google AI Studio, with billing on)")
    if r.status_code == 404:
        raise GeminiError(f"Gemini doesn't know the image model {settings.IMAGE_MODEL} (STUDIO_IMAGE_MODEL)")
    try:
        data = r.json()
    except ValueError:
        raise GeminiError(f"Gemini sent something Studio can't read ({r.status_code})", retry=r.status_code == 200)
    if r.status_code != 200:
        message = str((data.get("error") or {}).get("message") or r.status_code)[:200]
        if "api key" in message.lower() or "API_KEY" in json.dumps(data.get("error") or {}):
            raise GeminiError("Gemini refused the image key (check it in Google AI Studio, with billing on)")
        raise GeminiError(f"Gemini refused the request: {message}")
    usage = data.get("usageMetadata") if isinstance(data.get("usageMetadata"), dict) else {}
    block = (data.get("promptFeedback") or {}).get("blockReason")
    if block:
        raise GeminiError(f"Gemini's safety filter wouldn't draw it ({block})", blocked=True)
    candidates = data.get("candidates") or []
    for cand in candidates:
        for part in ((cand.get("content") or {}).get("parts") or []):
            inline = part.get("inlineData") or part.get("inline_data") or {}
            mime = inline.get("mimeType") or inline.get("mime_type") or ""
            if inline.get("data") and mime.startswith("image/"):
                return _jpeg(base64.b64decode(inline["data"])), usage
    reason = (candidates[0].get("finishReason") if candidates else None) or "no picture"
    if reason in _BLOCKED:
        raise GeminiError(f"Gemini's safety filter wouldn't draw it ({reason})", blocked=True)
    raise GeminiError(f"Gemini sent no picture ({reason})", retry=True)


def _jpeg(raw: bytes) -> bytes:
    """Gemini's picture (usually a PNG) as a JPEG Studio lays out."""
    from PIL import Image, UnidentifiedImageError
    try:
        with Image.open(io.BytesIO(raw)) as img:
            img = img.convert("RGB")
            if min(img.size) < 256:
                raise GeminiError(f"Gemini's picture was too small ({img.width}x{img.height})", retry=True)
            out = io.BytesIO()
            img.save(out, "JPEG", quality=92, optimize=True)
            return out.getvalue()
    except (UnidentifiedImageError, OSError) as e:
        raise GeminiError(f"Gemini's picture couldn't be read ({type(e).__name__})", retry=True)


def generate(prompt: str, aspect: str = ASPECT) -> tuple[bytes, dict]:
    """One picture from Gemini (generateContent, image only, 4:5 or the aspect asked): (JPEG bytes,
    usageMetadata). Busy, down or no picture: one more try after a pause. Raises GeminiError."""
    if available():
        raise GeminiError(available())
    url = f"{settings.GEMINI_URL}/models/{settings.IMAGE_MODEL}:generateContent"
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"responseModalities": ["IMAGE"], "imageConfig": {"aspectRatio": aspect}}}
    headers = {"x-goog-api-key": settings.GEMINI_API_KEY, "Content-Type": "application/json"}
    for attempt in (1, 2):
        try:
            r = requests.post(url, json=body, headers=headers, timeout=TIMEOUT)
            return _answer(r)
        except requests.RequestException as e:
            err = GeminiError(f"couldn't reach Gemini ({type(e).__name__})", retry=True)
        except GeminiError as e:
            err = e
        if not err.retry or attempt == 2:
            raise err
        log.info("Gemini: %s; trying once more", err)
        time.sleep(err.wait if err.wait is not None else BACKOFF)
    raise AssertionError("unreachable")


def draw_prompt(brief: str, option: str, avoid: str = "") -> str:
    prompt = f"{brief.strip()}\n\n{DRAW_RULES}" + (_SECOND if option == "b" else "")
    if avoid:
        prompt += f"\n\nAn earlier picture of this had a problem; make sure this one doesn't: {avoid.strip()}."
    return prompt


# ── Claude: the briefs, and the check ─────────────────────────────────────────

BRIEF_SCHEMA = {
    "type": "object",
    "properties": {"briefs": {"type": "array", "items": {
        "type": "object",
        "properties": {"slide": {"type": "integer"}, "room": {"type": "string"}, "brief": {"type": "string"}},
        "required": ["slide", "room", "brief"], "additionalProperties": False}}},
    "required": ["briefs"],
    "additionalProperties": False,
}

BRIEF_SYSTEM = """You write picture briefs for an AI image model that draws concept pictures \
for Urban Makers' Instagram carousels. Urban Makers is a renovation company in Singapore, owned \
by Bryan. A concept picture illustrates a design idea on one slide; it is always marked \
"Concept" and is never shown as a home Urban Makers built.

{brands}

Bryan's playbook, with the house style (the Urban Makers look):

<playbook>
{playbook}
</playbook>

Each brief (60 to 140 words, plain English) describes ONE photograph of a room:
- What the slide's idea looks like in a real home, so someone reading the slide gets the point \
at a glance. The team's note on what the picture should show comes first.
- Singapore homes, at real proportions: HDB flats have ceilings about 2.6 m high, aluminium-framed \
casement or sliding windows, compact rooms (a 4-room flat's living room about 3.5 by 5.5 m, a \
bedroom about 3 by 3.5 m), often a household shelter door and a service yard; condos have slightly \
higher ceilings and full-height sliding doors to a balcony. Furniture at real sizes for those \
rooms: no mansion-sized spaces, no views of named buildings.
- The Urban Makers look: what the house style above says; where it says nothing, warm, calm and \
lived-in: light oak or walnut timber, warm white walls, matte finishes, fluted panels, terrazzo or \
large-format tiles, brushed brass or black hardware, linen and rattan, plants, warm 2700-3000K light.
- A photograph, not a render: eye level, daylight from the window, the subject in the middle two \
thirds (words sit over the top and bottom of the slide).
- Never: words, letters, numbers, signs, logos or watermarks; people or faces; real places, \
estates or addresses; brand-name products anyone could identify; anything that claims to be a \
client's home or Urban Makers' finished work.
- A slide with a "change asked" keeps its previous brief and changes only what's asked.
- room: the room in a few words ("HDB living room"). Write one brief for every slide listed."""

CHECK_SCHEMA = {
    "type": "object",
    "properties": {"checks": {"type": "array", "items": {
        "type": "object",
        "properties": {"picture": {"type": "string"}, "ok": {"type": "boolean"}, "problem": {"type": "string"}},
        "required": ["picture", "ok", "problem"], "additionalProperties": False}}},
    "required": ["checks"],
    "additionalProperties": False,
}

CHECK_SYSTEM = """You check AI concept pictures for Urban Makers' Instagram carousels before Bryan \
sees them. Each picture is labelled (like "3a": slide 3, option a), with the slide's words and the \
brief it was drawn from. Reject a picture only for an obvious error a homeowner would notice:
- furniture or fittings that are warped, melted, floating, merged into each other or impossible \
(a chair with three legs, a door into a wall, a window where a wall should be, a tap with no basin);
- room geometry that couldn't be built: bent walls, clashing perspective, floors and ceilings that \
don't meet;
- any text, lettering, numbers, signs or logos, garbled or not;
- people's faces, or extra or missing limbs or fingers;
- the wrong room, or not what the slide is about (a kitchen when the slide is about a bedroom);
- clearly not a photograph (a cartoon, a sketch, a plastic-looking render).
Taste isn't your call: a plain but correct picture passes. ok: true when it passes. problem: when \
it doesn't, the problem in a few plain words for the redraw ("the chair has three legs"); otherwise \
"". Answer for every picture."""


def _client(client):
    if client is not None:
        return client
    import anthropic
    return anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY or None)


def _claude(client, ref: str, schema: dict, system: str, content, effort: str = "low", kind: str = "carousel") -> dict:
    import llm
    import usage
    response = llm.final_message(
        client, model=settings.CLAUDE_MODEL, max_tokens=12000,
        betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
        system=system, messages=[{"role": "user", "content": content}])
    usage.record(kind, ref, response)
    if response.stop_reason in ("refusal", "max_tokens"):
        raise RuntimeError(f"Claude couldn't do the concept pictures ({response.stop_reason})")
    return json.loads(next(b.text for b in response.content if b.type == "text"))


def _kind_text(post_type: str | None) -> str:
    import content_plan
    kind = content_plan.KINDS.get(post_type or "")
    return f"{kind['label']} ({kind['job']})" if kind else "not set"


def brief_prompt(title: str, post_type: str | None, slides: list[dict], wanted: list[int]) -> str:
    lines = [f"The carousel: {title}. Kind of post: {_kind_text(post_type)}.", "Its slides:"]
    for n, s in enumerate(slides, 1):
        lines.append(f"  {n}. [{s['role']}] {s['heading'] or '(no heading)'}" + (f" | {s['body']}" if s["body"] else ""))
    lines.append("Write a picture brief for each of these slides:")
    for n in wanted:
        img = slides[n - 1]["image"]
        lines.append(f"  slide {n}: the team's note on the picture: {img.get('note') or '(none: picture the slide)'}")
        if img.get("previous") and img.get("change"):
            lines.append(f"    previous brief: {img['previous']}")
            lines.append(f"    change asked: {img['change']}")
    return "\n".join(lines)


def write_briefs(title: str, post_type, slides: list[dict], wanted: list[int], ref: str, client=None) -> dict[int, str]:
    """One Claude call: a brief for each wanted slide (by number)."""
    import playbook
    system = BRIEF_SYSTEM.replace("{brands}", settings.brands_text()).replace("{playbook}", playbook.text()[:12000])
    answer = _claude(_client(client), ref, BRIEF_SCHEMA, system, brief_prompt(title, post_type, slides, wanted))
    out = {}
    for b in answer.get("briefs") or []:
        brief = str(b.get("brief") or "").strip()[:2000]
        if b.get("slide") in wanted and brief:
            out[b["slide"]] = brief
    return out


def _small(path: Path) -> str:
    from PIL import Image
    with Image.open(path) as img:
        img = img.convert("RGB")
        img.thumbnail((512, 640), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=85)
    return base64.standard_b64encode(buf.getvalue()).decode()


def check(items: list[dict], ref: str, client=None, reel: bool = False) -> dict[str, str]:
    """One Claude call over pictures [{label, path, slide, brief}]: label -> problem ("" = fine). reel:
    pictures for a Reel's inserts (reel_picture), the slide being the thing it shows."""
    content = []
    for it in items:
        s = it["slide"]
        content.append({"type": "text", "text": f"Picture {it['label']}: slide \"{s['heading']}\""
                        + (f" ({s['body']})" if s["body"] else "") + f". Brief: {it['brief']}"})
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                    "data": _small(it["path"])}})
    content.append({"type": "text", "text": "Check each picture above."})
    system = CHECK_SYSTEM + REEL_CHECK if reel else CHECK_SYSTEM
    answer = _claude(_client(client), ref, CHECK_SCHEMA, system, content, kind="edit" if reel else "carousel")
    verdicts = {str(c.get("picture", "")).strip().lower(): c for c in answer.get("checks") or []}
    out = {}
    for it in items:
        c = verdicts.get(it["label"])
        # Not answered for: passed (Bryan still sees it and picks).
        out[it["label"]] = "" if c is None or c.get("ok") else (str(c.get("problem") or "").strip()[:200] or "it looked wrong")
    return out


# ── Preparing a carousel's concept slides (carousel.run, before the layout) ───

def redraw_image(image: dict, change: str = "") -> dict:
    """The picture of a concept slide, to be drawn again: with a change ("warmer light"),
    Claude rewrites the brief from the previous one; without, the same brief, new pictures."""
    change = (change or "").strip()[:300]
    out = {"source": "concept"}
    if image.get("note"):
        out["note"] = image["note"]
    if change and image.get("brief"):
        out.update(previous=image["brief"], change=change)
    elif change:
        out["note"] = f"{image.get('note') or ''} ({change})".strip()[:300]
    elif image.get("brief"):
        out.update(brief=image["brief"], round=int(image.get("round") or 0) + 1)
    return out


def _share(key: str, option: str) -> str | None:
    """Put the picture's preview in the planner's storage (best effort)."""
    try:
        import cloud_sync
        import slide_images
        if not cloud_sync.enabled():
            return None
        path = thumb_path(key, option)
        cloud_sync.Client().upload(path, slide_images.preview(option_path(key, option)))
        return path
    except Exception as e:
        log.warning("Couldn't share a concept picture's preview: %s", e)
        return None


def _apply(image: dict, data: dict) -> str | None:
    """Fill a slide's image from its pictures' record. Returns why it has none, if so."""
    passed = [o for o in OPTIONS if (data.get("options") or {}).get(o, {}).get("state") == "ok"
              and option_path(image["concept"], o).is_file()]
    image["options"] = passed
    if not passed:
        image.pop("chosen", None)
        image.pop("thumb_path", None)
        problems = [o.get("problem") for o in (data.get("options") or {}).values() if o.get("problem")]
        image["why"] = ("the concept pictures came out wrong twice" + (f" ({problems[0]})" if problems else "")
                        if data.get("checked") else (problems[0] if problems else "the concept pictures couldn't be drawn"))
        return image["why"]
    image.pop("why", None)
    if image.get("chosen") not in passed:
        image["chosen"] = passed[0]
    shared = (data.get("shared") or {}).get(image["chosen"])
    if shared:
        image["thumb_path"] = shared
    return None


def prepare(conn, production_id: int, title: str, post_type, slides: list[dict], client=None) -> dict:
    """Make sure every concept slide has its pictures: briefs written, two options drawn and
    checked (redrawn once when wrong), from the cache when they're there. Changes the slides'
    images in place. Returns {drawn, usd, notes: {slide number: why it's text only}}."""
    import usage
    ref = f"production:{production_id}"
    drawn, usd, notes = 0, 0.0, {}
    wanted = [n for n, s in enumerate(slides, 1) if s["image"].get("source") == "concept"]
    if not wanted:
        return {"drawn": 0, "usd": 0.0, "notes": {}}
    todo = []
    for n in wanted:
        image = slides[n - 1]["image"]
        if post_type == FINISHED:
            # Laid out as text; pictures already drawn stay cached in case the kind changes back.
            for k in ("options", "chosen", "thumb_path"):
                image.pop(k, None)
            image["why"] = notes[n] = NOT_IN_FINISHED
            continue
        data = meta(image.get("concept"))
        if data and data.get("done"):
            why = _apply(image, data)
            if why:
                notes[n] = why
            continue
        if available():
            image["why"] = notes[n] = available()
            continue
        if len(todo) >= MAX_CONCEPTS:
            image["why"] = notes[n] = f"at most {MAX_CONCEPTS} concept pictures per carousel"
            continue
        todo.append(n)
    if not todo:
        return {"drawn": 0, "usd": 0.0, "notes": notes}

    # 1. Briefs, for the slides that have none yet (or asked for a change).
    need = [n for n in todo if not slides[n - 1]["image"].get("brief")]
    if need:
        try:
            briefs = write_briefs(title, post_type, slides, need, ref, client)
        except Exception as e:  # Claude down: those slides are text for now, the next version tries again
            log.warning("Concept briefs for production %s failed: %s", production_id, e)
            briefs = {}
        for n in need:
            image = slides[n - 1]["image"]
            if n not in briefs:
                image["why"] = notes[n] = "Claude didn't write its picture brief"
                continue
            image["brief"] = briefs[n]
            image["round"] = 0
            for k in ("previous", "change", "concept", "options", "chosen", "thumb_path"):
                image.pop(k, None)
    todo = [n for n in todo if slides[n - 1]["image"].get("brief")]
    CONCEPTS.mkdir(parents=True, exist_ok=True)
    records = {}
    for n in todo:
        image = slides[n - 1]["image"]
        image["concept"] = key_of(image["brief"], image.get("round") or 0)
        records[n] = meta(image["concept"]) or {"brief": image["brief"], "model": settings.IMAGE_MODEL,
                                                 "round": image.get("round") or 0, "options": {}}

    # 2-3. Draw, check, redraw once what's wrong, check again.
    for stage in (1, 2):
        batch = []
        for n in todo:
            image, data = slides[n - 1]["image"], records[n]
            if data.get("done"):
                continue
            for opt in OPTIONS:
                o = data["options"].setdefault(opt, {})
                if o.get("state") in ("ok", "bad", "blocked"):
                    continue
                if (stage == 2) != (o.get("state") == "retry"):
                    continue
                path = CONCEPTS / f"{image['concept']}_{opt}_{stage}.jpg"
                if not path.is_file():
                    try:
                        jpg, meta_ = generate(draw_prompt(image["brief"], opt, o.get("problem", "") if stage == 2 else ""))
                    except GeminiError as e:
                        o.update(state="blocked" if e.blocked else "failed", problem=str(e))
                        log.warning("Concept picture %s%s for production %s: %s", n, opt, production_id, e)
                        continue
                    path.write_bytes(jpg)
                    usd += usage.record_image(ref, settings.IMAGE_MODEL, meta_, settings.IMAGE_COST_USD)
                    drawn += 1
                    o["tries"] = stage
                batch.append({"label": f"{n}{opt}", "path": path, "slide": slides[n - 1], "brief": image["brief"],
                              "n": n, "opt": opt, "stage": stage})
        if batch:
            try:
                problems = check(batch, ref, client)
            except Exception as e:  # Claude down: the pictures stay drawn (cached) and are checked next time
                log.warning("Checking concept pictures for production %s failed: %s", production_id, e)
                for it in batch:
                    records[it["n"]]["options"][it["opt"]].update(state="unchecked", problem="Claude couldn't check the pictures")
                break
            for it in batch:
                data, o = records[it["n"]], records[it["n"]]["options"][it["opt"]]
                problem = problems.get(it["label"], "")
                if not problem:
                    shutil.copyfile(it["path"], option_path(slides[it["n"] - 1]["image"]["concept"], it["opt"]))
                    o.update(state="ok", problem="")
                else:
                    o.update(state="retry" if it["stage"] == 1 else "bad", problem=problem)
                data["checked"] = True
        for n in todo:
            _save_meta(slides[n - 1]["image"]["concept"], records[n])

    # 4. What each slide ends up with.
    for n in todo:
        image, data = slides[n - 1]["image"], records[n]
        states = [data["options"].get(o, {}).get("state") for o in OPTIONS]
        # Busy or down (not the picture's fault): not settled, so the next version tries again.
        data["done"] = all(s in ("ok", "bad", "blocked") for s in states)
        if data["done"]:  # settled: the drafts before the check aren't needed any more
            for stale in CONCEPTS.glob(f"{image['concept']}_?_?.jpg"):
                stale.unlink(missing_ok=True)
        data.setdefault("shared", {})
        for opt in OPTIONS:
            if data["options"].get(opt, {}).get("state") == "ok" and opt not in data["shared"]:
                shared = _share(image["concept"], opt)
                if shared:
                    data["shared"][opt] = shared
        _save_meta(image["concept"], data)
        why = _apply(image, data)
        if why:
            notes[n] = why
    if drawn:
        log.info("Concept pictures for production %s: %d drawn (US$%.3f)", production_id, drawn, usd)
    return {"drawn": drawn, "usd": round(usd, 4), "notes": notes}


# ── A Reel's picture inserts (pictures.py) ────────────────────────────────────
# One picture each, 9:16, drawn from the brief the edit plan wrote; checked like a carousel's and drawn
# once more with the problem named when it's wrong. Same cache (CONCEPTS: <key>_a.jpg and <key>.json), so a
# re-render, a fix round or a new version that keeps the brief never pays twice.

REEL_ASPECT = "9:16"
REEL_RULES = ("A realistic photograph for a renovation company's Instagram Reel, showing one thing clearly: a "
              "material, a finish, a fitting, a piece of joinery or a finished look, in a home in Singapore. Soft "
              "natural daylight, calm and warm, real materials at real sizes, vertical 9:16 framing with the subject "
              "filling the middle of the frame (words sit over the top and bottom). No text, letters, numbers, signs, "
              "logos or watermarks anywhere in the picture. No people and no faces. No recognisable brands or "
              "products. Not a real address, estate or building.")
REEL_CHECK = ("\nThese pictures are for a Reel: each shows one thing (a material, a finish, a fitting, a detail or "
              "a finished look) full screen while Bryan talks about it; \"slide\" is that thing. A close-up with no "
              "room is fine. Reject it when it isn't recognisably that thing.")


def reel_prompt(brief: str, avoid: str = "") -> str:
    prompt = f"{brief.strip()}\n\n{REEL_RULES}"
    if avoid:
        prompt += f"\n\nAn earlier picture of this had a problem; make sure this one doesn't: {avoid.strip()}."
    return prompt


def reel_key(brief: str) -> str:
    return key_of(brief, 0, REEL_ASPECT)


def reel_cached(brief: str) -> Path | None:
    """The Reel picture of this brief, when it's drawn, checked and kept (no Gemini, no Claude)."""
    key = reel_key(brief)
    data = meta(key) or {}
    path = option_path(key, "a")
    return path if data.get("done") and (data.get("options") or {}).get("a", {}).get("state") == "ok" \
        and path.is_file() else None


def reel_picture(brief: str, subject: str, ref: str, client=None, may_draw: bool = True) -> dict:
    """The picture for one of a Reel's inserts: from the cache when it's there; else drawn once (9:16),
    checked by Claude, and drawn once more with the problem named when it's wrong. may_draw: False when
    the draft's picture budget is spent (only the cache is used). Returns {key, path (None when there's
    no picture to show), drawn (pictures Gemini drew), usd, cached, why}. Never raises: a draft
    without the picture (it gets the drawing) beats no draft."""
    import usage
    brief = (brief or "").strip()[:2000]
    key = reel_key(brief)
    out = {"key": key, "path": None, "drawn": 0, "usd": 0.0, "cached": False, "why": ""}
    if not brief:
        return {**out, "why": "it had no picture brief"}
    hit = reel_cached(brief)
    if hit:
        return {**out, "path": hit, "cached": True}
    data = meta(key) or {"brief": brief, "model": settings.IMAGE_MODEL, "aspect": REEL_ASPECT, "round": 0,
                         "kind": "reel", "options": {}}
    o = data["options"].setdefault("a", {})
    if data.get("done"):  # settled and not usable (wrong twice, or the safety filter): drawn, not paid again
        return {**out, "why": o.get("problem") or "the picture came out wrong"}
    if available():
        return {**out, "why": available()}
    CONCEPTS.mkdir(parents=True, exist_ok=True)
    slide = {"heading": (subject or "").strip()[:120] or "the picture", "body": ""}
    for stage in ((2,) if o.get("state") == "retry" else (1, 2)):  # wrong once already: straight to the redraw
        if stage == 2 and o.get("state") != "retry":
            break
        path = CONCEPTS / f"{key}_a_{stage}.jpg"
        if not path.is_file():  # drawn before but not checked (Claude was down): checked now, not drawn again
            if not may_draw:
                out["why"] = "Studio had drawn as many new pictures as a draft may have"
                break
            try:
                jpg, meta_ = generate(reel_prompt(brief, o.get("problem", "") if stage == 2 else ""), REEL_ASPECT)
            except GeminiError as e:
                o.update(state="blocked" if e.blocked else "failed", problem=str(e))
                log.warning("Reel picture \"%s\" for %s: %s", subject, ref, e)
                out["why"] = str(e)
                break
            path.write_bytes(jpg)
            out["usd"] += usage.record_image(ref, settings.IMAGE_MODEL, meta_, settings.IMAGE_COST_USD)
            out["drawn"] += 1
            o["tries"] = stage
        try:
            problem = check([{"label": "1a", "path": path, "slide": slide, "brief": brief}], ref, client, reel=True)["1a"]
        except Exception as e:  # Claude down: drawn and kept, checked next time (never shown unchecked)
            log.warning("Checking the Reel picture \"%s\" for %s failed: %s", subject, ref, e)
            o.update(state="unchecked", problem="Claude couldn't check the picture")
            out["why"] = o["problem"]
            break
        data["checked"] = True
        if not problem:
            shutil.copyfile(path, option_path(key, "a"))
            o.update(state="ok", problem="")
            out["path"] = option_path(key, "a")
            break
        o.update(state="retry" if stage == 1 else "bad", problem=problem)
        out["why"] = f"the picture came out wrong twice ({problem})" if stage == 2 else problem
    data["done"] = o.get("state") in ("ok", "bad", "blocked")
    if data["done"]:
        for stale in CONCEPTS.glob(f"{key}_a_?.jpg"):
            stale.unlink(missing_ok=True)
    _save_meta(key, data)
    out["usd"] = round(out["usd"], 4)
    return out
