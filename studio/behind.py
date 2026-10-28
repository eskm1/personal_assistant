"""
Words behind Bryan (Bryan, 2026-10-28: "Have words pop up behind my head together in the background (making it appear
that I'm in front of the words)"). The first of the layered effects.

The picture, back to front: the footage's background, then big words (the look's display face: Archivo in Site Notes),
then Bryan, cut out of the frame, then the captions and graphics as usual. It is made as one more transparent insert
(the same path as a drawn insert, editor.mix_final): the words with Bryan's shape cut out of them, laid over the footage
under the captions. Where the cut-out is solid, the footage (Bryan) shows through untouched.

  1. The plan may choose it for the hook or one key line on a talking shot that suits it (rule(), graphics style
     "behind"; at most settings.EDIT_BEHIND_MAX, 2 at most). take() moves those out of the plan's graphics into
     edl["behind_plan"], so nothing that reads graphics (cards, the read's numbers, Resolve) sees a new style. A note
     of Bryan's ("words behind me here", "no words behind me") adds or removes one by code too (asks_from_notes,
     apply_asks), and the read's action "behind" does it in a fix round with no new plan (fix_actions._behind).
  2. place(): on the draft's clock (the hook from the first frame; a key line timed to his words), 1.5 to 4 seconds,
     inside one framing of one shot, clear of the cards, the inserts, a voice-over's window and the close.
  3. make(): the stretch of the finished picture is read back, the person found in every frame (Segmenter: MediaPipe's
     selfie multiclass model through LiteRT, on the CPU, offline once its model is fetched to ROOT/Models), the matte
     smoothed over time (no flicker), snapped to the picture's edges and feathered. The confidence check (confidence())
     must pass: Bryan clearly in the picture all through, one person, a steady outline, a still camera, his face found.
     The words are laid out behind and around his head, never cut off at the frame's edge, at least VISIBLE_MIN of them
     showing; his face is kept clear twice over (the matte, and the face the model finds). They pop up together.
  4. Anything that stops it, and it falls back to the usual words on top (the hook as the hook, a key line as a
     callout), with why in the draft's checks. final_check flags a cut-out that let words over his face.
"""
import hashlib
import importlib.util
import json
import logging
import math
import os
import re
import subprocess
import time
from pathlib import Path

import numpy as np

import editor
import extract
import settings
from settings import ROOT

log = logging.getLogger("studio.behind")

STYLE = "behind"
SHORTEST, LONGEST = 1.5, 4.0       # seconds on screen
GAP = 3.0                          # seconds of footage between two
MOST_WORDS, MOST_CHARS = 4, 28
NEAR_NOTE = 3.0                    # a note this close to one is about that one
POP_IN, SHOW_IN, FADE_OUT = 0.42, 0.16, 0.25
POP_FROM, POP_OVER, OUT_GROW = 0.84, 1.04, 0.03
LOOK = "behind-1"                  # bump when the look changes: the layers are made again

# The person finder: MediaPipe's selfie multiclass model (Apache-2.0; background, hair, body skin, face skin, clothes,
# others), run by LiteRT on the CPU. Fetched once from Google's model store into ROOT/Models and checked.
MODELS = ROOT / "Models"
MODEL = {"file": "selfie_multiclass_256x256.tflite", "bytes": 16371837,
         "url": "https://storage.googleapis.com/mediapipe-models/image_segmenter/selfie_multiclass_256x256/float32/1/"
                "selfie_multiclass_256x256.tflite",
         "sha256": "c6748b1253a99067ef71f7e26ca71096cd449baefa8f101900ea23016507e0e0"}
FACE_CLASS = 3

# The matte is worked at a quarter of the Reel (WORK), then brought up to full size only where the words are.
WORK_W, WORK_H = 270, 480
READ = (540, 960)                  # the stretch is read at this size: the finder works at 256 px, the matte at WORK
SMOOTH = (1, 2, 3, 2, 1)           # weights over neighbouring frames: the outline doesn't flicker
GUIDE_R, GUIDE_EPS = 4, 1e-3       # the matte snapped to the picture's edges (a guided filter at WORK size)
EDGE_LO, EDGE_HI = 0.3, 0.62       # the matte's soft edge before feathering
GROW, FEATHER = 2, 2.4             # px at full size: a hair of background left round him, then a soft edge
HOLE_MAX = 0.01                    # a gap inside him smaller than this share of the frame is the finder's mistake
FACE_GUARD = 6                     # px at WORK size: the face found, grown by this, never has words on it

# The confidence check (all on the WORK mattes).
MIN_AREA = 0.04                    # he fills at least this share of the frame ...
PRESENT = 0.9                      # ... in this share of the frames
STEADY_IOU, WORST_IOU, AREA_SWING = 0.85, 0.6, 0.2
SECOND_PERSON = 0.25               # a second shape at least this share of the biggest is another person
CAMERA_STEP, CAMERA_PATH = 9.0, 140.0   # Reel px: a camera moving more than this a frame, or this far in all
FACE_SEEN = 0.8

# The words: inside the frame's safe area, above the captions; this much of them must show around him.
SAFE_X = 64
SAFE_TOP, SAFE_BOTTOM = 230, editor.CARD_BOTTOM
MAX_PX = 270                       # the words' largest size (px); a block at most HEIGHT of the safe area
VISIBLE_MIN = 0.5                  # of the words showing around him, in all
WORD_MIN, KEY_MIN = 0.55, 0.7      # each word (the key word) at least this much showing
BEHIND_MIN = 0.03                  # and at least this much of them behind him (or they'd just sit above his head)
BEHIND_AIM = 0.15                  # the look: about this much of them behind his head (he is clearly in front)
HEIGHT = 0.5
BRIGHT = 0.62                      # a background lighter than this gets dark words


# ── On or off, and the model ──────────────────────────────────────────────────

_override = None                   # a Segmenter the tests hand in (use())
_cached = None
_failed_at = 0.0


def use(segmenter) -> None:
    """Studio finds the person with this from now on (tests: a fake); None goes back to the model."""
    global _override, _cached
    _override, _cached = segmenter, None


def available() -> str | None:
    """None when Studio can put words behind Bryan, else why not (plain words)."""
    if not settings.EDIT_BEHIND or settings.EDIT_BEHIND_MAX <= 0:
        return "words behind you are turned off (STUDIO_EDIT_BEHIND)"
    if _override is None and importlib.util.find_spec("ai_edge_litert") is None:
        return "the person finder isn't installed on the Studio machine yet (ai-edge-litert)"
    return None


def model_path() -> Path:
    """The model, fetched once into ROOT/Models and checked against its fingerprint. Raises when it can't be had (a
    failed fetch isn't tried again for an hour)."""
    global _failed_at
    path = MODELS / MODEL["file"]
    if path.exists() and path.stat().st_size == MODEL["bytes"]:
        return path
    if time.time() - _failed_at < 3600:
        raise RuntimeError("the person finder's model couldn't be fetched (tried in the last hour)")
    MODELS.mkdir(parents=True, exist_ok=True)
    part = path.with_suffix(".part")
    try:
        import requests
        with requests.get(MODEL["url"], stream=True, timeout=60) as r:
            r.raise_for_status()
            with open(part, "wb") as f:
                for chunk in r.iter_content(1 << 20):
                    f.write(chunk)
        digest = hashlib.sha256(part.read_bytes()).hexdigest()
        if digest != MODEL["sha256"]:
            raise RuntimeError("the person finder's model came down wrong (its fingerprint doesn't match)")
        part.replace(path)
        log.info("Fetched the person finder's model (%s, %.1f MB)", MODEL["file"], path.stat().st_size / 1e6)
        return path
    except Exception as e:
        _failed_at = time.time()
        part.unlink(missing_ok=True)
        raise RuntimeError(f"the person finder's model couldn't be fetched ({type(e).__name__}: {e})"[:300]) from e


