"""
Voice-over Reels (Bryan, 2026-10-21: "I want to begin filming video / b roll on site then come back and
voiceover. Filming speech on site is just too logistically heavy." And: "I will do a talking head in my studio
that you'll then cut and place in a part of the screen to show that it's still me. Sometimes we'll do on site but
i'll leave it to you to recognise that.")

  1. Recognising the footage (recognise, mode), from what Studio already has, no Claude call: a clip is a
     talking head when a face looks at the camera in most of its stills (the stills taken at ingest, OpenCV's
     face finder) and someone talks most of the time (the transcript); B-roll when hardly anything is said; a
     voice with no face to camera (a narrated walk-through) is "narrated". A talking head is a studio recording
     when enough of these agree: clean sound (the quality check's voice-to-background gap), a still camera
     (measured on three short moments), the setting Claude tagged it with (a studio, an office; or a site),
     and when it was filmed against the B-roll (another day, or hours later). Kept in the clip's footage.json.
  2. A post whose footage is a studio talking head plus site B-roll is a voice-over (mode "on"; never with a
     talking head filmed on site, and never without B-roll: those edit exactly as before). The editor plans the
     talking head's cuts as always (they are the voice: voice_edges, join_guard, sentence_guard, the clipped-word
     check all apply to them, unchanged); each B-roll cut in the plan (split_plan) is laid over his voice from the
     next talking-head cut on, instead of stopping it.
  3. The picture (layout, render_picture): Bryan full screen for his first sentence (the hook) and his last (the
     close); in between, the site footage fills the screen and he stays in a rounded window, a third of the width,
     in a bottom corner above Instagram's caption area and beside the captions, on the side the footage is
     calmest (or the side Bryan asked for). Shots change at the ends of his sentences or clauses. A stretch he
     asked to see himself full screen (a note, the read's "layout" fix, the plan's "picture") is kept full
     screen. The site's own sound plays well under his voice (SITE_BELOW).
  4. The draft keeps it all (edl["voiceover"], edl["broll"], edl["pip_asks"], edl["pip"]): a re-render or a fix
     round lays the same B-roll over the same words; the Resolve project gets the B-roll shots on V2 and his
     window as a layer above them (resolve_layers).
"""
import json
import logging
import math
import re
import statistics
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

import editor
import extract
import settings

log = logging.getLogger("studio.voiceover")

FOOTAGE_VERSION = 1
# A talking head: someone talks this share of the clip at least (and says this many words), and a face looks at
# the camera in this share of its stills. B-roll: talking under BROLL_SPEECH of it, or fewer words than that.
TALK_SPEECH, TALK_WORDS, FACE_SHARE, BROLL_SPEECH = 0.45, 10, 0.5, 0.25
FACE_MIN = 0.07          # a face this wide (share of the frame's width) or more is talking to the camera
# Studio or site: clean sound (dB between the voice and the room), a still camera (how far the picture moves in
# a tenth of a second, as a share of its width), the setting's words, and when it was filmed against the B-roll.
CLEAN_SNR, NOISY_SNR = 25.0, 15.0
STILL_MOVE, SHAKY_MOVE = 0.0015, 0.005
APART_HOURS, TOGETHER_HOURS = 3.0, 1.0
STUDIO_WORDS = re.compile(r"\b(studio|office|desk|backdrop|plain (wall|background)|seamless|home office|indoor set|"
                          r"podcast|interview set)\b", re.I)
SITE_WORDS = re.compile(r"\b(site|construction|renovation|renovating|hacking|works?ite|building works|scaffold\w*|"
                        r"outdoors?|corridor|void deck|unit under|under renovation|bare (unit|flat)|demolition)\b", re.I)

# The window: 360 x 480 (a third of the width, 3:4), corners rounded like the gallery look's cards, a hairline and a
# soft shadow. Its bottom edge sits at 1500, above Instagram's own caption and buttons (the bottom ~380 px, and a
# column on the right): bottom-left at x 56, or bottom-right at x 600 (clear of the buttons' column). The captions
# move beside it while it's up.
PIP_W, PIP_H, PIP_RADIUS = 360, 480, 28
PIP_BOTTOM = 1500
PIP_X = {"left": 56, "right": editor.W - 120 - PIP_W}
PIP_Y = PIP_BOTTOM - PIP_H
PIP_GAP = 32             # between the window and the captions beside it
# Shots: at least SHOT_MIN, about the length Claude planned, at most SHOT_LONGEST (a longer one is split), or
# SHOT_STRETCH when one is stretched to close a short gap; a gap under GAP_FILL between two shots is closed.
SHOT_MIN, SHOT_LONGEST, SHOT_STRETCH, GAP_FILL = 1.2, 5.0, 6.5, 2.0
SNAP = 1.2               # a shot starts at the sentence or clause end nearest its words, within this
LAST_ROOM = 2.0          # a shot planned just before his last sentence starts early enough to have this long
PAUSE_EDGE = 0.35        # a pause this long between words is a clause end too (Whisper often writes no comma)
HOOK_SHORTEST, HOOK_LONGEST, CLOSE_LONGEST, MIDDLE_MIN = 1.5, 7.0, 6.0, 3.0
SITE_BELOW, SITE_FADE = 18.0, 0.2


# ── Recognising the footage ───────────────────────────────────────────────────

def _words(clip: dict) -> list[dict]:
    return editor._words(clip)


def _quality(clip: dict) -> dict:
    try:
        return json.loads(clip.get("quality") or "{}") or {}
    except (TypeError, ValueError):
        return {}


def _stills(clip: dict) -> list:
    """The clip's stills as small grey pictures: the ones taken at ingest (frames/, for Claude's tagging), else
    eight grabbed now."""
    import numpy as np
    from PIL import Image
    files = sorted((Path(clip["clip_dir"]) / "frames").glob("*.jpg"))
    out = []
    for f in files[:24]:
        try:
            out.append(np.asarray(Image.open(f).convert("L")))
        except OSError:
            continue
    if out:
        return out
    dur = float(clip.get("duration") or 0)
    with tempfile.TemporaryDirectory(prefix="studio-stills-") as tmp:
        for i in range(8):
            jpg = Path(tmp) / f"{i}.jpg"
            try:
                extract._run([settings.FFMPEG, "-y", "-v", "error", "-ss", f"{dur * (i + 0.5) / 8:.2f}", "-i",
                              clip["video_path"], "-frames:v", "1", "-vf", "scale=512:-2", str(jpg)])
                out.append(np.asarray(Image.open(jpg).convert("L")))
            except (RuntimeError, OSError):
                continue
    return out


def face_share(clip: dict) -> float | None:
    """The share of the clip's stills with a face looking at the camera (the frontal face finder; FACE_MIN wide
    or more). None when OpenCV can't look."""
    try:
        import cv2
        import framing
        frontal = framing._cascades()[0]
    except Exception as e:
        log.warning("No face finder for the footage check: %s", e)
        return None
    stills = _stills(clip)
    if not stills:
        return None
    seen = 0
    for g in stills:
        h, w = g.shape[:2]
        size = max(20, int(w * FACE_MIN))
        found = list(frontal.detectMultiScale(g, scaleFactor=1.15, minNeighbors=5, minSize=(size, size)))
        if not found:
            found = list(frontal.detectMultiScale(cv2.equalizeHist(g), scaleFactor=1.15, minNeighbors=5,
                                                  minSize=(size, size)))
        seen += bool(found)
    return round(seen / len(stills), 3)