class Segmenter:
    """Finds the person in a frame. person(rgb) -> (person, face): float maps 0..1 in the frame's proportions, any
    size; face may be None (then the head is the top of the person)."""
    name = "none"

    def person(self, rgb: np.ndarray):
        raise NotImplementedError


class LiteSegmenter(Segmenter):
    """MediaPipe's selfie multiclass model (256x256) on LiteRT. The frame is squeezed to the model's square, as
    MediaPipe's own segmenter does, and the classes come back as the person (all but the background) and the face."""
    name = "mediapipe-selfie-multiclass-256"

    def __init__(self, path: Path, threads: int | None = None):
        from ai_edge_litert.interpreter import Interpreter
        self.interp = Interpreter(model_path=str(path), num_threads=threads or max(1, min(4, (os.cpu_count() or 2) // 2)))
        self.interp.allocate_tensors()
        self.inp = self.interp.get_input_details()[0]
        self.out = self.interp.get_output_details()[0]
        self.h, self.w = int(self.inp["shape"][1]), int(self.inp["shape"][2])

    def person(self, rgb: np.ndarray):
        import cv2
        x = cv2.resize(rgb, (self.w, self.h), interpolation=cv2.INTER_AREA).astype(np.float32)[None] / 255.0
        self.interp.set_tensor(self.inp["index"], x)
        self.interp.invoke()
        logits = self.interp.get_tensor(self.out["index"])[0].astype(np.float32)
        e = np.exp(logits - logits.max(-1, keepdims=True))
        p = e / e.sum(-1, keepdims=True)
        return 1.0 - p[..., 0], p[..., FACE_CLASS]


def segmenter() -> Segmenter:
    global _cached
    if _override is not None:
        return _override
    if _cached is None:
        _cached = LiteSegmenter(model_path())
    return _cached


# ── The plan ──────────────────────────────────────────────────────────────────

_RULE = """- Words behind Bryan (style "behind"): big words that pop up in the background behind his head, so he \
stands in front of them. Only for the hook or one key line, on a talking shot where he is clearly in the picture, \
alone, with a still camera and some background around his head. At most {cap} per Reel, usually one or none. title: \
the words, 1 to 4 words (at most 28 characters), the hook's words or the key line's in short, one *key* word marked or \
none. Anchor it like a card (clip_id, "at" when he starts saying them, duration 1.5 to 4 seconds), never at the same \
time as a card, a callout, a drawn animation or a picture. For the hook use role "hook", "at" his first word, and \
hook_text the same words (Studio shows them behind him, and on top as usual when it can't). Otherwise role "explain". \
lines [], scene "", look "cutaway", why: a few plain words. A note of Bryan's asking for words behind him makes one \
at that moment; one asking for none takes them away.
"""


def rule() -> str:
    """The plan prompt's part on words behind him, or "" when Studio can't make them."""
    return "" if available() else _RULE.format(cap=settings.EDIT_BEHIND_MAX)


def schema(base: dict) -> dict:
    """The edit plan's schema with the style "behind"."""
    out = json.loads(json.dumps(base))
    enum = out["properties"]["graphics"]["items"]["properties"]["style"]["enum"]
    if STYLE not in enum:
        enum.append(STYLE)
    return out


def words_of(text) -> str:
    """The words to show: plain, one line, at most MOST_WORDS words and MOST_CHARS characters (whole words)."""
    text = re.sub(r"\s+", " ", editor.unmark(text or "")).strip().strip(".,;:!?\"'“”‘’")
    out = []
    for w in text.split()[:MOST_WORDS]:
        if len(" ".join(out + [w])) > MOST_CHARS:
            break
        out.append(w)
    return " ".join(out)


def take(edl: dict, previous: dict | None = None) -> None:
    """The plan's words behind him moved out of its graphics into edl["behind_plan"] (anchored in the clips), so the
    rest of the editor never sees the style. "No words behind me" from before holds (edl["behind_off"])."""
    graphics = edl.get("graphics") or []
    mine = [g for g in graphics if g.get("style") == STYLE]
    if mine:
        edl["graphics"] = [g for g in graphics if g.get("style") != STYLE]
    if (previous or {}).get("behind_off"):
        edl["behind_off"] = True
    if edl.get("behind_off") or available():
        mine = []
    plan = [{"clip_id": g.get("clip_id"), "at": g.get("at"), "duration": g.get("duration"),
             "words": words_of(g.get("title")), "italic": g.get("italic") or "",
             "role": "hook" if g.get("role") == "hook" else "explain", "why": editor.unmark(g.get("why") or "")[:120]}
            for g in mine if words_of(g.get("title"))]
    if plan:  # a plan with none is left exactly as it was
        edl["behind_plan"] = plan


def describe(edl: dict) -> str:
    """For the revision prompt: the words behind him in the draft before (so a re-plan keeps or changes them)."""
    items = [b for b in (edl or {}).get("behind") or [] if "start" in b]
    if not items:
        return ""
    return ("\nPrevious words behind Bryan (style behind; clip, at, duration, role): " + "; ".join(
        f"{b['start']:.1f}-{b['start'] + b['duration']:.1f}s \"{editor._with_mark(b['words'], b.get('italic', ''))}\" "
        f"clip {b.get('clip_id')} at {float(b.get('at') or 0):.2f}s, {b['duration']:.1f}s, {b.get('role', 'explain')}"
        for b in items))


# ── Bryan's notes ─────────────────────────────────────────────────────────────

_WHERE = r"behind\s+(?:me|my\s+head|him|bryan|you)\b"
# Only notes about words or text behind him ("the mess behind me is distracting" is not one), or a quoted phrase put
# there; "no words behind me", "remove the text behind my head", "take the words behind me off" take them away.
_WORDS = r"(?:words?|text|title|letters)"
_ADD = re.compile(rf"\b{_WORDS}\b[^.!?]{{0,30}}\b{_WHERE}|\b(?:put|pop|show|have|place)\s+[\"“‘'][^\"”’']+[\"”’']\s+{_WHERE}", re.I)
_OFF = re.compile(rf"\b(?:no|remove|without|drop|lose|get rid of|take (?:away|off|out))\s+(?:the\s+|any\s+|those\s+)?{_WORDS}\s+{_WHERE}"
                  rf"|\b(?:don'?t|do not)\s+(?:want|put|need|add|show)\s+(?:the\s+|any\s+)?{_WORDS}\s+{_WHERE}"
                  rf"|\b{_WORDS}\s+{_WHERE}[^.!?]{{0,30}}\b(?:off|away|out|gone)\b|\bnothing\s+{_WHERE}", re.I)
_QUOTED = re.compile(r"[\"“‘']([^\"”’']{2,40})[\"”’']")
_SAYING = re.compile(r"\b(?:saying|reading|that says|that read|with)\s+([A-Za-z0-9][^.!?,;]{1,40}?)\s*(?:" + _WHERE
                     + r"|here|there|$|[.!?,;])", re.I)


def note_ask(text: str) -> dict | None:
    """What a note asks: {"op": "add", "words": quoted or named words, or ""} / {"op": "remove"}, or None."""
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if _OFF.search(text):
        return {"op": "remove"}
    if not _ADD.search(text):
        return None
    m = _QUOTED.search(text) or _SAYING.search(text)
    words = words_of(m.group(1)) if m else ""
    if words.lower() in ("me", "my head", "the words", "words", "text"):
        words = ""
    return {"op": "add", "words": words[:1].upper() + words[1:]}


def _spoken(draft, t: float, key_words=()) -> str:
    """Up to MOST_WORDS words he says at t (the draft's clock): the clause, short; around a key word when it has one,
    else its last words."""
    words = draft.words
    if not words:
        return ""
    k = min(range(len(words)), key=lambda i: 0 if words[i]["start"] <= t <= words[i]["end"]
            else min(abs(words[i]["start"] - t), abs(words[i]["end"] - t)))
    a = k
    while a > 0 and not editor.CLAUSE_END.search(words[a - 1]["word"].strip()):
        a -= 1
    b = k
    while b < len(words) - 1 and not editor.CLAUSE_END.search(words[b]["word"].strip()):
        b += 1
    clause = [re.sub(r"[^\w'-]", "", w["word"]) for w in words[a:b + 1]]
    clause = [w for w in clause if w]
    keys = {editor._core(x).lower() for x in key_words or []}
    hit = next((i for i, w in enumerate(clause) if w.lower() in keys), None)
    if len(clause) > MOST_WORDS - 1:
        clause = clause[max(0, hit - 1):hit + 2] if hit is not None else clause[-3:]
    text = words_of(" ".join(clause))
    return text[:1].upper() + text[1:]


def asks_from_notes(notes: list[dict], previous: dict, clips: dict) -> list[dict]:
    """Bryan's notes about words behind him, worked out on the draft they were made on: [{note_id, op, clip_id, at
    (seconds in the clip), words, hook}]. A note in the hook's first seconds is about the hook."""
    import fix_actions
    cuts = previous.get("cuts") or []
    out = []
    if not cuts:
        return out
    draft = None
    for n in notes or []:
        ask = note_ask(n.get("body"))
        if ask is None:
            continue
        t = n.get("at_seconds")
        if ask["op"] == "remove":
            src = None
            if t is not None:
                draft = draft or fix_actions.Draft(cuts, clips)
                src = draft.to_source(float(t))
            out.append({"note_id": n.get("id"), "op": "remove", "clip_id": src[0] if src else None,
                        "at": round(src[1], 3) if src else None, "words": "", "hook": False})
            continue
        if t is None:
            continue  # where? the plan reads the note itself
        draft = draft or fix_actions.Draft(cuts, clips)
        t = float(t)
        hook_words = words_of(previous.get("hook_text")) or next(
            (b["words"] for b in previous.get("behind") or [] if b.get("role") == "hook"), "")
        hook = t < editor.HOOK_SECONDS_SHOWN and bool(hook_words)
        words = ask["words"] or (hook_words if hook else _spoken(draft, t, previous.get("key_words") or []))
        src = draft.to_source(0.0 if hook else t)
        if not words or not src:
            continue
        out.append({"note_id": n.get("id"), "op": "add", "clip_id": src[0], "at": round(src[1], 3), "words": words,
                    "hook": hook})
    return out


def apply_asks(edl: dict, asks: list[dict]) -> list[str]:
    """Bryan's asks made on a new plan (edl["behind_plan"], still anchored in the clips). Returns what was done, in
    plain words (kept for the draft's checks)."""
    plan = edl.setdefault("behind_plan", [])
    did = []
    for ask in asks:
        def near(b):
            return b.get("clip_id") == ask["clip_id"] and abs(float(b.get("at") or -99) - float(ask["at"])) <= NEAR_NOTE
        if ask["op"] == "remove":
            gone = [b for b in plan if ask["at"] is None or near(b)]
            for b in gone:
                plan.remove(b)
            if ask["at"] is None:
                edl["behind_off"] = True
                did.append("took the words behind you off, as your note asked (and keeps them off for this post)")
            elif gone:
                did.append(f"took \"{gone[0]['words']}\" from behind you, as your note asked")
            continue
        edl.pop("behind_off", None)
        here = next((b for b in plan if near(b)), None)
        if here is not None:
            here.update(words=ask["words"], italic="", asked=True, **({"role": "hook"} if ask["hook"] else {}))
        else:
            plan.append({"clip_id": ask["clip_id"], "at": ask["at"], "duration": 2.6 if ask["hook"] else 2.5,
                         "words": ask["words"], "italic": "", "role": "hook" if ask["hook"] else "explain",
                         "why": "you asked for words behind you here", "asked": True})
        if ask["hook"] and not edl.get("hook_text"):
            edl["hook_text"], edl["hook_italic"] = ask["words"], ""
        did.append(f"put \"{ask['words']}\" behind you, as your note asked")
    return did


# ── On the draft's clock ──────────────────────────────────────────────────────

def _offsets(cuts: list[dict]) -> list[float]:
    out, off = [], 0.0
    for c in cuts:
        out.append(off)
        off += c["end"] - c["start"]
    return out


def _framing(c: dict) -> tuple:
    return c["clip_id"], c.get("crop_x"), c.get("crop_y"), round(float(c.get("zoom") or 1.0), 4)


def _stretch_end(cuts: list[dict], t: float) -> float:
    """Where the framing at t (draft seconds) changes: the shot, its crop and its zoom stay the same until then."""
    offsets = _offsets(cuts)
    k = next((i for i, (c, off) in enumerate(zip(cuts, offsets)) if off - 0.001 <= t < off + c["end"] - c["start"]), None)
    if k is None:
        return t
    end = offsets[k] + cuts[k]["end"] - cuts[k]["start"]
    while k + 1 < len(cuts) and _framing(cuts[k + 1]) == _framing(cuts[k]):
        k += 1
        end = offsets[k] + cuts[k]["end"] - cuts[k]["start"]
    return end


def _end_at(start: float, want: float, words: list[dict]) -> float:
    """Ends just after a word, as near start + want as SHORTEST..LONGEST allow."""
    fits = [w for w in words if start + SHORTEST - editor.TAIL_PAD <= w["end"] <= start + LONGEST - editor.TAIL_PAD]
    if not fits:
        return start + min(max(want, SHORTEST), LONGEST)
    best = min(fits, key=lambda w: abs(w["end"] + editor.TAIL_PAD - (start + want)))
    return max(best["end"] + editor.TAIL_PAD, best.get("sound_end", 0.0))


def frame_at(t: float) -> float:
    return round(round(t * editor.FPS) / editor.FPS, 4)


def place(items: list[dict], cuts: list[dict], total: float, words: list[dict], hook_text: str,
          busy: list[dict] = (), window: list = ()) -> tuple[list[dict], list[dict]]:
    """The words behind him on the draft's clock, checked: timed to his words, inside one framing, clear of what's
    busy (cards, callouts, inserts: [{start, duration}]) and of a voice-over's window spans, before `total` (the close
    less its clear second), at most settings.EDIT_BEHIND_MAX, GAP apart. Bryan's asks first. Returns the placed ones
    and the ones to show on top instead (each with "why")."""
    most = min(settings.EDIT_BEHIND_MAX, 2)
    offsets = _offsets(cuts)
    placed, on_top = [], []
    for b in sorted(items, key=lambda b: (not b.get("asked"), b.get("role") != "hook")):
        b = dict(b)
        b["words"] = words_of(b.get("words"))
        if not b["words"]:
            continue

        def off(why, when=None):
            """Shown on top instead, at the time worked out so far (when: (start, end))."""
            on_top.append({k: v for k, v in b.items() if k not in ("start", "duration", "file", "check")}
                          | ({"start": when[0], "duration": round(when[1] - when[0], 3)} if when else {}) | {"why_not": why})
        hook = b.get("role") == "hook"
        at, cid = b.get("at"), b.get("clip_id")
        start = next((o + float(at) - c["start"] for c, o in zip(cuts, offsets)
                      if at is not None and c["clip_id"] == cid and c["start"] - 0.01 <= float(at) < c["end"]), None)
        if start is None and at is not None:  # its moment fell in a pause that was cut
            start = next((o for c, o in zip(cuts, offsets) if c["clip_id"] == cid and float(at) <= c["start"] <= float(at) + 1.5), None)
        if hook and (start is None or start < 3.0):
            start = 0.0
        if start is None:
            off("the moment it was for was cut")
            continue
        if not hook:
            start = editor._snap_start(start, words)
            if (hook_text or "").strip():
                start = max(start, editor.HOOK_SECONDS_SHOWN)
        start = frame_at(start)
        want = float(b.get("duration") or 2.5)
        end = min(_end_at(start, want, words), _stretch_end(cuts, start), total)
        end = frame_at(end)
        if end - start < SHORTEST - 0.01:
            off("the shot changes or the Reel ends too soon after it" if end < total else "there wasn't room before the end",
                (start, max(end, start + SHORTEST)))
            continue
        if len(placed) >= most:
            off(f"Studio keeps to {most} in a Reel", (start, end))
            continue
        if any(start < o["start"] + o["duration"] + 0.3 and end > o["start"] - 0.3 for o in busy):
            off("a card or an animation is up then", (start, end))
            continue
        if any(start < s1 and end > s0 for s0, s1 in window):
            off("you're in the small window there", (start, end))
            continue
        if any(start < p["start"] + p["duration"] + GAP and end > p["start"] - GAP for p in placed):
            off("it came too soon after the other words behind you", (start, end))
            continue
        placed.append({k: v for k, v in b.items() if k not in ("why_not", "file", "check", "start", "duration")}
                      | {"start": start, "duration": round(end - start, 3)})
    return sorted(placed, key=lambda p: p["start"]), on_top


def on_top(items: list[dict], cards: list[dict], total: float) -> tuple[list[dict], str, list[str]]:
    """The words that couldn't go behind him, shown the usual way: a key line as a callout (when it doesn't clash with
    a card), the hook as the hook. Returns the callouts, the hook's words ("" when none) and what was done."""
    callouts, hook, notes = [], "", []
    for b in items:
        why = b.get("why_not") or "it couldn't be made"
        if b.get("role") == "hook":
            hook = b["words"]
            notes.append(f"showed \"{b['words']}\" on top as the hook instead of behind you: {why}")
            continue
        start = b.get("start")
        if start is None:
            notes.append(f"left out the words \"{b['words']}\": {why}")
            continue
        length = min(max(float(b.get("duration") or 2.0), 1.2), 4.0, total - start)
        if length < 1.0 or any(start < c["start"] + c["duration"] + 0.3 and start + length > c["start"] - 0.3
                               for c in cards + callouts):
            notes.append(f"left out the words \"{b['words']}\": {why}, and there was no room for them on top")
            continue
        callouts.append({"clip_id": b.get("clip_id"), "at": b.get("at"), "style": "callout", "start": round(start, 3),
                         "duration": round(length, 3), "title": b["words"], "italic": b.get("italic") or "",
                         "lines": [], "scene": "", "look": "cutaway", "role": "explain",
                         "why": "words behind you, shown on top"})
        notes.append(f"showed \"{b['words']}\" on top instead of behind you: {why}")
    return callouts, hook, notes


# ── The person, frame by frame ────────────────────────────────────────────────

def read_frames(video: Path, start: float, n: int, size: tuple[int, int] | None = None) -> list[np.ndarray]:
    """n frames of the Reel from start (on the frame grid), as RGB arrays (size: (w, h), else the Reel's)."""
    W, H = size or (editor.W, editor.H)
    cmd = [extract.resolve(settings.FFMPEG), "-v", "error", "-ss", f"{max(start - 0.5 / editor.FPS, 0.0):.4f}",
           "-i", str(video), "-an", "-frames:v", str(n), "-vf", f"scale={W}:{H}",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    result = subprocess.run(cmd, capture_output=True, creationflags=getattr(extract, "_NO_WINDOW", 0))
    if result.returncode != 0:
        raise RuntimeError("ffmpeg couldn't read the picture: " + result.stderr.decode("utf-8", "replace").strip()[-200:])
    data = np.frombuffer(result.stdout, dtype=np.uint8)
    k = len(data) // (W * H * 3)
    frames = [data[i * W * H * 3:(i + 1) * W * H * 3].reshape(H, W, 3) for i in range(k)]
    while frames and len(frames) < n:  # the picture ended a frame early: its last frame again
        frames.append(frames[-1])
    return frames


def find(frames: list[np.ndarray], seg: Segmenter) -> tuple[np.ndarray, np.ndarray | None, np.ndarray]:
    """Each frame's person and face (WORK size, 0..1) and the frame in grey (WORK size, 0..1)."""
    import cv2
    people, faces, greys = [], [], []
    for f in frames:
        p, fc = seg.person(f)
        people.append(cv2.resize(np.asarray(p, np.float32), (WORK_W, WORK_H), interpolation=cv2.INTER_LINEAR))
        faces.append(None if fc is None else cv2.resize(np.asarray(fc, np.float32), (WORK_W, WORK_H),
                                                        interpolation=cv2.INTER_LINEAR))
        greys.append(cv2.resize(cv2.cvtColor(f, cv2.COLOR_RGB2GRAY), (WORK_W, WORK_H),
                                interpolation=cv2.INTER_AREA).astype(np.float32) / 255.0)
    face = np.stack(faces) if faces and all(x is not None for x in faces) else None
    return np.clip(np.stack(people), 0, 1), face, np.stack(greys)


def smooth(raw: np.ndarray) -> np.ndarray:
    """Each frame's matte as the weighted mean of its neighbours (SMOOTH): no flicker, no lag (the whole stretch is
    known before it's drawn)."""
    n, half = len(raw), len(SMOOTH) // 2
    out = np.empty_like(raw)
    total = float(sum(SMOOTH))
    for i in range(n):
        acc = np.zeros_like(raw[0])
        for k, wt in enumerate(SMOOTH):
            acc += wt * raw[min(max(i + k - half, 0), n - 1)]
        out[i] = acc / total
    return out


def fill_holes(m: np.ndarray) -> np.ndarray:
    """A small gap the finder left inside him (a shadow on a cheek the colour of the wall) filled: background wholly
    enclosed by him and under HOLE_MAX of the frame. A bigger one (an arm on a hip) is real background and stays."""
    import cv2
    inv = (m <= 0.5).astype(np.uint8)
    n, lab, st, _ = cv2.connectedComponentsWithStats(inv, connectivity=4)
    if n <= 2:
        return m
    edge = np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]]))
    small = np.zeros(n, bool)
    small[1:] = st[1:, cv2.CC_STAT_AREA] < HOLE_MAX * m.size
    small[edge] = False
    return np.maximum(m, small[lab].astype(np.float32)) if small.any() else m


def guided(guide: np.ndarray, src: np.ndarray, r: int = GUIDE_R, eps: float = GUIDE_EPS) -> np.ndarray:
    """A guided filter (He et al.): src's edges moved onto the guide's (the picture's), at WORK size."""
    import cv2
    box = lambda x: cv2.boxFilter(x, -1, (2 * r + 1, 2 * r + 1))  # noqa: E731
    mi, mp = box(guide), box(src)
    a = (box(guide * src) - mi * mp) / (box(guide * guide) - mi * mi + eps)
    b = mp - a * mi
    return np.clip(box(a) * guide + box(b), 0, 1)


def confidence(raw: np.ndarray, face: np.ndarray | None, greys: np.ndarray) -> tuple[str | None, dict]:
    """Is the person found well enough over the stretch? None, or why not in plain words; and the measures."""
    import cv2
    solid = raw > 0.5
    n = len(solid)
    area = solid.reshape(n, -1).mean(1)
    stats = {"frames": n, "area": round(float(np.median(area)), 3)}
    if (area >= MIN_AREA).mean() < PRESENT:
        return ("Studio couldn't find you clearly in the picture" if np.median(area) < MIN_AREA / 2
                else "you're too small or not in the picture all through"), stats
    ious = [float((a & b).sum() / max((a | b).sum(), 1)) for a, b in zip(solid, solid[1:])] or [1.0]
    swing = float(area.std() / max(area.mean(), 1e-6))
    stats.update(steady=round(float(np.median(ious)), 3), worst=round(min(ious), 3), swing=round(swing, 3))
    if np.median(ious) < STEADY_IOU or min(ious) < WORST_IOU or swing > AREA_SWING:
        return "Studio wasn't sure of your outline there (it kept changing)", stats
    mean = (solid.mean(0) > 0.5).astype(np.uint8)
    count, _, comp, _ = cv2.connectedComponentsWithStats(mean, connectivity=8)
    sizes = sorted((int(comp[k, cv2.CC_STAT_AREA]) for k in range(1, count)), reverse=True)
    people = sum(1 for s in sizes if sizes and s >= SECOND_PERSON * sizes[0] and s >= 0.01 * mean.size)
    faces_seen = 0
    if face is not None:
        fmask = (face.mean(0) > 0.45).astype(np.uint8)
        fc, _, fstats, _ = cv2.connectedComponentsWithStats(fmask, connectivity=8)
        fsizes = sorted((int(fstats[k, cv2.CC_STAT_AREA]) for k in range(1, fc)), reverse=True)
        faces_seen = sum(1 for s in fsizes if s >= 0.3 * fsizes[0] and s >= 0.0015 * fmask.size)
    stats.update(people=people, faces=faces_seen)
    if people > 1 or faces_seen > 1:
        return "more than one person is in the picture there", stats
    if face is not None:
        seen = float(((face > 0.5).reshape(n, -1).mean(1) > 0.0015).mean())
        stats["face_seen"] = round(seen, 3)
        if seen < FACE_SEEN:
            return "Studio couldn't see your face clearly there", stats
    steps, scale = [], editor.W / WORK_W
    win = cv2.createHanningWindow((WORK_W, WORK_H), cv2.CV_32F)
    for a, b in zip(greys[::2], greys[2::2]):
        (dx, dy), _ = cv2.phaseCorrelate(a.copy(), b.copy(), win)  # it windows its inputs in place
        steps.append(math.hypot(dx, dy) * scale / 2)
    move = float(np.median(steps)) if steps else 0.0
    path = float(sum(steps) * 2)
    stats.update(camera_step=round(move, 1), camera_path=round(path, 1))
    if move > CAMERA_STEP or path > CAMERA_PATH:
        return "the camera moves too much there", stats
    return None, stats