def speech_share(clip: dict) -> tuple[float, int]:
    """(the share of the clip someone is talking, how many words), from the transcript."""
    words = _words(clip)
    dur = float(clip.get("duration") or 0) or (words[-1]["end"] if words else 0.0)
    if not words or dur <= 0:
        return 0.0, 0
    talk = sum(b - a for a, b in editor.speech_spans(words))
    return round(min(talk / dur, 1.0), 3), len(words)


def camera_move(clip: dict) -> float | None:
    """How much the picture moves in a tenth of a second (median, as a share of its width), at three moments
    (while talking, when there's talk): about 0 on a tripod, more held in the hand. None when it can't be read."""
    try:
        import cv2
        import numpy as np
    except ImportError:
        return None
    dur = float(clip.get("duration") or 0)
    if dur < 1.0:
        return None
    spans = editor.speech_spans(_words(clip)) or [(0.0, dur)]
    longest = sorted(spans, key=lambda s: s[1] - s[0], reverse=True)[:3]
    points = sorted((a + b) / 2 for a, b in longest) if longest[0][1] - longest[0][0] > 1.0 else \
        [dur * f for f in (0.25, 0.5, 0.75)]
    preview = Path(clip["clip_dir"]) / extract.PREVIEW_NAME
    source = str(preview if preview.exists() else clip["video_path"])
    sw, sh = framing_dims(clip)
    if not (sw and sh):
        return None
    w, h = 192, max(2, round(192 * sh / sw / 2) * 2)
    moves = []
    for t in points:
        try:
            raw = subprocess.run([settings.FFMPEG, "-v", "error", "-ss", f"{max(0.0, t - 0.4):.2f}", "-t", "0.8",
                                  "-i", source, "-an", "-vf", f"fps=10,scale={w}:{h},format=gray", "-f", "rawvideo",
                                  "-"], capture_output=True, timeout=120).stdout
        except (subprocess.SubprocessError, OSError):
            continue
        if len(raw) < 2 * w * h:
            continue
        frames = np.frombuffer(raw[:len(raw) // (w * h) * w * h], np.uint8).reshape(-1, h, w).astype(np.float32)
        win = cv2.createHanningWindow((w, h), cv2.CV_32F)
        for a, b in zip(frames, frames[1:]):
            (dx, dy), _ = cv2.phaseCorrelate(a, b, win)
            moves.append(math.hypot(dx, dy) / w)
    return round(statistics.median(moves), 5) if moves else None


def framing_dims(clip: dict) -> tuple[int, int]:
    try:
        import framing
        return framing._dims(clip)
    except Exception:
        return 0, 0


def _cache_key(clip: dict) -> str:
    video, transcript = Path(clip["video_path"]), Path(clip["clip_dir"]) / "transcript.json"
    size = video.stat().st_size if video.exists() else 0
    told = transcript.stat().st_mtime_ns if transcript.exists() else 0
    return f"{FOOTAGE_VERSION}:{clip['id']}:{video.name}:{size}:{told}:{clip.get('setting') or ''}:{clip.get('format') or ''}"


def recognise(clip: dict) -> dict:
    """What one clip is: {kind: talking_head | broll | narrated, where: studio | site | None (B-roll and narrated
    clips are site footage), score (the studio signals added up), signals, face, speech, words, why}. Kept beside
    the clip (footage.json) until the clip, its transcript or its tags change."""
    path = Path(clip["clip_dir"]) / "footage.json"
    key = _cache_key(clip)
    try:
        kept = json.loads(path.read_text(encoding="utf-8"))
        if kept.get("key") == key:
            return kept["footage"]
    except (OSError, ValueError, KeyError):
        pass
    speech, n = speech_share(clip)
    face = face_share(clip)
    told = clip.get("format") or ""
    if speech < BROLL_SPEECH or n < TALK_WORDS:
        kind = "broll"
        why = "hardly anything is said" if n else "nothing is said"
    elif speech >= TALK_SPEECH and (face is not None and face >= FACE_SHARE
                                    or face is not None and face >= FACE_SHARE / 2 and told == "talking_head"
                                    or face is None and told == "talking_head"):
        kind = "talking_head"
        why = f"a face to camera in {face:.0%} of it and talking {speech:.0%} of the time" if face is not None \
            else f"tagged a talking head, talking {speech:.0%} of the time"
    else:
        kind = "narrated"
        why = f"talking {speech:.0%} of the time, but no face to camera" if face is not None and face < FACE_SHARE / 2 \
            else f"some talking ({speech:.0%}), no steady face to camera"
    out = {"kind": kind, "face": face, "speech": speech, "words": n, "why": why, "where": None, "score": 0,
           "signals": {}}
    if kind == "talking_head":
        q = _quality(clip)
        snr = (q.get("audio") or {}).get("snr_db")
        codes = {f.get("code") for f in q.get("flags") or []}
        move = camera_move(clip)
        setting = f"{clip.get('setting') or ''} {clip.get('summary') or ''}"
        sig = {}
        sig["sound"] = (-1 if (snr is not None and snr < NOISY_SNR) or codes & {"very_noisy", "noisy_ok"}
                        else 1 if snr is not None and snr >= CLEAN_SNR else 0)
        sig["camera"] = 0 if move is None else 1 if move <= STILL_MOVE else -1 if move >= SHAKY_MOVE else 0
        sig["setting"] = -1 if SITE_WORDS.search(setting) else 1 if STUDIO_WORDS.search(setting) else 0
        out.update(signals=sig, snr=snr, move=move)
    try:
        path.write_text(json.dumps({"key": key, "footage": out}), encoding="utf-8")
    except OSError as e:
        log.warning("Couldn't keep the footage check for clip %s: %s", clip.get("id"), e)
    return out


def _when(clip: dict) -> datetime | None:
    raw = clip.get("recorded_at")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None


def _apart(talk: dict, broll: list[dict]) -> int:
    """+1 when the talking head was filmed on another day than all the B-roll, or hours after it; -1 when
    within the hour of some of it; 0 when the times aren't known."""
    t = _when(talk)
    times = [w for w in (_when(b) for b in broll) if w]
    if t is None or not times:
        return 0
    try:
        gaps = [abs((t - w).total_seconds()) / 3600 for w in times]
    except TypeError:  # one with a timezone, one without
        return 0
    if min(gaps) <= TOGETHER_HOURS:
        return -1
    return 1 if min(gaps) >= APART_HOURS or all(t.date() != w.date() for w in times) else 0


def _studio(rec: dict) -> bool:
    """A studio recording: the signals add up to 2 or more, with neither the sound nor the camera against it."""
    s = rec.get("signals") or {}
    return sum(s.values()) >= 2 and s.get("sound", 0) >= 0 and s.get("camera", 0) >= 0


def mode(clips: dict[int, dict]) -> dict:
    """Is this post a voice-over? {on, footage: {clip id: what it is (recognise), where it was filmed},
    studio, site, broll, narrated: [clip ids], why}. On when there's a talking head filmed in the studio and
    B-roll, and no talking head filmed on site (settings.EDIT_VOICEOVER; off = always the old way)."""
    footage = {}
    for cid, clip in clips.items():
        try:
            footage[cid] = dict(recognise(clip))
        except Exception as e:  # a clip that can't be looked at is edited the old way
            log.warning("Couldn't tell what clip %s is: %s: %s", cid, type(e).__name__, e)
            footage[cid] = {"kind": "unknown", "where": None, "why": "couldn't be checked", "signals": {}}
    broll = [cid for cid, f in footage.items() if f["kind"] == "broll"]
    narrated = [cid for cid, f in footage.items() if f["kind"] == "narrated"]
    for cid, f in footage.items():
        if f["kind"] == "talking_head":
            f["signals"] = {**f.get("signals", {}), "when": _apart(clips[cid], [clips[b] for b in broll + narrated])}
            f["score"] = sum(f["signals"].values())
            f["where"] = "studio" if _studio(f) else "site"
    studio = [cid for cid, f in footage.items() if f.get("where") == "studio"]
    site = [cid for cid, f in footage.items() if f["kind"] == "talking_head" and f.get("where") == "site"]
    # In a voice-over the voice is Bryan's from the studio: every other clip is laid under it, a site clip with
    # some talk on it (workers, a radio, a walk-through) included; its sound is only ambience there.
    under = broll + narrated
    on = bool(settings.EDIT_VOICEOVER and studio and under and not site)
    why = ("a talking head from the studio and site footage" if on else
           "voice-overs are turned off" if not settings.EDIT_VOICEOVER and studio and under else
           "the talking head was filmed on site" if site else
           "no B-roll to lay over the voice" if studio else "no studio talking head")
    return {"on": on, "footage": {str(k): v for k, v in footage.items()}, "studio": studio, "site": site,
            "broll": under, "narrated": narrated, "why": why}


def labels(vo: dict | None) -> dict[int, str]:
    """A few words per clip for the editor's prompt ("talking head, filmed in the studio")."""
    out = {}
    for cid, f in ((vo or {}).get("footage") or {}).items():
        kind = f.get("kind")
        out[int(cid)] = ("talking head, filmed in the studio" if f.get("where") == "studio" else
                         "talking head, filmed on site" if kind == "talking_head" else
                         "B-roll: site footage, its sound only ambience" if kind == "broll" else
                         "talking over site footage, no face to camera" if kind == "narrated" else "")
    return out


# ── The plan ──────────────────────────────────────────────────────────────────

PLAN_RULE = """- This post is a voice-over. Bryan's talking head (clips {studio}) was filmed in his studio; the \
other clips ({broll}) are site B-roll whose sound is only ambience. Plan the talking-head cuts exactly as for a \
talking-head Reel: they are the voice and the timing, and every rule above applies to them. Put each B-roll cut \
in the list just BEFORE the talking-head cut whose words it shows: a B-roll cut doesn't stop his voice, Studio \
lays it over him from the start of the next talking-head cut for about its length, changing shots at the ends of \
his sentences or clauses. Choose B-roll that shows what he is saying at that moment, 2 to 5 seconds each of steady, \
sharp footage, enough to cover everything between his first and last sentence. Studio shows Bryan full screen for \
his first sentence (the hook) and his last (the close); in between he stays in a small window in a bottom corner \
over the B-roll, so B-roll whose key action is in the middle or the top works best. Don't place B-roll in his \
first or last sentence, and don't plan cards over his first sentence.
- picture: where Bryan wants to be seen full screen instead of in the window (his notes: "full screen here", \
"show me when I say this"): full_screen stretches anchored like graphics (clip_id of the talking head, start and \
end in that clip's seconds); [] for none. side: "left" or "right" when his notes ask to move the window to that \
corner, else "auto". On a revision, keep the stretches he asked for before."""

PICTURE_SCHEMA = {
    "type": "object",
    "properties": {
        "full_screen": {"type": "array", "items": {
            "type": "object",
            "properties": {"clip_id": {"type": "integer"}, "start": {"type": "number"}, "end": {"type": "number"}},
            "required": ["clip_id", "start", "end"], "additionalProperties": False}},
        "side": {"type": "string", "enum": ["auto", "left", "right"]},
    },
    "required": ["full_screen", "side"],
    "additionalProperties": False,
}


def plan_rule(vo: dict) -> str:
    return (PLAN_RULE.replace("{studio}", ", ".join(map(str, vo["studio"])))
            .replace("{broll}", ", ".join(map(str, vo["broll"]))))


def schema(base: dict) -> dict:
    """The edit plan's schema with the voice-over's picture field."""
    out = json.loads(json.dumps(base))
    out["properties"]["picture"] = PICTURE_SCHEMA
    out["required"] = list(out["required"]) + ["picture"]
    return out


def split_plan(edl: dict, vo: dict, picture: dict | None = None) -> None:
    """Claude's cut list as the voice (the talking-head and narrated cuts, in order) and the B-roll laid over it
    (edl["broll"]: each B-roll cut with the moment it covers from, the start of the next voice cut: at_clip, at;
    after the last voice cut, the end of it, with place "end"). The plan's picture field (picture) becomes asks
    (edl["pip_asks"], edl["pip_side"])."""
    broll = set(vo.get("broll") or [])
    voice, out, waiting = [], [], []
    for c in edl.get("cuts") or []:
        if c.get("clip_id") in broll:
            waiting.append(c)
            continue
        voice.append(c)
        for b in waiting:
            out.append({**_shot_of(b), "at_clip": c["clip_id"], "at": round(float(c["start"]), 3), "place": "start"})
        waiting = []
    if waiting and voice:
        last = voice[-1]
        for b in waiting:
            out.append({**_shot_of(b), "at_clip": last["clip_id"], "at": round(float(last["end"]), 3), "place": "end"})
    edl["cuts"] = voice
    edl["broll"] = out
    pic = picture or {}
    asks = []
    for s in pic.get("full_screen") or []:
        try:
            a, b = float(s["start"]), float(s["end"])
            if b > a:
                asks.append({"kind": "full", "clip_id": int(s["clip_id"]), "from": round(a, 3), "to": round(b, 3),
                             "by": "plan"})
        except (KeyError, TypeError, ValueError):
            continue
    if asks:
        edl["pip_asks"] = asks
    if pic.get("side") in ("left", "right"):
        edl["pip_side"] = pic["side"]


def _shot_of(c: dict) -> dict:
    return {"clip_id": c["clip_id"], "start": round(float(c["start"]), 3), "end": round(float(c["end"]), 3),
            "why": c.get("why", "")}


def keep_asks(edl: dict, previous: dict) -> None:
    """A new plan keeps the full-screen stretches and the side asked for before (Bryan's notes, fixes), plus its
    own."""
    old = [a for a in previous.get("pip_asks") or [] if a.get("by") != "plan"]
    new = edl.get("pip_asks") or []
    merged = old + [a for a in new if not any(_same_ask(a, o) for o in old)]
    if merged:
        edl["pip_asks"] = merged
    if not edl.get("pip_side") and previous.get("pip_side"):
        edl["pip_side"] = previous["pip_side"]


def _same_ask(a: dict, b: dict) -> bool:
    return (a.get("kind") == b.get("kind") and a.get("clip_id") == b.get("clip_id")
            and abs(float(a.get("from", 0)) - float(b.get("from", 0))) < 0.5
            and abs(float(a.get("to", 0)) - float(b.get("to", 0))) < 0.5)


# ── Asks: full screen here, the window there, move me to the left ─────────────

_FULL = re.compile(r"\b(full[\s-]?screen|fullscreen|full frame|show (me|my face|him)|see (me|my face)|"
                   r"on me here|make me (big|bigger)|bigger here)\b", re.I)
_WINDOW = re.compile(r"\b(not? full[\s-]?screen|back (in|to|into) the (window|corner|box)|show the (site|footage|"
                     r"b-?roll|work)|(small|little) window|picture[- ]in[- ]picture|in the corner here)\b", re.I)
_SIDE = re.compile(r"\b(?:move|put|place|switch|have)\b[^.]{0,30}?\b(?:to|on|in)\s+the\s+(left|right)\b|"
                   r"\b(left|right)[- ](?:side|corner|hand side)\b", re.I)
_WHOLE = re.compile(r"\b(whole|entire|all through|all the way|throughout|every ?where|the whole thing|no window)\b",
                    re.I)


def note_asks(text: str) -> dict | None:
    """What a note asks of the picture, by its words: {kind: full | window, whole: bool} and/or {side}; None
    when it asks nothing of the window."""
    text = str(text or "")
    out = {}
    side = _SIDE.search(text)
    if side:
        out["side"] = (side.group(1) or side.group(2)).lower()
    if re.search(r"\bno window\b|\bdon'?t (put|show) me in a (window|box|corner)\b", text, re.I):
        out.update(kind="full", whole=True)
    elif _WINDOW.search(text):
        out.update(kind="window", whole=bool(_WHOLE.search(text)))
    elif _FULL.search(text):
        out.update(kind="full", whole=bool(_WHOLE.search(text)))
    return out or None


def _to_source(cuts: list[dict], t: float) -> tuple[int, float] | None:
    off = 0.0
    for c in cuts:
        length = c["end"] - c["start"]
        if off - 0.001 <= t <= off + length + 0.001:
            return c["clip_id"], c["start"] + min(max(t - off, 0.0), length)
        off += length
    if cuts and t > off:
        return cuts[-1]["clip_id"], cuts[-1]["end"]
    return None


def stretch_at(edl: dict, clips: dict[int, dict], t: float) -> tuple[float, float] | None:
    """The sentence being said at t on a draft's clock (start, end on that clock): the stretch a "full screen
    here" note means."""
    cuts = [c for c in edl.get("cuts") or [] if c["clip_id"] in clips]
    words = editor.timeline(cuts, clips) if cuts else []
    if not words:
        return None
    k = min(range(len(words)), key=lambda i: 0 if words[i]["start"] <= t <= words[i]["end"]
            else min(abs(words[i]["start"] - t), abs(words[i]["end"] - t)))
    a = k
    while a > 0 and not editor.SENTENCE_END.search(words[a - 1]["word"].strip()):
        a -= 1
    b = k
    while b < len(words) - 1 and not editor.SENTENCE_END.search(words[b]["word"].strip()):
        b += 1
    return words[a]["start"], words[b]["end"]


def ask_for(edl: dict, clips: dict[int, dict], kind: str, start: float | None, end: float | None,
            by: str) -> list[dict]:
    """An ask on a draft's clock (start-end; start alone: the sentence said there; neither: the whole draft) as
    asks tied to the voice's own words (clip and seconds), so they hold through any re-cut."""
    cuts = [c for c in edl.get("cuts") or [] if c["clip_id"] in clips]
    total = sum(c["end"] - c["start"] for c in cuts)
    if start is None:
        start, end = 0.0, total
    elif end is None or end <= start + 0.2:
        span = stretch_at(edl, clips, start)
        if span is None:
            return []
        start, end = span
    out, off = [], 0.0
    for c in cuts:  # one ask per piece of voice it covers
        length = c["end"] - c["start"]
        a, b = max(start, off), min(end, off + length)
        if b - a > 0.05:
            out.append({"kind": kind, "clip_id": c["clip_id"], "from": round(c["start"] + a - off, 3),
                        "to": round(c["start"] + b - off, 3), "by": by})
        off += length
    return _merge_asks(out)


def _merge_asks(asks: list[dict]) -> list[dict]:
    out = []
    for a in asks:
        if out and out[-1]["kind"] == a["kind"] and out[-1]["clip_id"] == a["clip_id"] and \
                a["from"] <= out[-1]["to"] + 1.5:
            out[-1]["to"] = max(out[-1]["to"], a["to"])
        else:
            out.append(dict(a))
    return out


def asks_from_notes(notes: list[dict], previous: dict, clips: dict[int, dict]) -> tuple[list[dict], str | None, list[str]]:
    """Bryan's notes on a voice-over draft that ask about the picture ("full screen here", "move me to the
    left"), made by Studio's code as asks on the new edit (so they're done whatever the plan says). Returns
    (asks, side or None, what was done in plain words)."""
    asks, side, did = [], None, []
    if not (previous.get("voiceover") or {}).get("on"):
        return asks, side, did
    for n in notes:
        want = note_asks(n.get("body"))
        if not want:
            continue
        if want.get("side"):
            side = want["side"]
            did.append(f"moved your window to the {side}")
        if want.get("kind"):
            at = None if want.get("whole") else n.get("at_seconds")
            made = ask_for(previous, clips, want["kind"], at, None, "note")
            asks += made
            if made:
                did.append(("showed you full screen" if want["kind"] == "full" else "put the site footage back with "
                            "you in the window") + (" all through" if at is None else f" at {_mmss(at)}"))
    return asks, side, did


def _mmss(t: float) -> str:
    return f"{int(t // 60)}:{int(t % 60):02d}"


# ── The layout on the draft's clock ───────────────────────────────────────────

def _offsets(cuts: list[dict]) -> list[float]:
    out, off = [], 0.0
    for c in cuts:
        out.append(off)
        off += c["end"] - c["start"]
    return out + [off]


def to_draft(cuts: list[dict], cid: int, src: float, place: str = "start") -> float | None:
    """A moment of a voice clip (its seconds) on the draft's clock: inside a piece, or (cut out) the next piece
    of that clip on ("start"), or the end of the last piece before it ("end")."""
    offs = _offsets(cuts)
    for k, c in enumerate(cuts):
        if c["clip_id"] == cid and c["start"] - 0.05 <= src <= c["end"] + 0.05:
            return offs[k] + min(max(src - c["start"], 0.0), c["end"] - c["start"])
    later = [k for k, c in enumerate(cuts) if c["clip_id"] == cid and c["start"] >= src]
    earlier = [k for k, c in enumerate(cuts) if c["clip_id"] == cid and c["end"] <= src]
    if place == "end" and earlier:
        return offs[max(earlier, key=lambda k: cuts[k]["end"]) + 1]
    if later:
        return offs[min(later, key=lambda k: cuts[k]["start"])]
    if earlier:
        return offs[max(earlier, key=lambda k: cuts[k]["end"]) + 1]
    return None


def edges(words: list[dict]) -> list[float]:
    """Where a shot may change: the middle of the gap after each word that ends a sentence or a clause (a full
    stop, a comma) or before a pause of PAUSE_EDGE, on the draft's clock."""
    out = []
    for a, b in zip(words, words[1:]):
        end = max(a["end"], a.get("sound_end", a["end"]))
        gap = b["start"] - end
        if editor.CLAUSE_END.search(a["word"].strip()) or gap >= PAUSE_EDGE or b["start"] - a["end"] >= PAUSE_EDGE:
            out.append(round((end + b["start"]) / 2 if gap > 0 else (a["end"] + b["start"]) / 2, 3))
    return out


def hook_and_close(words: list[dict], speech_end: float) -> tuple[float, float]:
    """The end of his first sentence (the hook) and the start of his last (the close), on the draft's clock."""
    ends = [k for k, w in enumerate(words) if editor.SENTENCE_END.search(w["word"].strip())]
    cut_points = edges(words)

    def after(k):  # the middle of the gap after word k
        if k + 1 >= len(words):
            return speech_end
        end = max(words[k]["end"], words[k].get("sound_end", words[k]["end"]))
        return round((end + words[k + 1]["start"]) / 2 if words[k + 1]["start"] > end else words[k + 1]["start"], 3)
    first = next((k for k in ends if words[k]["end"] - words[0]["start"] >= HOOK_SHORTEST), None)
    hook = after(first) if first is not None else None
    if hook is None or hook > words[0]["start"] + HOOK_LONGEST:
        later = [t for t in cut_points if t >= words[0]["start"] + 2.5]
        hook = later[0] if later and later[0] <= words[0]["start"] + HOOK_LONGEST else \
            min(words[0]["start"] + HOOK_LONGEST, speech_end)
    before_last = [k for k in ends if k < len(words) - 1]
    close = after(before_last[-1]) if before_last else 0.0
    if speech_end - close > CLOSE_LONGEST:
        near = [t for t in cut_points if 2.0 <= speech_end - t <= CLOSE_LONGEST]
        close = near[0] if near else speech_end - CLOSE_LONGEST
    return round(hook, 3), round(max(close, hook), 3)


def _near(points: list[float], t: float, lo: float, hi: float) -> float | None:
    inside = [p for p in points if lo - 1e-6 <= p <= hi + 1e-6]
    return min(inside, key=lambda p: abs(p - t)) if inside else None


def requests(edl: dict, cuts: list[dict], clips: dict[int, dict], plan: dict | None = None,
             hook: float = 0.0, close: float = 0.0) -> list[dict]:
    """The B-roll asked for (edl["broll"]) with the moment each covers from on the draft's clock ("anchor") and the
    footage it can use ("avail": the clip's seconds from its in-point to its end). A longer one is split into
    shots of about SHOT_LONGEST. With none planned, the plan's B-roll segments spread over the middle."""
    out = []
    for r in edl.get("broll") or []:
        clip = clips.get(r.get("clip_id"))
        if not clip:
            continue
        at = to_draft(cuts, r.get("at_clip"), float(r.get("at", 0)), r.get("place", "start"))
        if at is None:
            continue
        length = float(r["end"]) - float(r["start"])
        if r.get("place") == "end":
            at = max(at - length, 0.0)
        out.append({**r, "anchor": round(at, 3), "length": length})
    if not out and plan:
        under = set((edl.get("voiceover") or {}).get("broll") or [])
        segs = [s for s in plan.get("segments") or [] if s.get("clip_id") in clips and s["clip_id"] in under]
        middle = max(close - hook, 0.0)
        for i, s in enumerate(segs):
            length = min(float(s["end"]) - float(s["start"]), SHOT_LONGEST) if s["end"] > s["start"] else SHOT_LONGEST
            out.append({"clip_id": s["clip_id"], "start": float(s["start"]), "end": float(s["start"]) + length,
                        "why": s.get("purpose", ""), "anchor": round(hook + middle * i / max(len(segs), 1), 3),
                        "length": length, "from_segments": True})
    split = []
    for r in sorted(out, key=lambda r: r["anchor"]):
        n = max(1, math.ceil(r["length"] / SHOT_LONGEST - 0.15))
        part = r["length"] / n
        for i in range(n):
            split.append({**r, "start": round(float(r["start"]) + i * part, 3), "length": round(part, 3),
                          "anchor": round(r["anchor"] + i * part, 3)})
    for r in split:
        dur = float(clips[r["clip_id"]].get("duration") or (r["start"] + r["length"]))
        r["avail"] = round(max(dur - r["start"], 0.0), 3)
        r["dur"] = dur
    return split


def _subtract(spans: list[list[float]], cut: list[float]) -> list[list[float]]:
    out = []
    for a, b in spans:
        if b <= cut[0] or a >= cut[1]:
            out.append([a, b])
            continue
        if a < cut[0]:
            out.append([a, cut[0]])
        if b > cut[1]:
            out.append([cut[1], b])
    return out


def layout(edl: dict, cuts: list[dict], clips: dict[int, dict], words: list[dict], speech_end: float,
           plan: dict | None = None) -> dict | None:
    """The picture of a voice-over draft on its clock: {hook: [0, end of his first sentence], close: [start of his
    last, the speech's end], shots: [{clip_id, src (its in-point), start, end, why}], spans: when his window is up
    (the shots over his studio talking head), full: stretches asked full screen, notes}. None when it isn't a
    voice-over, or there's no room between his first and last sentence (then he's full screen all through, as
    before; notes say so in edl["pip_notes"])."""
    vo = edl.get("voiceover") or {}
    if not vo.get("on") or not words:
        return None
    hook, close = hook_and_close(words, speech_end)
    notes = []
    if close - hook < MIDDLE_MIN:
        edl["pip_notes"] = ["kept you full screen all through: there wasn't room for site footage between your "
                            "first and last sentence"]
        return None
    points = sorted(set([p for p in edges(words) if hook <= p <= close] + [hook, close]))
    reqs = requests(edl, cuts, clips, plan, hook, close)
    shots, t = [], hook
    for r in reqs:  # one planned just before his last sentence starts a little earlier, so it has room
        r["anchor"] = round(min(r["anchor"], max(close - LAST_ROOM, hook)), 3)
    for i, r in enumerate(reqs):
        a = max(r["anchor"], t)
        if a > close - SHOT_MIN:
            notes.append(f"left out the site shot \"{r.get('why') or 'B-roll'}\": no room before your last sentence")
            continue
        start = _near(points, a, max(t, a - SNAP), min(a + SNAP, close - SHOT_MIN))
        if start is None:  # no sentence or clause end near its words: the nearest one it can start at
            fits = [p for p in points if t - 1e-6 <= p <= close - SHOT_MIN + 1e-6]
            start = min(fits, key=lambda p: abs(p - a)) if fits else None
        if start is None:
            notes.append(f"left out the site shot \"{r.get('why') or 'B-roll'}\": no room before your last sentence")
            continue
        nxt = reqs[i + 1]["anchor"] if i + 1 < len(reqs) else close
        limit = min(close, start + min(SHOT_LONGEST, r["avail"]))
        if i + 1 < len(reqs):
            limit = min(limit, max(nxt, start + SHOT_MIN))
        want = start + max(min(r["length"], SHOT_LONGEST), SHOT_MIN)
        end = _near(points, want, start + SHOT_MIN, limit)
        if end is None:
            fits = [p for p in points if start + SHOT_MIN - 1e-6 <= p <= limit + 1e-6]
            end = fits[-1] if fits else None
        if end is None:
            continue
        shots.append({"clip_id": r["clip_id"], "src": float(r["start"]), "start": start, "end": end,
                      "why": r.get("why", ""), "dur": r["dur"]})
        t = end
    # Close the short gaps: stretch the shot before (its footage allowing), else start the next one earlier.
    for k, s in enumerate(shots):
        nxt = shots[k + 1]["start"] if k + 1 < len(shots) else close
        gap = nxt - s["end"]
        if 0.01 < gap < GAP_FILL or (k + 1 == len(shots) and 0.01 < gap):
            room = s["dur"] - s["src"] - (s["end"] - s["start"])
            longest = s["start"] + SHOT_STRETCH
            if room >= gap - 0.01 and nxt <= longest + 1e-6:
                s["end"] = nxt
                continue
            if k + 1 < len(shots) and shots[k + 1]["src"] >= gap and \
                    shots[k + 1]["end"] - s["end"] <= SHOT_STRETCH + 1e-6:
                shots[k + 1]["src"] -= gap
                shots[k + 1]["start"] = s["end"]
    if shots and shots[0]["start"] - hook > 0.01:  # the first one starts as the hook ends
        s, gap = shots[0], shots[0]["start"] - hook
        if gap < GAP_FILL and s["src"] >= gap:
            s["src"] -= gap
            s["start"] = hook
    # Asks: full screen here (Bryan's notes, the read's fixes, the plan), or the window back.
    full = []
    for ask in sorted(edl.get("pip_asks") or [], key=lambda a: 0 if a.get("kind") == "full" else 1):
        a = to_draft(cuts, ask.get("clip_id"), float(ask.get("from", 0)), "start")
        b = to_draft(cuts, ask.get("clip_id"), float(ask.get("to", 0)), "end")
        if a is None or b is None or b - a < 0.2:
            continue
        a = _near(points, a, a - SNAP, a + 0.2) or a
        b = _near(points, b, b - 0.2, b + SNAP) or b
        if ask.get("kind") == "full":
            full.append([round(a, 3), round(b, 3)])
        else:
            full = _subtract(full, [a, b])
    kept = []
    for s in shots:
        parts = [[s["start"], s["end"]]]
        for f in full:
            parts = _subtract(parts, f)
        for a, b in parts:
            if b - a >= SHOT_MIN - 0.01 or (b - a >= 0.8 and len(parts) == 1):
                kept.append({**s, "src": round(s["src"] + a - s["start"], 3), "start": round(a, 3), "end": round(b, 3)})
    shots = [{k: v for k, v in s.items() if k != "dur"} for s in kept]
    for s in shots:
        s["start"], s["end"], s["src"] = _frame(s["start"]), _frame(s["end"]), round(s["src"], 3)
    shots = [s for s in shots if s["end"] - s["start"] >= 0.5]
    studio = set(vo.get("studio") or [])
    spans = []
    offs = _offsets(cuts)
    for s in shots:  # his window is up over his studio talking head (not over a narrated piece)
        for k, c in enumerate(cuts):
            a, b = max(s["start"], offs[k]), min(s["end"], offs[k + 1])
            if b - a > 0.01 and c["clip_id"] in studio:
                if spans and abs(spans[-1][1] - a) < 0.02:
                    spans[-1][1] = round(b, 3)
                else:
                    spans.append([round(a, 3), round(b, 3)])
    spans = [[_frame(a), _frame(b)] for a, b in spans if b - a >= 0.3]
    if not shots:
        notes.append("kept you full screen: no site footage could be placed")
    covered = sum(s["end"] - s["start"] for s in shots)
    return {"hook": [0.0, hook], "close": [close, round(speech_end, 3)], "shots": shots, "spans": spans,
            "full": full, "covered": round(covered / max(close - hook, 0.01), 2), "notes": notes}


def _frame(t: float) -> float:
    return round(round(t * editor.FPS) / editor.FPS, 4)


# ── The window: where it goes, what it shows ─────────────────────────────────

def box(side: str) -> tuple[int, int, int, int]:
    """The window on the Reel: (x, y, width, height)."""
    return PIP_X.get(side, PIP_X["left"]), PIP_Y, PIP_W, PIP_H


def caption_room(side: str) -> tuple[int, int]:
    """The captions' left and right insets while the window is up beside them."""
    x, _, w, _ = box(side)
    return (x + w + PIP_GAP, 150) if side == "left" else (96, editor.W - x + PIP_GAP)


def crop_for(cuts: list[dict], clips: dict[int, dict], spans: list[list[float]]) -> tuple[int, int, int, int]:
    """The part of his full-screen picture the window shows (x, y, width, height on the Reel, 3:4): around his face
    as framed in the draft (framing.py's face finder, its faces.json), the face about two fifths down; the upper
    middle when no face is found."""
    seen = []
    for a, b in spans[:6]:
        try:
            seen += editor.face_boxes(cuts, clips, a, b)
        except Exception as e:
            log.warning("Couldn't find the face for the window: %s", e)
            break
    if seen:
        fx = statistics.median(f[0] for f in seen)
        fy = statistics.median(f[1] for f in seen)
        size = statistics.median(f[2] for f in seen)
        w = min(max(size * 3.2, 480.0), float(editor.W))
    else:
        fx, fy, w = editor.W / 2, editor.H * 0.36, 720.0
    h = min(w * PIP_H / PIP_W, float(editor.H))
    w = h * PIP_W / PIP_H
    x = min(max(fx - w / 2, 0.0), editor.W - w)
    y = min(max(fy - 0.4 * h, 0.0), editor.H - h)
    w2, h2 = int(w) // 2 * 2, int(h) // 2 * 2
    return int(x) // 2 * 2, int(y) // 2 * 2, w2, h2


def choose_side(track: Path, shots: list[dict], asked: str | None = None) -> tuple[str, dict]:
    """Left or right: where the B-roll is calmest under the window (the least detail and movement there, by each
    shot's length), left unless right is clearly calmer (Instagram's buttons sit on the right). Bryan's ask wins.
    Returns (side, {left, right} busyness)."""
    if asked in ("left", "right"):
        return asked, {}
    try:
        import numpy as np
        w, h = 54, 96
        raw = subprocess.run([settings.FFMPEG, "-v", "error", "-i", str(track), "-an", "-vf",
                              f"fps=4,scale={w}:{h}:flags=area,format=gray", "-f", "rawvideo", "-"],
                             capture_output=True, timeout=600, check=True).stdout
        frames = np.frombuffer(raw[:len(raw) // (w * h) * w * h], np.uint8).reshape(-1, h, w).astype(np.float32)
    except Exception as e:
        log.warning("Couldn't look at the B-roll for the window's side: %s", e)
        return "left", {}
    score = {}
    for side in ("left", "right"):
        x, y, bw, bh = box(side)
        x0, x1, y0, y1 = int(x / editor.W * w), int((x + bw) / editor.W * w), int(y / editor.H * h), int((y + bh) / editor.H * h)
        total = 0.0
        for s in shots:
            i0, i1 = int(s["start"] * 4), max(int(s["end"] * 4), int(s["start"] * 4) + 1)
            part = frames[i0:i1, y0:y1, x0:x1]
            if not len(part):
                continue
            detail = float(np.mean(np.abs(np.diff(part, axis=2)))) + float(np.mean(np.abs(np.diff(part, axis=1))))
            motion = float(np.mean(np.abs(np.diff(part, axis=0)))) if len(part) > 1 else 0.0
            total += (detail + 2 * motion) * (s["end"] - s["start"])
        score[side] = round(total, 1)
    side = "right" if score.get("right", 0) < 0.8 * score.get("left", 0) else "left"
    return side, score


# ── Rendering ─────────────────────────────────────────────────────────────────

def _art(folder: Path) -> tuple[Path, Path, Path]:
    """The window's mask (rounded corners), its shadow and its hairline, as PNGs drawn once with Pillow."""
    from PIL import Image, ImageDraw, ImageFilter
    folder.mkdir(parents=True, exist_ok=True)
    mask, shadow, border = folder / "pip_mask.png", folder / "pip_shadow.png", folder / "pip_border.png"
    if mask.exists() and shadow.exists() and border.exists():
        return mask, shadow, border
    s = 4  # drawn 4x and scaled down, for smooth corners
    m = Image.new("L", (PIP_W * s, PIP_H * s), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, PIP_W * s - 1, PIP_H * s - 1], PIP_RADIUS * s, fill=255)
    m.resize((PIP_W, PIP_H), Image.LANCZOS).save(mask)
    pad = SHADOW_PAD
    a = Image.new("L", (PIP_W + 2 * pad, PIP_H + 2 * pad), 0)
    ImageDraw.Draw(a).rounded_rectangle([pad, pad + 10, pad + PIP_W, pad + PIP_H + 10], PIP_RADIUS, fill=110)
    sh = Image.new("RGBA", a.size, (14, 18, 20, 0))
    sh.putalpha(a.filter(ImageFilter.GaussianBlur(18)))
    sh.save(shadow)
    b = Image.new("RGBA", (PIP_W * s, PIP_H * s), (0, 0, 0, 0))
    ImageDraw.Draw(b).rounded_rectangle([s, s, PIP_W * s - 1 - s, PIP_H * s - 1 - s], PIP_RADIUS * s,
                                        outline=(246, 244, 238, 140), width=2 * s)
    b.resize((PIP_W, PIP_H), Image.LANCZOS).save(border)
    return mask, shadow, border


SHADOW_PAD = 40


def broll_track(shots: list[dict], clips: dict[int, dict], length: float, out: Path, sound: Path | None = None,
                alpha: bool = False) -> None:
    """The B-roll on the draft's clock: each shot framed to fill 1080x1920 from the middle, black (or, alpha,
    transparent) between them, as one video `length` seconds long; sound: its site sound (each shot's own,
    faded in and out at the edges, silence between) as a WAV."""
    fps, w, h = editor.FPS, editor.W, editor.H
    total = max(1, round(length * fps))
    args, graph, vids, auds, n = [settings.FFMPEG, "-y", "-v", "error"], [], [], [], -1
    at = 0
    pix = "yuva420p" if alpha else "yuv420p"
    gap_colour = "black@0.0" if alpha else "black"
    for k, s in enumerate(shots):
        f0, f1 = round(s["start"] * fps), round(s["end"] * fps)
        if f0 > at:
            gap = f0 - at
            graph.append(f"color=c={gap_colour}:s={w}x{h}:r={fps},format={pix},trim=end_frame={gap},setsar=1[g{k}]")
            vids.append(f"[g{k}]")
            if sound:
                graph.append(f"anullsrc=r={editor.RATE}:cl=stereo,atrim=end_sample={gap * editor.RATE // fps}[ga{k}]")
                auds.append(f"[ga{k}]")
        frames = max(1, f1 - max(f0, at))
        clip = clips[s["clip_id"]]
        n += 1
        args += ["-ss", f"{s['src']:.4f}", "-t", f"{frames / fps + 0.5:.3f}", "-i", clip["video_path"]]
        graph.append(f"[{n}:v]setpts=PTS-STARTPTS,fps={fps},scale={w}:{h}:force_original_aspect_ratio=increase,"
                     f"crop={w}:{h},setsar=1,format={pix},tpad=stop_mode=clone:stop={frames},trim=end_frame={frames},"
                     f"setpts=PTS-STARTPTS[s{k}]")
        vids.append(f"[s{k}]")
        if sound:
            samples = frames * editor.RATE // fps
            secs = samples / editor.RATE
            fade = min(SITE_FADE, secs / 3)
            if clip.get("has_audio"):
                graph.append(f"[{n}:a]asetpts=PTS-STARTPTS,aresample={editor.RATE},aformat=channel_layouts=stereo,"
                             f"apad=whole_len={samples},atrim=end_sample={samples},afade=t=in:d={fade:.3f},"
                             f"afade=t=out:st={secs - fade:.4f}:d={fade:.3f}[sa{k}]")
            else:
                graph.append(f"anullsrc=r={editor.RATE}:cl=stereo,atrim=end_sample={samples}[sa{k}]")
            auds.append(f"[sa{k}]")
        at = max(f0, at) + frames
    if at < total:
        gap = total - at
        graph.append(f"color=c={gap_colour}:s={w}x{h}:r={fps},format={pix},trim=end_frame={gap},setsar=1[gend]")
        vids.append("[gend]")
        if sound:
            graph.append(f"anullsrc=r={editor.RATE}:cl=stereo,atrim=end_sample={gap * editor.RATE // fps}[gaend]")
            auds.append("[gaend]")
    graph.append(f"{''.join(vids)}concat=n={len(vids)}:v=1:a=0,trim=end_frame={total}[v]")
    outputs = ["-map", "[v]"]
    if alpha:
        outputs += ["-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le", str(out)]
    else:
        outputs += ["-c:v", "libx264", "-preset", "fast", "-crf", "16", "-pix_fmt", "yuv420p", str(out)]
    if sound:
        graph.append(f"{''.join(auds)}concat=n={len(auds)}:v=0:a=1[a]")
        outputs += ["-map", "[a]", "-c:a", "pcm_f32le", str(sound)]
    extract._run(args + ["-filter_complex", ";".join(graph), *outputs])


def _enable(spans: list[list[float]]) -> str:
    fps = editor.FPS
    return "+".join(f"between(n,{round(a * fps)},{round(b * fps) - 1})" for a, b in spans) or "0"


def composite(cut: Path, track: Path | None, pip: dict, out: Path, length: float, alpha: bool = False) -> None:
    """The picture: his full-screen cut (cut), the B-roll (track) over it where there's a shot, and his window (the
    crop of his picture, rounded, with its shadow and hairline) where pip["spans"] say, as one video with his voice
    (the cut's sound, copied). alpha: only his window, on a transparent layer `length` seconds long (ProRes 4444,
    no sound), for the Resolve project (its B-roll is on the timeline as clips)."""
    fps = editor.FPS
    mask, shadow, border = _art(editor.RENDERS / "pip")
    x, y, _, _ = box(pip["side"])
    cx, cy, cw, ch = pip["crop"]
    win = _enable(pip["spans"])
    args = [settings.FFMPEG, "-y", "-v", "error", "-i", str(cut)]
    if not alpha:
        args += ["-i", str(track)]
    first = 1 if alpha else 2
    for png in (mask, shadow, border):
        args += ["-loop", "1", "-framerate", str(fps), "-i", str(png)]
    im, ish, ib = first, first + 1, first + 2
    window = (f"crop={cw}:{ch}:{cx}:{cy},scale={PIP_W}:{PIP_H},format=yuva420p[pp];"
              f"[{im}:v]format=gray[m];[pp][m]alphamerge=shortest=1[pip];")
    if alpha:
        graph = (f"[0:v]{window}color=c=black@0.0:s={editor.W}x{editor.H}:r={fps}:d={length:.3f},format=yuva420p[b1];"
                 f"[b1][{ish}:v]overlay={x - SHADOW_PAD}:{y - SHADOW_PAD}:enable='{win}'[b2];"
                 f"[b2][pip]overlay={x}:{y}:eof_action=pass:enable='{win}'[b3];"
                 f"[b3][{ib}:v]overlay={x}:{y}:enable='{win}',format=yuva444p10le[v]")
        extract._run(args + ["-filter_complex", graph, "-map", "[v]", "-c:v", "prores_ks", "-profile:v", "4444",
                             "-pix_fmt", "yuva444p10le", "-r", str(fps), "-t", f"{length:.3f}", str(out)])
        return
    shots = _enable([[s["start"], s["end"]] for s in pip["shots"]])
    graph = (f"[0:v]split[bf][bp];[bp]{window}[bf][1:v]overlay=0:0:enable='{shots}'[b1];"
             f"[b1][{ish}:v]overlay={x - SHADOW_PAD}:{y - SHADOW_PAD}:shortest=1:enable='{win}'[b2];"
             f"[b2][pip]overlay={x}:{y}:shortest=1:enable='{win}'[b3];"
             f"[b3][{ib}:v]overlay={x}:{y}:shortest=1:enable='{win}',format=yuv420p[v]")
    extract._run(args + ["-filter_complex", graph, "-map", "[v]", "-map", "0:a", "-c:a", "copy", "-c:v", "libx264",
                         "-preset", "fast", "-crf", "16", "-r", str(fps), "-t", f"{length:.3f}", str(out)])


def site_gain(sound: Path) -> float | None:
    """The gain (dB) that sets the site sound SITE_BELOW under the levelled voice (editor.VOICE_LUFS); None when
    it's silent."""
    heard = editor.loudness(sound)
    if heard is None:
        return None
    return round(min(max(editor.VOICE_LUFS - SITE_BELOW - heard, -30.0), 6.0), 1)


def render_picture(edl: dict, cuts: list[dict], clips: dict[int, dict], words: list[dict], footage: float,
                   speech_end: float, work: Path, plan: dict | None = None) -> tuple[Path | None, dict | None, list[str]]:
    """A voice-over draft's picture (layout, then the B-roll track and the composite) from work/cut.mp4, as
    work/picture.mp4 with his voice, plus the site sound (work/site.wav, its gain in the layout). Returns (the
    picture, or None when it's the plain cut; the layout kept as edl["pip"]; notes for the draft's checks)."""
    pip = layout(edl, cuts, clips, words, speech_end, plan)
    notes = list(edl.pop("pip_notes", []) or [])
    if pip is None:
        return None, None, notes
    notes += pip.pop("notes", [])
    if not pip["shots"]:
        return None, None, notes
    track, sound = work / "broll.mp4", work / "site.wav"
    broll_track(pip["shots"], clips, footage, track, sound)
    pip["side"], pip["busy"] = choose_side(track, pip["shots"], edl.get("pip_side"))
    pip["box"] = list(box(pip["side"]))
    pip["crop"] = list(crop_for(cuts, clips, pip["spans"])) if pip["spans"] else [0, 0, editor.W, editor.H]
    pip["site_gain_db"] = site_gain(sound)
    picture = work / "picture.mp4"
    composite(work / "cut.mp4", track, pip, picture, footage)
    return picture, pip, notes


def lit_beside(chunks: list[dict], pip: dict | None) -> list[dict]:
    """Captions beside the window sit on site footage, busy and often bright: they get the gallery look's stronger
    glow and the soft shade that hugs their words ("lit") while the window is up."""
    spans = (pip or {}).get("spans") or []
    return [{**c, "lit": True} if any(c["start"] < b and c["end"] > a for a, b in spans) else c for c in chunks]


def summary(pip: dict | None) -> str:
    """One sentence for Bryan's "What Studio did"."""
    if not pip:
        return ""
    n = len(pip["shots"])
    return (f"Studio laid {n} site shot{'s' if n != 1 else ''} over your voice with you in a small window "
            f"bottom-{pip['side']}, and kept you full screen for your first and last sentence"
            + (f" and the {len(pip['full'])} stretch{'es' if len(pip['full']) != 1 else ''} you asked for" if pip.get("full") else "")
            + ".")


def describe(pip: dict | None, clips: dict[int, dict] | None = None) -> str:
    """The picture in words, for Studio's read and the editor's re-plan."""
    if not pip:
        return ""
    clips = clips or {}
    shots = "; ".join(f"{s['start']:.1f}-{s['end']:.1f}s clip {s['clip_id']}"
                      + (f" ({(clips.get(s['clip_id']) or {}).get('title')})" if (clips.get(s['clip_id']) or {}).get("title") else "")
                      + f" from {s['src']:.1f}s" for s in pip["shots"])
    return (f"Bryan full screen 0.0-{pip['hook'][1]:.1f}s (his first sentence) and from {pip['close'][0]:.1f}s (his "
            f"last sentence and the close); site footage in between ({shots}), with Bryan in a rounded window "
            f"bottom-{pip['side']} while it's up"
            + (f"; full screen as asked at {', '.join(f'{a:.1f}-{b:.1f}s' for a, b in pip['full'])}" if pip.get("full") else "")
            + "; the site's own sound low under his voice.")


# ── DaVinci Resolve ───────────────────────────────────────────────────────────

def resolve_layer(edl: dict, cuts: list[dict], clips: dict[int, dict], length: float, out: Path) -> bool:
    """His window as a transparent layer (ProRes 4444) for the Resolve project: the cut rendered again for his
    picture, the window where it was in the draft. The B-roll itself goes on the timeline as clips (V2)."""
    pip = edl.get("pip")
    if not pip or not pip.get("spans"):
        return False
    with tempfile.TemporaryDirectory(prefix="studio-pip-") as tmp:
        cut = Path(tmp) / "cut.mp4"
        editor.render_cut(cuts, clips, cut)
        composite(cut, None, pip, out, length, alpha=True)
    return out.exists()