# ── The words ─────────────────────────────────────────────────────────────────

def display_font(look: dict) -> Path:
    """The look's display face (Site Notes: Archivo, its heaviest; the gallery: the serif; classic: the caption face)."""
    titles = (look or {}).get("titles") or editor.title_style()
    if titles == "site":
        return editor.SITE_FONTS[max(editor.SITE_FONTS)]
    if titles == "gallery":
        serif = editor.TITLE_STYLES["gallery"].get("serif")
        return editor.TITLE_FONTS[serif][1]
    faces = editor.caption_font((look or {}).get("caption_font"))[1]
    return faces[-1][0]


def palette(brand: str) -> dict:
    """The words' colours from the look's theme (editor.THEME): light words, the key word in the accent (brass), dark
    words (the labels' colour) over a light background."""
    theme = editor.THEME.get(brand) or editor.THEME["urban_makers"]

    def rgb(v):
        v = str(v)
        if v.startswith("#"):
            return tuple(int(v[i:i + 2], 16) for i in (1, 3, 5))
        return tuple(int(x) for x in v.split(","))
    return {"light": (255, 255, 255), "dark": rgb(theme.get("label-rgb", "18,17,14")),
            "accent": rgb(theme.get("brass") or theme.get("accent")), "accent_ink": rgb(theme.get("brass-ink", "#16140F"))}


def _font(path: Path, px: int):
    from PIL import ImageFont
    try:
        return ImageFont.truetype(str(path), px)
    except OSError:
        return ImageFont.load_default(px)


def line_options(words: str, font_path: Path, most: int = 4) -> list[tuple[list[str], int]]:
    """The words in 1 to 3 lines and the size each split allows (as big as fits the frame's safe width with the pop's
    overshoot, MAX_PX, and HEIGHT of the safe area), the best few first: fewer lines and bigger words."""
    parts = words.split()
    width = (editor.W - 2 * SAFE_X) / POP_OVER
    f = _font(font_path, 200)
    out = []
    for n in range(1, min(3, len(parts)) + 1):
        for lines in _splits(parts, n):
            widest = max(f.getlength(x) for x in lines) or 1
            px = int(min(MAX_PX, 200 * width / widest, (SAFE_BOTTOM - SAFE_TOP) * HEIGHT / (n * 0.92 + 0.5)))
            out.append((px - (n - 1) * 30, lines, max(px, 60)))
    out.sort(key=lambda o: -o[0])
    return [(lines, px) for _, lines, px in out[:most]]


def _splits(parts: list[str], n: int) -> list[list[str]]:
    if n == 1:
        return [[" ".join(parts)]]
    out = []
    for k in range(1, len(parts) - n + 2):
        for rest in _splits(parts[k:], n - 1):
            out.append([" ".join(parts[:k])] + rest)
    return out


def words_image(lines: list[str], px: int, italic: str, look: dict, brand: str, dark: bool):
    """The words as one RGBA picture (lines centred, the key word in the accent, a soft shadow under light words), and
    each word's box in it: [(x0, y0, x1, y1, is the key word)]."""
    from PIL import Image, ImageDraw, ImageFilter
    font = _font(display_font(look), px)
    pal = palette(brand)
    ink = pal["dark"] if dark else pal["light"]
    lead = round(px * 0.92)
    width = int(max(font.getlength(x) for x in lines)) + 2
    pad = round(px * 0.25)
    img = Image.new("RGBA", (width + 2 * pad, lead * len(lines) + round(px * 0.25) + 2 * pad), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    key = editor._core(italic).lower() if italic else ""
    space = font.getlength(" ")
    boxes = []
    for i, line in enumerate(lines):
        x = pad + (width - font.getlength(line)) / 2
        y = pad + i * lead + px * 0.86
        for w in line.split(" "):
            is_key = bool(key) and editor._core(w).lower() == key
            d.text((x, y), w, font=font, fill=(*(pal["accent"] if is_key else ink), 255), anchor="ls")
            x0, y0, x1, y1 = font.getbbox(w, anchor="ls")
            boxes.append((round(x + x0), round(y + y0), round(x + x1), round(y + y1), is_key))
            x += font.getlength(w) + space
    if not dark:  # a soft shadow keeps light words clear of a busy wall, under them only
        alpha = img.getchannel("A")
        shade = Image.new("RGBA", img.size, (*pal["dark"], 0))
        shade.putalpha(alpha.point(lambda v: v * 0.42).filter(ImageFilter.GaussianBlur(px * 0.06)))
        out = Image.new("RGBA", img.size, (0, 0, 0, 0))
        out.alpha_composite(shade, (0, round(px * 0.03)))
        out.alpha_composite(img)
        img = out
    return img, boxes


def head_of(person: np.ndarray, face: np.ndarray | None) -> dict | None:
    """His head on the Reel (px): the top of him, and the face's box (the face the model found; without one, the top
    of the person)."""
    sx, sy = editor.W / WORK_W, editor.H / WORK_H
    solid = person > 0.5
    rows = np.nonzero(solid.any(1))[0]
    if not len(rows):
        return None
    top = int(rows[0])
    if face is not None and (face > 0.5).any():
        ys, xs = np.nonzero(face > 0.5)
    else:
        band = solid[top:top + max(4, int((rows[-1] - top) * 0.18))]
        ys, xs = np.nonzero(band)
        ys = ys + top
    return {"top": round(top * sy), "x0": round(xs.min() * sx), "x1": round((xs.max() + 1) * sx),
            "y0": round(ys.min() * sy), "y1": round((ys.max() + 1) * sy)}


def layout(ink: np.ndarray, boxes: list, person: np.ndarray, head: dict) -> tuple[tuple[int, int] | None, float, float]:
    """Where one set of lines goes (top left, Reel px): centred, inside the safe area, crossing his head's height (some
    of the words behind him), every word at least WORD_MIN showing (the key word KEY_MIN) and VISIBLE_MIN of them in
    all; then about BEHIND_AIM of them behind his head (the look), his head well into them. ink: the words' alpha (full size). Returns
    (x, y) or None, the share showing and the least-shown word's share."""
    import cv2
    W, H = editor.W, editor.H
    sx, sy = W / WORK_W, H / WORK_H
    solid = person > 0.5
    bh, bw = ink.shape
    x = round((W - bw) / 2)
    small = cv2.resize(ink, (max(1, round(bw / sx)), max(1, round(bh / sy))), interpolation=cv2.INTER_AREA)
    total = float(small.sum()) or 1.0
    parts = [(slice(round(b[1] / sy), max(round(b[3] / sy), round(b[1] / sy) + 1)),
              slice(round(b[0] / sx), max(round(b[2] / sx), round(b[0] / sx) + 1)), b[4]) for b in boxes]
    tall = max(head["y1"] - head["top"], 1)
    best, best_score = (None, 0.0, 0.0), -9.0
    for y in range(SAFE_TOP, SAFE_BOTTOM - bh + 1, 12):
        if not (y + bh >= head["top"] + 0.3 * tall and y <= head["top"] + 0.6 * tall):  # across his head, not his neck
            continue
        x0, y0 = round(x / sx), round(y / sy)
        cover = solid[y0:y0 + small.shape[0], x0:x0 + small.shape[1]]
        if cover.shape != small.shape:
            continue
        seen = small * (~cover)
        showing = float(seen.sum() / total)
        shares = [float(seen[r, c].sum() / max(small[r, c].sum(), 1e-6)) for r, c, _ in parts]
        worst = min(shares) if shares else 0.0
        fine = (showing >= VISIBLE_MIN and showing <= 1 - BEHIND_MIN and worst >= WORD_MIN
                and all(s >= KEY_MIN for s, (_, _, k) in zip(shares, parts) if k))
        depth = min(max((y + bh - head["top"]) / tall, 0.0), 0.6)
        # Fine ones first; among them about BEHIND_AIM of the words behind him, his head well into them.
        score = (2.0 - abs(1 - showing - BEHIND_AIM) + 0.1 * depth) if fine else showing
        if score > best_score:
            best, best_score = ((x, y) if fine else None, showing, worst), score
    return best[0], round(best[1], 3), round(best[2], 3)


def choose(words: str, italic: str, look: dict, brand: str, person: np.ndarray, face: np.ndarray | None) -> dict:
    """The words' lines, size and place over this stretch (its mean mattes): the first split, biggest first, that fits
    around him (layout). {lines, px, at, showing, worst, head}; at None when nothing fits."""
    head = head_of(person, face)
    out = {"lines": [], "px": 0, "at": None, "showing": 0.0, "worst": 0.0, "head": head}
    if head is None:
        return out
    for lines, px in line_options(words, display_font(look)):
        img, boxes = words_image(lines, px, italic, look, brand, dark=False)
        ink = np.asarray(img.getchannel("A"), np.float32) / 255.0
        at, showing, worst = layout(ink, boxes, person, head)
        if at is not None:
            return {"lines": lines, "px": px, "at": at, "showing": showing, "worst": worst, "head": head,
                    "size": img.size}
        if showing > out["showing"]:
            out.update(lines=lines, px=px, showing=showing, worst=worst)
    return out


# ── The layer ─────────────────────────────────────────────────────────────────

def _ease_back(x: float) -> float:
    x = min(max(x, 0.0), 1.0)
    c = 1.4
    return 1 + (c + 1) * (x - 1) ** 3 + c * (x - 1) ** 2


def pop(t: float, duration: float) -> tuple[float, float]:
    """(scale, opacity) of the words at t: they pop up together (a quick grow with a little overshoot), hold, and fade."""
    scale = POP_FROM + (1 - POP_FROM) * _ease_back(t / POP_IN)
    alpha = min(1.0, t / SHOW_IN)
    left = duration - t
    if left < FADE_OUT:
        k = max(left, 0.0) / FADE_OUT
        alpha *= k
        scale += OUT_GROW * (1 - k)
    return scale, max(0.0, min(alpha, 1.0))


def key(b: dict, frames: list[np.ndarray], look: dict, brand: str, seg_name: str) -> str:
    """The layer's cache key: the words, their look, the stretch's own frames (three of them), the finder."""
    h = hashlib.sha256()
    for f in (frames[0], frames[len(frames) // 2], frames[-1]):
        h.update(f[::4, ::4].tobytes())
    blob = json.dumps({"w": b["words"], "i": b.get("italic") or "", "d": round(b["duration"], 3), "n": len(frames),
                       "look": (look or {}).get("titles"), "brand": brand, "v": LOOK, "seg": seg_name,
                       "frames": h.hexdigest()}, sort_keys=True)
    return "behind-" + hashlib.sha256(blob.encode()).hexdigest()[:24]


def render_one(b: dict, frames: list[np.ndarray], out: Path, look: dict, brand: str, seg: Segmenter) -> dict:
    """One stretch's layer as a transparent VP9 WebM (editor.W x H at FPS, one frame per picture frame): the words with
    his shape cut out. Raises ValueError (plain words) when the person isn't found well enough or the words don't
    fit around him. Returns what it measured."""
    import cv2
    W, H, FPS = editor.W, editor.H, editor.FPS
    raw, face_raw, greys = find(frames, seg)
    why, stats = confidence(raw, face_raw, greys)
    if why:
        raise ValueError(why)
    soft = smooth(raw)
    soft = np.stack([fill_holes(guided(g, m)) for g, m in zip(greys, soft)])
    face = smooth(face_raw) if face_raw is not None else None
    mean_person, mean_face = soft.mean(0), (face.mean(0) if face is not None else None)
    plan = choose(b["words"], b.get("italic", ""), look, brand, mean_person, mean_face)
    if plan["at"] is None:
        raise ValueError("you fill too much of the picture there for words behind you"
                         if plan["head"] else "Studio couldn't find your head there")
    x, y = plan["at"]
    bw, bh = plan["size"]
    # Light or dark words: how light the background is where they go (his shape left out).
    sx, sy = WORK_W / W, WORK_H / H
    region = (slice(max(0, round(y * sy)), round((y + bh) * sy)), slice(max(0, round(x * sx)), round((x + bw) * sx)))
    bg = greys[0][region][mean_person[region] < 0.5]
    light = float(np.median(bg)) if bg.size else 0.4
    img, _ = words_image(plan["lines"], plan["px"], b.get("italic", ""), look, brand, dark=light > BRIGHT)
    words = np.asarray(img, np.float32)
    a_src = words[..., 3] / 255.0
    rgb_src = words[..., :3] * a_src[..., None]  # premultiplied, so the edges don't darken as they're scaled
    # The box the words can reach (with the pop's overshoot and the end's grow), clipped to the frame.
    grow = max(POP_OVER, 1 + OUT_GROW)
    cx, cy = x + bw / 2, y + bh / 2
    X0, X1 = max(0, int(cx - bw * grow / 2) - 4), min(W, int(math.ceil(cx + bw * grow / 2)) + 4)
    Y0, Y1 = max(0, int(cy - bh * grow / 2) - 4), min(H, int(math.ceil(cy + bh * grow / 2)) + 4)
    rw, rh = X1 - X0, Y1 - Y0
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * GROW + 1, 2 * GROW + 1))
    guard_k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * FACE_GUARD + 1, 2 * FACE_GUARD + 1))
    cmd = [extract.resolve(settings.FFMPEG), "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgba",
           "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-b:v", "0",
           "-crf", "18", "-deadline", "realtime", "-cpu-used", "8", "-row-mt", "1", "-auto-alt-ref", "0", "-an", str(out)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    shown = []
    frame = np.zeros((H, W, 4), np.uint8)
    try:
        for i in range(len(frames)):
            scale, opacity = pop(i / FPS, b["duration"])
            m = np.float32([[scale, 0, cx - X0 - scale * bw / 2], [0, scale, cy - Y0 - scale * bh / 2]])
            rgb = cv2.warpAffine(rgb_src, m, (rw, rh), flags=cv2.INTER_LINEAR, borderValue=0)
            alpha = cv2.warpAffine(a_src, m, (rw, rh), flags=cv2.INTER_LINEAR, borderValue=0)
            rgb = rgb / np.maximum(alpha, 1e-4)[..., None]
            # His shape at full size, only where the words are: a firm core, a hair grown, a feathered edge.
            p = cv2.resize(soft[i], (W, H), interpolation=cv2.INTER_LINEAR)[Y0:Y1, X0:X1]
            p = np.clip((p - EDGE_LO) / (EDGE_HI - EDGE_LO), 0, 1)
            p = p * p * (3 - 2 * p)
            p = cv2.GaussianBlur(cv2.dilate(p, kernel), (0, 0), FEATHER)
            if face is not None:  # his face, found apart, never under words
                g = cv2.dilate((face[i] > 0.4).astype(np.float32), guard_k)
                g = cv2.resize(g, (W, H), interpolation=cv2.INTER_LINEAR)[Y0:Y1, X0:X1]
                p = np.maximum(p, g)
            a = alpha * opacity * (1 - p)
            if alpha.sum() > 1:
                shown.append(float((alpha * (1 - p)).sum() / alpha.sum()))
            frame[:] = 0
            frame[Y0:Y1, X0:X1, :3] = np.clip(rgb, 0, 255).astype(np.uint8)
            frame[Y0:Y1, X0:X1, 3] = np.clip(a * 255 + 0.5, 0, 255).astype(np.uint8)
            proc.stdin.write(frame.tobytes())
        proc.stdin.close()
        err = proc.stderr.read().decode("utf-8", "replace")
        if proc.wait() != 0 or not out.exists():
            raise RuntimeError("ffmpeg couldn't encode the words behind you: " + err.strip()[-300:])
    except BaseException:
        proc.kill()
        proc.wait()
        out.unlink(missing_ok=True)
        raise
    return {**stats, "showing": round(float(np.median(shown)) if shown else plan["showing"], 3), "worst_word": plan["worst"],
            "box": [X0, Y0, X1, Y1], "words_at": [x, y, x + bw, y + bh], "head": plan["head"], "lines": plan["lines"],
            "px": plan["px"], "dark": light > BRIGHT}


def make(items: list[dict], picture: Path, look: dict, brand: str) -> tuple[list[dict], list[dict], dict]:
    """Each placed stretch made into its layer (kept in editor.INSERTS by key, so a re-render of the same picture
    reuses it). Returns the ones made (each with "file" and "check": what was measured), the ones to show on top
    instead (each with "why_not"), and the tally {made, reused, cpu_seconds, effect_seconds}."""
    done, on_top_ = [], []
    tally = {"made": 0, "reused": 0, "cpu_seconds": 0.0, "effect_seconds": 0.0}
    if not items:
        return done, on_top_, tally
    why = available()
    try:
        seg = None if why else segmenter()
    except Exception as e:
        seg, why = None, f"Studio couldn't load the person finder ({e})"
    editor.INSERTS.mkdir(parents=True, exist_ok=True)
    for b in items:
        if why:
            on_top_.append({**b, "why_not": why})
            continue
        t0, c0 = time.time(), _cpu()
        try:
            n = max(1, round(b["duration"] * editor.FPS))
            frames = read_frames(picture, b["start"], n, READ)
            k = key(b, frames, look, brand, seg.name)
            out = editor.INSERTS / f"{k}.webm"
            meta = editor.INSERTS / f"{k}.json"
            if out.exists() and out.stat().st_size > 0 and meta.exists():
                out.touch()
                check = json.loads(meta.read_text())
                tally["reused"] += 1
            else:
                part = editor.INSERTS / f"{k}.part.webm"
                check = render_one(b, frames, part, look, brand, seg)
                part.replace(out)
                meta.write_text(json.dumps(check))
                tally["made"] += 1
                used = _cpu() - c0
                tally["cpu_seconds"] += used
                tally["effect_seconds"] += b["duration"]
                check = {**check, "cpu_per_second": round(used / max(b["duration"], 0.01), 1),
                         "seconds": round(time.time() - t0, 1)}
            done.append({**b, "file": k, "check": check})
        except ValueError as e:
            on_top_.append({**b, "why_not": str(e)})
        except Exception as e:  # a draft with the words on top beats no draft
            log.warning("Words behind you \"%s\" didn't render: %s: %s", b.get("words"), type(e).__name__, e)
            on_top_.append({**b, "why_not": "it didn't render"})
    tally["cpu_seconds"] = round(tally["cpu_seconds"], 1)
    tally["effect_seconds"] = round(tally["effect_seconds"], 2)
    return done, on_top_, tally


def _cpu() -> float:
    """CPU seconds this process and the ffmpeg it waited for have used."""
    t = os.times()
    return t.user + t.system + t.children_user + t.children_system


def for_resolve(edl: dict) -> list[dict]:
    """The layers as inserts for the Resolve project's animations track (only ones still kept)."""
    return [{"style": STYLE, "file": b["file"], "start": b["start"], "duration": b["duration"], "title": b["words"]}
            for b in (edl or {}).get("behind") or [] if b.get("file") and (editor.INSERTS / f"{b['file']}.webm").exists()]


# ── In the editor's render ────────────────────────────────────────────────────

def run(edl: dict, cuts: list[dict], picture: Path, words: list[dict], clear_by: float, busy: list[dict],
        window: list, look: dict, brand: str) -> tuple[list[tuple[Path, float]], list[dict], list[str]]:
    """Words behind him for one render (editor._render): the plan's (edl["behind_plan"], a new plan) or the draft's own
    (edl["behind"], a re-render or a fix round), placed, made, and what couldn't be shown on top instead. Sets
    edl["behind"] (and edl["behind_tally"]); a hook shown behind him leaves edl["hook_text"] empty (kept in the item,
    so taking it off puts the hook back). Returns the layers for the mix, callouts to add to the cards, and what was
    done in plain words."""
    items = edl.pop("behind_plan", None)
    if items is None:
        items = [{k: v for k, v in b.items() if k not in ("file", "check")} for b in edl.get("behind") or []]
    hooked = next((b.get("hook_text") for b in items if b.get("role") == "hook" and b.get("hook_text")), None)
    if hooked and not (edl.get("hook_text") or "").strip():
        edl["hook_text"] = hooked  # a re-render: the hook's words, until they're behind him again
    if not items:
        edl.pop("behind", None)
        edl.pop("behind_tally", None)
        return [], [], []
    hook_text = edl.get("hook_text") or ""
    hook_behind = [b for b in items if b.get("role") == "hook"]
    placed, off = place(items, cuts, clear_by, words, "" if hook_behind else hook_text, busy, window)
    done, failed, tally = make(placed, picture, look, brand)
    callouts, hook_words, notes = on_top(off + failed, busy, clear_by)
    for b in done:
        if b.get("role") == "hook":
            b["hook_text"] = hook_text or b["words"]
            b["hook_italic"] = edl.get("hook_italic") or ""
            edl["hook_text"], edl["hook_italic"] = "", ""
    if hook_words and not (edl.get("hook_text") or "").strip():
        edl["hook_text"] = hook_words
    if done:
        edl["behind"] = done
        edl["behind_tally"] = tally
        notes.insert(0, "put " + ", ".join(f"\"{b['words']}\"" for b in done) + f" behind you ({_cost(tally)})")
    else:
        edl.pop("behind", None)
        edl.pop("behind_tally", None)
    return [(editor.INSERTS / f"{b['file']}.webm", b["start"]) for b in done], callouts, notes


def _cost(tally: dict) -> str:
    if tally.get("effect_seconds"):
        return f"{tally['cpu_seconds'] / tally['effect_seconds']:.0f} s of work a second"
    return "made before, reused"


def summary(edl: dict) -> str:
    """For "What the editor did"."""
    items = (edl or {}).get("behind") or []
    if not items:
        return ""
    parts = [f"{b['start']:.0f}-{b['start'] + b['duration']:.0f} s: \"{b['words']}\"" for b in items]
    return (f"Studio put {'words' if len(items) == 1 else 'two lines of words'} behind you, so you stand in front of "
            f"them ({'; '.join(parts)}).")


def listed(edl: dict) -> list[dict]:
    """The draft's words behind him, for the read and Review: B1, B2..."""
    return [{"n": k, "start": b["start"], "end": round(b["start"] + b["duration"], 2), "words": b["words"],
             "role": b.get("role", "explain"), "asked": bool(b.get("asked"))}
            for k, b in enumerate((edl or {}).get("behind") or [], 1)]


def read_line(edl: dict) -> str:
    """The read's material: what's behind him, on purpose."""
    items = listed(edl)
    if not items:
        return ""
    return ("Words behind Bryan (numbered: B1 is item 1; big words in the background that he stands in front of, on "
            "purpose, as the hook or a key line): " + "; ".join(
                f"B{x['n']} {x['start']:.1f}-{x['end']:.1f}s \"{x['words']}\"" + (" (the hook)" if x["role"] == "hook" else "")
                + (" (Bryan asked for it)" if x["asked"] else "") for x in items))


# ── A fix round (fix_actions) ─────────────────────────────────────────────────

def patch(edl: dict, draft, a: dict) -> str:
    """The read's action "behind": field "remove" takes one off (item, B2 = 2) or all (item null, and keeps them off
    for this post); field "" puts `to` (the words) behind him from start to end. Raises ValueError (plain words)."""
    items = edl.setdefault("behind", [])
    if a.get("field") == "remove":
        n = a.get("item")
        if not items:
            raise ValueError("there are no words behind you in this draft")
        if n is None:
            gone = list(items)
            edl["behind"] = []
            edl["behind_off"] = True
        elif isinstance(n, int) and 1 <= n <= len(items):
            gone = [items.pop(n - 1)]
        else:
            raise ValueError("the read named words behind you the draft doesn't have")
        for b in gone:
            if b.get("hook_text") and not (edl.get("hook_text") or "").strip():
                edl["hook_text"], edl["hook_italic"] = b["hook_text"], b.get("hook_italic") or ""
        return "took " + ", ".join(f"\"{b['words']}\"" for b in gone) + " from behind you" + (
            " (shown on top as the hook)" if any(b.get("hook_text") for b in gone) else "")
    if available():
        raise ValueError(available())
    text, italic = editor.title_parts(a.get("to") or "")
    words = words_of(text)
    if not words:
        raise ValueError("the read didn't say which words to put behind you")
    if len(items) >= min(settings.EDIT_BEHIND_MAX, 2):
        raise ValueError(f"Studio keeps to {min(settings.EDIT_BEHIND_MAX, 2)} in a Reel")
    start = a.get("start")
    if start is None:
        raise ValueError("the read didn't say when")
    start = max(float(start), 0.0)
    end = a.get("end")
    length = min(max((float(end) - start) if end is not None else 2.5, SHORTEST), LONGEST)
    hook = start < 0.5 and bool((edl.get("hook_text") or "").strip())
    src = draft.to_source(0.0 if hook else start)
    if not src:
        raise ValueError("there's no footage there")
    if any(start < b["start"] + b["duration"] + GAP and start + length > b["start"] - GAP for b in items):
        raise ValueError("there are words behind you there already")
    edl.pop("behind_off", None)
    items.append({"clip_id": src[0], "at": round(src[1], 3), "start": round(start, 3), "duration": round(length, 3),
                  "words": words, "italic": italic, "role": "hook" if hook else "explain",
                  "why": "Studio's read asked for it", "asked": True})
    items.sort(key=lambda b: b["start"])
    return f"put \"{words}\" behind you at {int(start // 60)}:{int(start % 60):02d}"


def reword_hook(edl: dict, text: str, italic: str = "") -> str | None:
    """The read's hook_text patch when the hook is behind him: the words behind him change (or go, with the hook,
    for ""), not a second hook on top. Returns what was done, or None when the hook isn't behind him."""
    items = edl.get("behind") or []
    hook = next((b for b in items if b.get("role") == "hook"), None)
    if hook is None:
        return None
    if not text:
        items.remove(hook)
        return "took the hook (behind you) off"
    hook.update(words=words_of(text), italic=italic, hook_text=text, hook_italic=italic)
    return f"the hook behind you now reads \"{hook['words']}\""


# ── The watch-through (final_check) ───────────────────────────────────────────

def alpha_frames(file: Path, fps: int, size: tuple[int, int]) -> np.ndarray:
    """A layer's alpha, small and fps times a second (frame k at k / fps from its start)."""
    w, h = size
    cmd = [extract.resolve(settings.FFMPEG), "-v", "error", "-c:v", "libvpx-vp9", "-i", str(file), "-an",
           "-vf", f"fps={fps},alphaextract,scale={w}:{h}", "-f", "rawvideo", "-pix_fmt", "gray", "-"]
    result = subprocess.run(cmd, capture_output=True, creationflags=getattr(extract, "_NO_WINDOW", 0))
    if result.returncode != 0:
        raise RuntimeError("ffmpeg couldn't read the layer: " + result.stderr.decode("utf-8", "replace").strip()[-200:])
    data = np.frombuffer(result.stdout, dtype=np.uint8)
    n = len(data) // (w * h)
    return data[:n * w * h].reshape(n, h, w)

