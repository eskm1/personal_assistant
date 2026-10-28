"""
Studio listens back to and watches through every finished Reel the way a viewer does, before Bryan
sees it (Bryan, 2026-10-21: words cut mid-word, sentences broken by cuts, cards over his face, text
gone before it can be read, endings mid-sentence). Studio's read (draft_review.py) looks at about
six stills and the cut list; this checks the finished file itself, its sound and its picture ten
times a second, with tools already on the Studio machine (Whisper's re-check, the voice envelope,
OpenCV's face finder, ffmpeg) and no Claude, so it costs nothing.

Listen back: the finished sound against the words the cut list meant to keep.
  cut_short    a word cut short at a join or at the end: the cut lands inside the word (by its
               transcript times), the clip's voice is still going at the cut (voice_edges), or
               the word is missing when the draft is heard again (the re-transcription _render
               already makes, kept as qa["heard"]) and is quiet in the finished sound
  doubled      a word or phrase heard twice in a row that Bryan didn't say twice on purpose: a
               stutter left in, or the same words either side of a join (two takes)
  broken       a join that drops real words from inside a sentence (not hesitations or
               stutters), or a shot that stops partway through a sentence where the next shot
               doesn't carry on (a join Claude already judged reads complete stays as it is)
  ends_mid     the voice ends partway through a sentence
Watch through: the picture at SAMPLE_FPS against the edit plan's hook, cards, drawings and pictures.
  covers_face  the hook, a callout or a panel (drawn or a picture; in Site Notes a card too, a panel over the
               footage) over the face (framing.find_faces) for more than COVER_SECONDS; full-screen cards,
               drawings and pictures replace the picture on purpose; words behind him (behind.py) showing over
               the middle of his face (the cut-out missed it: their own layer's words, where the face finder sees him)
  flash        a graphic up for less than FLASH seconds
  too_fast     words that leave before they can be read: READ_BASE + READ_PER_CHAR a character,
               at least READ_MIN (the end card is deliberate and not held to it); a picture (pictures.py)
               gone before it can be seen (pictures.SEE_SECONDS)
  repeat       the same animation twice back to back
  late         a card, drawing or picture still on within editor.END_CLEAR of the close, or the end card
               still moving in the draft's last moments (the video ending mid-animation)

Each flag becomes a fix in Studio's read, in the read's own shape (what, why, start, end, area,
editor_can_fix, rule, action...), timed on the draft's clock with the words around it ("check" and
"subject" say which flag it is). Studio's own fix rounds (self_fix.py) make them with the same
patches as any other fix (fix_actions: move_cut, drop_words, restore_words, graphic, or replan when
nothing smaller fits) and judge them the same way: a round is kept when the flag is gone from the
next check and nothing got worse. What can't be fixed is left for Bryan with its left_because.

A slideshow Reel (slideshow.py: a carousel's slides with music) has no voice, so it's only watched: each slide is
held to the same reading time (up to slideshow.MAX_SECONDS, the most a slide gets), and the end card as on any Reel.

Reels only. It runs on its own thread while Claude reads the draft (start, then merge), so a draft
waits no longer for it; settings.FINAL_CHECK = False turns it off. It never breaks a draft: anything
that goes wrong is logged and the read carries on without it.
"""
import difflib
import json
import logging
import math
import re
import subprocess
import tempfile
import threading
import time
from concurrent.futures import Future
from pathlib import Path

import numpy as np

import editor
import extract
import framing
import settings
import voice_edges

log = logging.getLogger("studio.final_check")

# Watching: small grey frames, ten a second, are plenty to see a face and what moves.
SAMPLE_FPS = 10
FRAME_W, FRAME_H = 270, 480
COVER_SECONDS = 0.3      # a graphic over the face longer than this is a problem
COVER_SHARE = 0.2        # ... when it covers at least this share of the face's box
FACE_BORROW = 1.5        # a face hidden by a graphic is where it was this close by, in the same shot
FLASH = 0.5
READ_BASE, READ_PER_CHAR, READ_MIN = 0.3, 0.06, 1.2
REPEAT_GAP = 8.0         # two graphics this close (seconds between them) are back to back
SAME_PICTURE = 6.0       # mean grey levels apart: two graphics' frames this close look the same
MOVED_LEVELS, MOVED_SHARE = 16, 0.005   # the end still moving: this share of the frame changed this much
SLOT_SEARCH = 4.0        # how far (seconds) a title covering the face may move to clear it
CARD_LONGEST = 4.0       # fix_actions._graphic's longest card or callout
# Listening: a word reaching this far past a cut is cut inside it; a word this far under the
# draft's speech level, and not heard again, is missing.
EDGE = 0.05
WEAK_DB = 18.0
MAX_FLAGS = 6
WAIT = 240.0             # merge waits at most this long for the check (seconds)

# The rule behind each flag, for Studio's learned rules (learning.py), as the read writes them.
RULES = {
    "cut_short": "Cut only after a word's sound has died away, never inside a word",
    "doubled": "Never let a cut repeat words: keep one take of each phrase",
    "broken": "Never cut words out of the middle of a sentence",
    "ends_mid": "End on a complete sentence, never partway through one",
    "covers_face": "Keep titles and panels clear of the speaker's face",
    "flash": "Leave every graphic up long enough to read",
    "too_fast": "Leave text up about 0.3 s plus 0.06 s a letter, at least 1.2 s",
    "repeat": "Never play the same animation twice in a row",
    "late": "Finish every card and animation at least a second before the end",
}
AREAS = {"cut_short": "sound", "doubled": "sound", "broken": "story", "ends_mid": "story",
         "covers_face": "picture", "flash": "picture", "too_fast": "picture", "repeat": "picture", "late": "picture"}
ORDER = ["ends_mid", "cut_short", "broken", "doubled", "covers_face", "too_fast", "flash", "late", "repeat"]

_whisper_failed = 0.0    # when the fallback transcription last failed (it isn't retried for 10 minutes)


def _mmss(t) -> str:
    t = max(float(t or 0), 0.0)
    return f"{int(t // 60)}:{int(t % 60):02d}"


def _quote(words: list[str], most: int = 8) -> str:
    text = " ".join(w.strip() for w in words if w.strip())
    parts = text.split()
    return " ".join(parts[:most]) + (" ..." if len(parts) > most else "")


def _action(kind: str, **kw) -> dict:
    """An action in the read's vocabulary (draft_review.REEL_ACTIONS), every field present."""
    a = {"type": kind, "start": None, "end": None, "from": "", "to": "", "item": None, "field": "", "amount": None,
         "reason": ""}
    a.update({k: (round(v, 2) if isinstance(v, float) else v) for k, v in kw.items()})
    return a


def _fix(check: str, what: str, why: str, start: float | None, end: float | None, action: dict | None,
         subject: str, words: str = "", area: str | None = None, left_note: str = "") -> dict:
    """A flag as one of the read's fixes."""
    can = action is not None
    return {"what": what, "why": why, "start": None if start is None else round(start, 2),
            "end": None if end is None else round(end, 2), "area": area or AREAS[check], "when": "this_draft",
            "kind": None, "editor_can_fix": can, "rule": RULES[check] if can else "", "same_rule": 0,
            "action": action or _action("none"), "for_bryan": "", "check": check, "subject": subject,
            "words": words, **({"left_note": left_note} if left_note else {})}


# ── The job: what the check needs, read from the catalogue before it goes off on its own ─────

def _job(conn, production: dict) -> dict:
    edl = json.loads(production.get("edl") or "{}")
    ids = list(dict.fromkeys(c["clip_id"] for c in edl.get("cuts", [])))
    rows = conn.execute(f"SELECT * FROM clips WHERE id IN ({','.join('?' * len(ids))})", ids).fetchall() if ids else []
    clips = {r["id"]: dict(r) for r in rows}
    qa = json.loads(production.get("qa") or "{}")
    return {"id": production["id"], "video": Path(production["video_path"]), "edl": edl, "qa": qa, "clips": clips,
            "total": float(production.get("duration") or 0.0)}


def start(conn, production: dict) -> Future | None:
    """Starts the listen-back and watch-through of a Reel's finished draft on its own thread, so it
    runs while Claude reads the draft. Returns a Future with check()'s result (None when it failed),
    or None when it's turned off or not a Reel. Never raises."""
    if not settings.FINAL_CHECK or production.get("format") == "carousel":
        return None
    try:
        job = _job(conn, production)
        if not (job["edl"].get("cuts") or job["edl"].get("slideshow")) or not job["video"].exists():
            return None
    except Exception as e:
        log.warning("Studio couldn't start listening back to production %s: %s: %s", production.get("id"),
                    type(e).__name__, e)
        return None
    done: Future = Future()

    def go():
        try:
            done.set_result(check(job))
        except Exception as e:  # a draft without the check beats no draft
            log.warning("Studio's listen-back and watch-through failed for production %s: %s: %s", job["id"],
                        type(e).__name__, e)
            done.set_result(None)
    threading.Thread(target=go, name="final-check", daemon=True).start()
    return done


def run(conn, production: dict) -> dict | None:
    """The same check, here and now (tests, the command line). None when off or it failed."""
    pending = start(conn, production)
    return pending.result() if pending else None


def check(job: dict) -> dict:
    """Listen back and watch through. Returns {flags (the fixes), seconds, listened, watched,
    heard (where the heard words came from), frames}. Each half that fails is logged and skipped."""
    t0 = time.time()
    out = {"flags": [], "listened": False, "watched": False, "heard": None, "frames": 0}
    if job["edl"].get("slideshow"):  # slides and music: no voice to listen back to
        out["heard"] = "no voice"
    else:
        try:
            flags, heard = listen(job)
            out.update(listened=True, heard=heard)
            out["flags"] += flags
        except Exception as e:
            log.warning("Studio couldn't listen back to production %s: %s: %s", job["id"], type(e).__name__, e)
    try:
        flags, frames = watch(job)
        out.update(watched=True, frames=frames)
        out["flags"] += flags
    except Exception as e:
        log.warning("Studio couldn't watch production %s through: %s: %s", job["id"], type(e).__name__, e)
    out["flags"].sort(key=lambda f: (ORDER.index(f["check"]), f["start"] if f["start"] is not None else 0.0))
    out["flags"] = out["flags"][:MAX_FLAGS]
    out["seconds"] = round(time.time() - t0, 1)
    log.info("Production %s: listened back and watched through in %.1fs: %d flag(s)%s", job["id"], out["seconds"],
             len(out["flags"]), (" (" + ", ".join(f"{f['check']} at {_mmss(f['start'])}" for f in out["flags"]) + ")")
             if out["flags"] else "")
    return out


# ── Listen back ───────────────────────────────────────────────────────────────

def pieces(cuts: list[dict]) -> list[dict]:
    """The pieces as the render laid them out (editor._frame_plan: whole frames): each one's
    seconds in its clip (src0-src1), on the draft's clock (d0-d1), and whether its sound carries
    straight on into the next piece (no join there)."""
    frames, _, _, source = editor._frame_plan(cuts)
    out, at = [], 0.0
    for k, c in enumerate(cuts):
        length = frames[k] / editor.FPS
        carries = (k + 1 < len(cuts) and cuts[k + 1]["clip_id"] == c["clip_id"]
                   and abs(source[k] + length - source[k + 1]) < 1e-6)
        out.append({"k": k, "clip_id": c["clip_id"], "src0": source[k], "src1": source[k] + length, "d0": at,
                    "d1": at + length, "carries": carries, "cut": c})
        at += length
    return out


def intended(parts: list[dict], clips: dict[int, dict]) -> list[dict]:
    """The words the cut list keeps, in draft order, timed on the draft as rendered: {raw (as said),
    word (as shown), clip_id, i (its index in the clip's words), piece, start, end, src, src_end}."""
    out = []
    for p in parts:
        clip = clips.get(p["clip_id"])
        if not clip:
            continue
        say = p["cut"].get("say") or {}
        for i, w in enumerate(editor._words(clip)):
            if w["start"] >= p["src0"] - 0.02 and w["end"] <= p["src1"] + 0.02:
                shown = say.get(f"{w['start']:.2f}", w["word"])
                if not str(shown).strip():
                    continue  # folded into the word before by a correction
                out.append({"raw": w["word"], "word": shown, "clip_id": p["clip_id"], "i": i, "piece": p["k"],
                            "start": p["d0"] + w["start"] - p["src0"], "end": p["d0"] + w["end"] - p["src0"],
                            "src": w["start"], "src_end": w["end"]})
    return out


def _heard(job: dict) -> tuple[list[dict] | None, str | None]:
    """The draft's words as heard again: the re-transcription _render made (qa["heard"]), else one
    made here (Whisper on the finished file). None when there's no way to hear it."""
    global _whisper_failed
    heard = job["qa"].get("heard")
    if isinstance(heard, list):
        return heard, "render"
    if time.time() - _whisper_failed < 600:
        return None, None
    try:
        import transcribe
        with tempfile.TemporaryDirectory(prefix="studio-listen-") as tmp:
            wav = extract.extract_audio(job["video"], Path(tmp) / "heard.wav")
            words = transcribe.transcribe(wav)["words"]
        return [{"word": w["word"], "start": w["start"], "end": w["end"]} for w in words], "here"
    except Exception as e:
        _whisper_failed = time.time()
        log.info("No re-transcription to listen back with (%s); the cut list and the sound are checked", e)
        return None, None


def _aligned(want: list[dict], heard: list[dict] | None) -> set[int] | None:
    """Which of the intended words were heard again (their indices), by order; None without a hearing."""
    if heard is None:
        return None
    a = [editor._norm(w["raw"]) for w in want]
    b = [editor._norm(str(w.get("word", ""))) for w in heard]
    matcher = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    found = set()
    for block in matcher.get_matching_blocks():
        found.update(range(block.a, block.a + block.size))
    return found | {i for i, x in enumerate(a) if not x}


def _around(want: list[dict], t: float, before: int = 3, after: int = 3) -> str:
    """The words either side of a moment, with | at it ("pour a level | screed over")."""
    left = [w["word"] for w in want if w["end"] <= t + 0.01][-before:]
    right = [w["word"] for w in want if w["start"] >= t - 0.01][:after]
    return f"{_quote(left)} | {_quote(right)}".strip()


def listen(job: dict) -> tuple[list[dict], str | None]:
    """The listen-back's flags, and where the heard words came from (or None)."""
    edl, clips = job["edl"], job["clips"]
    cuts = [c for c in edl.get("cuts", []) if c["clip_id"] in clips]
    if not cuts:
        return [], None
    parts = pieces(cuts)
    want = intended(parts, clips)
    heard, source = _heard(job)
    found = _aligned(want, heard)
    try:
        sound = voice_edges.Voice(voice_edges._measure(job["video"]))
    except Exception as e:  # no sound to measure: the other evidence stands
        log.info("No envelope of production %s's finished sound: %s", job["id"], e)
        sound = None

    def lost(w: dict) -> bool:
        """Intended, not heard again, and quiet in the finished sound: it isn't there."""
        if found is None or sound is None or not sound.usable:
            return False
        n = want.index(w)
        return n not in found and sound.level(w["start"], max(w["end"], w["start"] + 0.05)) < sound.speech - WEAK_DB
    flags = []
    for p in parts:
        last = p["k"] == len(parts) - 1
        if not p["carries"]:
            flags += _end_side(p, clips, want, lost, last)
        if p["k"] == 0 or not parts[p["k"] - 1]["carries"]:
            flags += _start_side(p, clips, want, lost)
    flags += _doubled(want, clips, heard)
    flags += _broken(parts, clips, want)
    flags += _ending(parts, clips, want)
    # One flag per moment and kind (a cut inside a word is also the end of the sentence's speech).
    out, seen = [], set()
    for f in flags:
        key = (f["check"], round(f["start"] or 0, 1))
        if key not in seen:
            seen.add(key)
            out.append(f)
    return out, source


def _end_side(p: dict, clips: dict, want: list[dict], lost, last: bool) -> list[dict]:
    """The end of a piece where its sound stops (a join, or the draft's end): a word cut short?"""
    clip = clips[p["clip_id"]]
    words = editor._words(clip)
    v = voice_edges.voice(clip)
    src1, t = p["src1"], p["d1"]
    mine = [w for w in want if w["piece"] == p["k"]]
    cross = next((w for w in words if w["start"] < src1 - EDGE and w["end"] > src1 + EDGE), None)
    going = voice_edges.clipped(v, src1)
    gone = [w for w in mine[-3:] if lost(w)]
    if not (cross or going or (gone and gone[-1] is mine[-1])):
        return []
    dur = clip.get("duration") or (v.duration if v else src1 + 1.0)
    if cross:
        w, keep = cross, (src1 - cross["start"]) / max(cross["end"] - cross["start"], 0.01) >= 0.5
    else:
        w, keep = (editor._word_at(words, src1) or (words[mine[-1]["i"]] if mine else None)), True
    if w is None:
        return []
    k = next((i for i, x in enumerate(words) if x is w), None)
    nxt = words[k + 1] if k is not None and k + 1 < len(words) else None
    word = w["word"].strip()
    around = _around(want, t)
    if keep:
        why = ("Its sound is cut off as the shot ends, so it's heard cut short." if going or cross else
               "It isn't heard in the finished draft: the cut takes it.")
        if last and (going or cross):
            why = "The draft stops before this word has finished."
        base = min(w["end"] + editor.TAIL_PAD, nxt["start"] if nxt else dur)
        real = voice_edges.end_point(v, w["end"], base, next_start=nxt["start"] if nxt else None, limit=dur)
        # More of the shot, never into the next word (a move_cut keeps whole words it reaches).
        amount = min(max(real - src1 + 0.02, 0.1), (nxt["start"] - src1 - 0.03) if nxt else 9.0)
        if amount < 0.04:
            return [_fix("cut_short", f"Listen to \"{word}\" at {_mmss(t)}", why, t, None, None, word, around,
                         left_note="The next word starts straight after it, so there's no clean place to cut.")]
        return [_fix("cut_short", f"Keep the end of \"{word}\" at {_mmss(t)}", why, t, None,
                     _action("move_cut", start=t, field="before", amount=amount), word, around)]
    amount = -(src1 - w["start"] + 0.03)
    return [_fix("cut_short", f"Cut before the clipped \"{word}\" at {_mmss(t)}",
                 "The shot ends a moment into this word, so a scrap of it is heard.", t, None,
                 _action("move_cut", start=t, field="before", amount=amount), word, around)]


def _start_side(p: dict, clips: dict, want: list[dict], lost) -> list[dict]:
    """The start of a piece where its sound starts (a join, or the draft's start): a word cut into?"""
    clip = clips[p["clip_id"]]
    words = editor._words(clip)
    v = voice_edges.voice(clip)
    src0, t = p["src0"], p["d0"]
    mine = [w for w in want if w["piece"] == p["k"]]
    cross = next((w for w in words if w["start"] < src0 - EDGE and w["end"] > src0 + EDGE), None)
    onset = (v is not None and src0 > voice_edges.CLIP_WINDOW and v.active(src0 - voice_edges.CLIP_WINDOW, src0)
             and not v.quiet[min(v.i(src0), len(v.quiet) - 1)])
    gone = bool(mine) and lost(mine[0])
    if not (cross or onset or gone):
        return []
    if cross:
        w, keep = cross, (cross["end"] - src0) / max(cross["end"] - cross["start"], 0.01) >= 0.5
    else:
        w = next((x for x in words if x["start"] >= src0 - 0.02), None) if not mine else words[mine[0]["i"]]
        keep = True
    if w is None:
        return []
    k = next((i for i, x in enumerate(words) if x is w), None)
    prv = words[k - 1] if k else None
    word = w["word"].strip()
    around = _around(want, t)
    if keep:
        why = "The shot starts after this word began, so it's heard cut off."
        base = max(w["start"] - editor.HEAD_PAD, prv["end"] if prv else 0.0, 0.0)
        real = voice_edges.start_point(v, w["start"], base, prev_end=prv["end"] if prv else None)
        amount = min(max(src0 - real + 0.02, 0.1), (src0 - prv["end"] - 0.03) if prv else src0)
        if amount < 0.04:
            return [_fix("cut_short", f"Listen to \"{word}\" at {_mmss(t)}", why, t, None, None, word, around,
                         left_note="The word before runs straight into it, so there's no clean place to cut.")]
        return [_fix("cut_short", f"Keep the start of \"{word}\" at {_mmss(t)}", why, t, None,
                     _action("move_cut", start=t, field="after", amount=amount), word, around)]
    amount = -(w["end"] - src0 + 0.03)
    return [_fix("cut_short", f"Start after the clipped \"{word}\" at {_mmss(t)}",
                 "The shot starts at the tail of a word, so a scrap of it is heard.", t, None,
                 _action("move_cut", start=t, field="after", amount=amount), word, around)]


def _doubled(want: list[dict], clips: dict, heard: list[dict] | None) -> list[dict]:
    """Words heard twice in a row that weren't said twice on purpose."""
    norms = [editor._same_word(w["raw"]) for w in want]
    out, i, n = [], 0, len(want)
    stutter_of: dict[int, set] = {}
    while i < n:
        for size in (3, 2, 1):
            if i + 2 * size > n:
                continue
            first, second = norms[i:i + size], norms[i + size:i + 2 * size]
            if not all(first) or first != second or any(editor._CJK.search(x) for x in first):
                continue
            if any(editor.SENTENCE_END.search(want[j]["raw"].strip()) for j in range(i, i + size)):
                continue  # "No. No." is said on purpose
            a, b = want[i + size - 1], want[i + size]
            straight = a["clip_id"] == b["clip_id"] and b["i"] == a["i"] + 1
            if straight:  # said like that: only a stutter is a problem
                if a["clip_id"] not in stutter_of:
                    stutter_of[a["clip_id"]] = editor.stutters(editor._words(clips[a["clip_id"]]))
                if want[i]["i"] not in stutter_of[a["clip_id"]]:
                    continue  # doubled on purpose ("very very"), or with a real pause between
            span = want[i:i + 2 * size]
            said = " ".join(w["raw"].strip() for w in span)
            twice = heard is not None and sum(
                1 for h in heard if editor._same_word(str(h.get("word", ""))) == first[0]
                and span[0]["start"] - 0.6 <= float(h.get("start") or 0) <= span[-1]["end"] + 0.6) >= 2
            phrase = _quote([w["raw"] for w in span[:size]])
            why = ("Two takes meet at the cut, so the same words are heard twice." if not straight else
                   "A stutter is left in, so the words are heard twice.") + (" Heard again: twice." if twice else "")
            out.append(_fix("doubled", f"Cut the repeated \"{phrase}\" at {_mmss(span[0]['start'])}", why,
                            span[0]["start"], span[size - 1]["end"],
                            _action("drop_words", start=span[0]["start"], end=span[size - 1]["end"], **{"from": said}),
                            phrase, _around(want, span[size]["start"])))
            i += 2 * size
            break
        else:
            i += 1
    return out


def _broken(parts: list[dict], clips: dict, want: list[dict]) -> list[dict]:
    """Joins that leave a sentence broken: real words cut from inside it (one clip), or a shot that
    stops partway through a sentence where the next shot doesn't carry on with it."""
    import fix_actions
    out = []
    spoken = [p for p in parts if any(w["piece"] == p["k"] for w in want)]
    for a, b in zip(parts, parts[1:]):
        if a["carries"] or b["cut"].get("sentence_ok"):
            continue
        t = b["d0"]
        j = editor._broken_join(a["cut"], b["cut"], clips)
        if j and _retaken(j):
            continue  # the words cut are a stutter or a false start of the words kept after them
        if j:
            cut = [j["words"][k]["word"] for k in j["cut"]]
            text = " ".join(w.strip() for w in cut)
            action = (_action("restore_words", start=t, **{"from": text}) if len(cut) <= fix_actions.MAX_RESTORE_WORDS
                      else _action("replan", start=t, reason="a sentence is broken by a long cut"))
            out.append(_fix("broken", f"Bring back \"{_quote(cut, 6)}\" at {_mmss(t)}",
                            "Words were cut from the middle of a sentence, so it doesn't read right.", t, None,
                            action, _quote(cut, 6), _around(want, t)))
    # A shot that stops mid-sentence (not the last one that speaks: that's the ending's).
    for p in spoken[:-1]:
        mine = [w for w in want if w["piece"] == p["k"]]
        last = mine[-1]
        if p["carries"] or editor.SENTENCE_END.search(last["raw"].strip()):
            continue
        nxt = next((w for w in want if w["start"] >= p["d1"] - 0.01), None)
        words = editor._words(clips[p["clip_id"]])
        if editor.ends_clause(words, last["i"]):
            continue  # a whole clause, before an "and" or "so" that starts the next one
        free = editor.droppable(words)
        if nxt and nxt["clip_id"] == p["clip_id"] and nxt["i"] > last["i"] and all(
                k in free for k in range(last["i"] + 1, nxt["i"])):
            continue  # it carries on with the next word (only a pause or an "um" between)
        if nxt and nxt["clip_id"] == p["clip_id"] and nxt["i"] <= last["i"]:
            continue  # the next shot goes back and says it again (a retake): the repeat is "doubled"
        if nxt and nxt["clip_id"] == p["clip_id"] and editor._broken_join(p["cut"], parts[p["k"] + 1]["cut"], clips):
            continue  # flagged above
        nxt_part = parts[p["k"] + 1] if p["k"] + 1 < len(parts) else None
        if nxt_part and nxt_part["cut"].get("sentence_ok"):
            continue
        rest = editor._rest_of_sentence(words, words[last["i"]])
        t = p["d1"]
        action = (_action("restore_words", start=t, **{"from": " ".join(w["word"].strip() for w in rest)}) if rest
                  else _action("replan", start=t, reason="a shot stops partway through a sentence"))
        out.append(_fix("broken", f"Finish the sentence that stops at {_mmss(t)}",
                        "The shot ends partway through a sentence and the next shot doesn't carry it on.", t, None,
                        action, last["raw"].strip(), _around(want, t)))
    return out


def _retaken(j: dict) -> bool:
    """A join's cut words said again (whole or begun) by the words kept after it: "excite | excites",
    "we use the | we always use the" (fix_actions' own test for a stutter or a false start)."""
    import fix_actions
    cut = [fix_actions._norm(j["words"][k]["word"]) for k in j["real"]]
    after = [fix_actions._norm(w["word"]) for w in j["words"][j["first"]:j["first"] + len(cut) + 3]]
    return fix_actions._stutter(cut, after) or editor._restart(cut, after)


def _ending(parts: list[dict], clips: dict, want: list[dict]) -> list[dict]:
    """The voice ends partway through a sentence."""
    if not want:
        return []
    last = want[-1]
    if editor.SENTENCE_END.search(last["raw"].strip()):
        return []
    p = parts[last["piece"]]
    words = editor._words(clips[last["clip_id"]])
    if editor.ends_clause(words, last["i"]):  # "...left there | and ...": a whole clause
        return []
    rest = editor._rest_of_sentence(words, words[last["i"]])
    t = p["d1"]
    action = (_action("restore_words", start=t, **{"from": " ".join(w["word"].strip() for w in rest)}) if rest
              else _action("replan", start=t, reason="the draft ends partway through a sentence"))
    return [_fix("ends_mid", "Finish the last sentence" + (f": bring back \"{_quote([w['word'] for w in rest], 5)}\""
                                                            if rest else ""),
                 f"The voice stops partway through a sentence, on \"{last['raw'].strip()}\".", last["end"], t,
                 action, last["raw"].strip(), _around(want, last["end"], 5, 0))]


# ── Watch through ─────────────────────────────────────────────────────────────

def frames(video: Path) -> np.ndarray:
    """The draft as small grey frames, SAMPLE_FPS a second (frame n is at n / SAMPLE_FPS)."""
    cmd = [extract.resolve(settings.FFMPEG), "-v", "error", "-i", str(video), "-an",
           "-vf", f"fps={SAMPLE_FPS},scale={FRAME_W}:{FRAME_H},format=gray", "-f", "rawvideo", "-"]
    result = subprocess.run(cmd, capture_output=True, creationflags=getattr(extract, "_NO_WINDOW", 0))
    if result.returncode != 0:
        raise RuntimeError("ffmpeg failed: " + result.stderr.decode("utf-8", "replace").strip()[-200:])
    data = np.frombuffer(result.stdout, dtype=np.uint8)
    n = len(data) // (FRAME_W * FRAME_H)
    return data[:n * FRAME_W * FRAME_H].reshape(n, FRAME_H, FRAME_W)


def _text_box(text: str, sub: str, look: str, kind: str) -> tuple[float, float, float, float]:
    """Where a title's words sit on the Reel (x0, y0, x1, y1 in its pixels), from the overlay's
    layout (editor._SITE_CSS / _GALLERY_CSS / _OVERLAY_CSS): Site Notes' labels stacked from x 64, y 300,
    measured as the overlay sets them (editor.site_layout); the serif title at top 318 (gallery), the boxed
    hook at 250 or the callout at 300 (classic), an estimate from the text's length."""
    n = max(len(text), 1)
    if look == "site":
        return editor.site_layout(kind, text, "", [sub] if sub else [])["box"]
    if look == "gallery":
        px = editor._title_px(text)
        width = 848.0
        lines = max(1, math.ceil(n * 0.42 * px / width))
        h = lines * 0.96 * px + 68 + (38 * 1.3 * max(1, math.ceil(len(sub) * 0.5 * 38 / width)) if sub else 0)
        return 92.0, 318.0, 92.0 + min(width, n * 0.42 * px), 318.0 + h
    if kind == "hook":
        lines = max(1, math.ceil(n * 30.8 / 832))
        w = min(900.0, n * 30.8 + 68)
        return (editor.W - w) / 2, 250.0, (editor.W + w) / 2, 250.0 + lines * 65 + 42
    return 70.0, 300.0, editor.W - 70.0, 300.0 + 68 + 112 + (72 if sub else 0)


def shown(edl: dict, total: float) -> list[dict]:
    """What the draft shows over the footage, in time order: the hook and every graphic, each with
    {n (its number in the read, None for the hook), kind, style, start, end, text, box (None when
    it fills the screen: a card, a full-screen drawing, the end card; Site Notes' card is a panel or labels over
    the footage, so it has its box), g (the graphic)}; on a slideshow
    Reel each slide too (kind "slide", n None, slide: its number). Site Notes' boxes are measured in the draft's
    own titles font (its brand kit)."""
    import brand_kit
    with brand_kit.measuring(editor.draft_look(edl).get("kit")):
        return _shown(edl, total)


def _shown(edl: dict, total: float) -> list[dict]:
    look = editor.draft_look(edl)["titles"]
    close = edl.get("close") or {}
    card = close.get("card")
    close_at = float(close["at"]) if close.get("at") is not None else total
    numbered = [(n, g) for n, g in enumerate(edl.get("graphics") or [], 1) if "start" in g]
    titles = [(n, g) for n, g in numbered if g.get("style") in ("card", "callout")]
    out = []
    hook, _ = editor.title_parts(edl.get("hook_text") or "")
    if hook:
        cutters = [(n, g) for n, g in titles if g["start"] > 0.8]
        end = min([editor.HOOK_SECONDS_SHOWN, close_at if card else total] + [g["start"] for _, g in cutters])
        by = min(cutters, key=lambda x: x[1]["start"])[0] if cutters and end < editor.HOOK_SECONDS_SHOWN - 0.01 else None
        out.append({"n": None, "kind": "hook", "style": "hook", "start": 0.0, "end": end, "text": hook,
                    "box": _text_box(hook, "", look, "hook"), "g": None, "cut_by": by})
    for n, g in numbered:
        style = g.get("style")
        s, e = float(g["start"]), float(g["start"]) + float(g["duration"])
        lines = [editor.unmark(x) for x in g.get("lines") or [] if editor.unmark(x)]
        title = editor.unmark(g.get("title") or "")
        if style in ("scene", "picture"):
            box = (40.0, float(editor.PANEL_TOP), editor.W - 40.0, float(editor.PANEL_BOTTOM)) if g.get("look") == "panel" else None
            out.append({"n": n, "kind": "drawing" if style == "scene" else "picture", "style": style, "start": s, "end": e,
                        "text": title, "box": box, "g": g})
        elif style == "card":
            box = editor.site_layout("card", title, g.get("italic") or "", lines)["box"] if look == "site" else None
            out.append({"n": n, "kind": "card", "style": style, "start": s, "end": e,
                        "text": " ".join([title] + lines), "box": box, "g": g})
        else:
            sub = lines[0] if lines else ""
            out.append({"n": n, "kind": "callout", "style": style, "start": s, "end": e,
                        "text": " ".join(x for x in (title, sub) if x), "box": _text_box(title, sub, look, "callout"),
                        "g": g})
    for x in (edl.get("slideshow") or {}).get("slides") or []:
        out.append({"n": None, "kind": "slide", "style": "slide", "start": float(x["start"]),
                    "end": float(x["start"]) + float(x["seconds"]), "text": x.get("text") or "", "box": None, "g": None,
                    "slide": x["n"]})
    return sorted(out, key=lambda x: x["start"])


class _Faces:
    """Where the face is in the draft's frames, looked for only where it's asked about (and kept)."""

    def __init__(self, grey: np.ndarray, parts: list[dict]):
        self.grey, self.parts, self.seen = grey, parts, {}
        # A shot: pieces of one clip framed alike in a row (a jump cut doesn't move the face).
        self.shots, n, before = [], -1, None
        for p in parts:
            c = p["cut"]
            frame = (p["clip_id"], c.get("crop_x"), c.get("crop_y"), c.get("zoom"))
            n += frame != before
            before = frame
            self.shots.append(n)

    def _shot(self, t: float) -> int:
        return next((self.shots[k] for k, p in enumerate(self.parts) if p["d0"] - 0.001 <= t < p["d1"]), -1)

    def found(self, i: int):
        """The face found in frame i (x0, y0, x1, y1 on the Reel), or None."""
        if i not in self.seen:
            self.seen[i] = None
            if 0 <= i < len(self.grey):
                faces = framing.find_faces(self.grey[i])
                if faces:
                    x, y, size = max(faces, key=lambda f: f[2])
                    cx, cy, s = x * editor.W, y * editor.H, size * editor.W * 1.1
                    self.seen[i] = (cx - s / 2, cy - s / 2, cx + s / 2, cy + s / 2)
        return self.seen[i]

    def at(self, i: int):
        """The face in frame i: found there, else where it was in the nearest frame of the same
        shot (a title over it can hide it from the finder)."""
        f = self.found(i)
        if f is not None:
            return f
        k = self._shot(i / SAMPLE_FPS)
        for d in range(1, int(FACE_BORROW * SAMPLE_FPS) + 1):
            for j in (i - d, i + d):
                if 0 <= j < len(self.grey) and self._shot(j / SAMPLE_FPS) == k and self.found(j) is not None:
                    return self.seen[j]
        return None


def _covered(face, box) -> bool:
    if face is None or box is None:
        return False
    w = min(face[2], box[2]) - max(face[0], box[0])
    h = min(face[3], box[3]) - max(face[1], box[1])
    return w > 0 and h > 0 and w * h >= COVER_SHARE * (face[2] - face[0]) * (face[3] - face[1])


def _cover_run(faces: _Faces, box, start: float, end: float) -> float:
    """The longest stretch (seconds) the box covers the face between start and end."""
    best = run = 0
    for i in range(int(math.ceil(start * SAMPLE_FPS)), int(end * SAMPLE_FPS) + 1):
        run = run + 1 if _covered(faces.at(i), box) else 0
        best = max(best, run)
    return best / SAMPLE_FPS


def _need(text: str) -> float:
    """How long words must stay up to be read."""
    return max(READ_MIN, READ_BASE + READ_PER_CHAR * len(text.strip()))


def watch(job: dict) -> tuple[list[dict], int]:
    """The watch-through's flags, and how many frames were looked at."""
    edl, clips, total = job["edl"], job["clips"], job["total"]
    cuts = [c for c in edl.get("cuts", []) if c["clip_id"] in clips]
    grey = frames(job["video"])
    total = total or len(grey) / SAMPLE_FPS
    parts = pieces(cuts) if cuts else []
    items = shown(edl, total)
    close = edl.get("close") or {}
    close_at = float(close["at"]) if close.get("at") is not None else total
    clear_by = close_at - editor.END_CLEAR
    faces = _Faces(grey, parts)
    out = []
    graphics = [x for x in items if x["n"] is not None]
    for x in items:
        length = x["end"] - x["start"]
        name = "the hook" if x["kind"] == "hook" else f"the {x['kind']} \"{_quote([x['text']], 5)}\""
        # Over the face
        if x["box"] is not None and _cover_run(faces, x["box"], x["start"], x["end"]) > COVER_SECONDS:
            out.append(_cover_fix(x, name, faces, graphics, clear_by))
        # Up too briefly, or gone before its words can be read
        if x["kind"] == "slide":
            import slideshow
            need = min(_need(x["text"]), slideshow.MAX_SECONDS)
            if length < need - 0.05:
                out.append(_fix("too_fast", f"Leave slide {x['slide']} up longer ({need:.1f}s) at {_mmss(x['start'])}",
                                f"It's up for {length:.1f}s; its words need about {need:.1f}s to read.", x["start"],
                                x["end"], _action("replan", start=x["start"], end=x["end"],
                                                  reason=f"slide {x['slide']} up {need:.1f}s"), x["text"], area="captions"))
        elif x["kind"] != "hook" or x.get("cut_by"):
            need = _need(x["text"]) if x["text"] and x["kind"] != "picture" else FLASH
            if x["kind"] == "picture":  # nothing to read (but its mark): long enough to see what it shows
                import pictures
                need = pictures.SEE_SECONDS
            if length < FLASH or length < need - 0.05:
                out.append(_time_fix(x, name, length, need, graphics, clear_by))
        # Still on at the close
        if x["n"] is not None and x["end"] > clear_by + 0.05:
            out.append(_late_fix(x, name, clear_by, graphics))
    out += _behind_face(edl, faces)
    # The same animation twice back to back
    for a, b in zip(graphics, graphics[1:]):
        if b["start"] - a["end"] < REPEAT_GAP and _same(a, b, grey):
            out.append(_fix("repeat", f"Remove the second \"{_quote([b['text']], 5)}\" at {_mmss(b['start'])}",
                            "The same animation plays twice in a row.", b["start"], b["end"],
                            _action("graphic", start=b["start"], item=b["n"], field="remove"), b["text"]))
    # The end card still moving in the very last moments: the video ends mid-animation
    if close.get("card") and len(grey) >= SAMPLE_FPS:
        a = grey[max(len(grey) - int(0.45 * SAMPLE_FPS), 0)].astype(np.int16)
        b = grey[-1].astype(np.int16)
        moved = float(np.mean(np.abs(a - b) > MOVED_LEVELS))
        if moved > MOVED_SHARE:
            out.append(_fix("late", "Check the very end: it's still moving as the video stops",
                            f"{moved:.0%} of the picture still changes in the last half second.",
                            total - 0.5, total, None, "the end card",
                            left_note="Studio can't change how the end card moves; worth a look before posting."))
    return out, len(grey)


FACE_CORE = 0.6          # the middle of the face finder's box: all face, no background beside it
BEHIND_OVER = 0.1        # words behind him over this share of that middle: the cut-out missed his face


def _behind_face(edl: dict, faces: "_Faces") -> list[dict]:
    """Words behind him (behind.py) that show over his face: his shape is cut out of them, so where the face finder
    (not the person finder that made the cut-out) sees his face, the layer's own words must not be. A broken cut-out
    is flagged like a title over the face, and the fix takes those words off."""
    import behind
    out = []
    sx, sy = FRAME_W / editor.W, FRAME_H / editor.H
    for n, b in enumerate(edl.get("behind") or [], 1):
        f = editor.INSERTS / f"{b.get('file')}.webm"
        if not b.get("file") or not f.exists():
            continue
        alpha = behind.alpha_frames(f, SAMPLE_FPS, (FRAME_W, FRAME_H))
        run = best = 0
        for k in range(len(alpha)):
            face = faces.at(int(round((b["start"] + k / SAMPLE_FPS) * SAMPLE_FPS)))
            hit = False
            if face is not None:
                cx, cy = (face[0] + face[2]) / 2 * sx, (face[1] + face[3]) / 2 * sy
                hw, hh = (face[2] - face[0]) * sx * FACE_CORE / 2, (face[3] - face[1]) * sy * FACE_CORE / 2
                core = alpha[k, max(0, int(cy - hh)):int(cy + hh) + 1, max(0, int(cx - hw)):int(cx + hw) + 1]
                hit = core.size > 0 and float((core > 128).mean()) >= BEHIND_OVER
            run = run + 1 if hit else 0
            best = max(best, run)
        if best / SAMPLE_FPS > COVER_SECONDS:
            end = b["start"] + b["duration"]
            out.append(_fix("covers_face", f"Take the words \"{b['words']}\" from behind you at {_mmss(b['start'])}",
                            "They show over your face: Studio's cut-out of you missed there.", b["start"], end,
                            _action("behind", start=b["start"], end=end, item=n, field="remove"), b["words"]))
    return out


def _same(a: dict, b: dict, grey: np.ndarray) -> bool:
    """Do two graphics look like the same animation? The same words, the same drawing, or a
    drawing of the same kind whose frames look alike."""
    norm = lambda s: re.sub(r"\W+", "", str(s or "").lower())  # noqa: E731
    if a["text"] and norm(a["text"]) == norm(b["text"]):
        return True
    ga, gb = a["g"] or {}, b["g"] or {}
    if a["kind"] == "picture" and b["kind"] == "picture":
        return bool(ga.get("picture")) and ga.get("picture") == gb.get("picture")
    if a["kind"] != "drawing" or b["kind"] != "drawing":
        return False
    if ga.get("scene") and json.dumps(ga.get("scene"), sort_keys=True) == json.dumps(gb.get("scene"), sort_keys=True):
        return True
    if ga.get("type") != gb.get("type") or ga.get("look") != gb.get("look"):
        return False
    diffs = []
    for o in (0.5, 1.0, 1.5, 2.0):
        if o < min(a["end"] - a["start"], b["end"] - b["start"]):
            i, j = int((a["start"] + o) * SAMPLE_FPS), int((b["start"] + o) * SAMPLE_FPS)
            if j < len(grey):
                diffs.append(float(np.mean(np.abs(grey[i].astype(np.float32) - grey[j].astype(np.float32)))))
    return bool(diffs) and float(np.mean(diffs)) < SAME_PICTURE


def _free(start: float, end: float, x: dict, graphics: list[dict], clear_by: float) -> bool:
    """Is there room for graphic x between start and end?"""
    if start < editor.HOOK_SECONDS_SHOWN - 0.1 or end > clear_by + 0.001:
        return False
    return not any(o is not x and start < o["end"] + 0.25 and end > o["start"] - 0.25 for o in graphics)


def _cover_fix(x: dict, name: str, faces: _Faces, graphics: list[dict], clear_by: float) -> dict:
    """A title over the face: moved to the nearest moment it's clear of it (within SLOT_SEARCH), else
    the editor places it again (a hook covering the face needs fewer words or a different shot)."""
    t, length = x["start"], x["end"] - x["start"]
    what = f"Move {name} off the face at {_mmss(t)}"
    why = "It covers the face for more than a moment."
    if x["kind"] == "hook":
        return _fix("covers_face", f"Keep the hook clear of the face at {_mmss(t)}", why, t, x["end"],
                    _action("replan", start=t, end=x["end"], reason="the hook title covers the face: a shorter hook"),
                    x["text"], area="hook")
    if x["kind"] in ("drawing", "picture"):
        return _fix("covers_face", f"Show {name} full screen at {_mmss(t)}", "As a panel it covers the face.",
                    t, x["end"], _action("replan", start=t, end=x["end"], reason="the panel covers the face: full screen"),
                    x["text"])
    steps = int(SLOT_SEARCH * SAMPLE_FPS)
    for d in sorted(range(-steps, steps + 1), key=abs):
        s = round(t + d / SAMPLE_FPS, 2)
        if d and _free(s, s + length, x, graphics, clear_by) and _cover_run(faces, x["box"], s, s + length) == 0:
            return _fix("covers_face", what, why + f" Clear of it from {_mmss(s)}.", t, x["end"],
                        _action("graphic", start=s, item=x["n"], field="time", amount=round(length, 2)), x["text"])
    return _fix("covers_face", what, why, t, x["end"],
                _action("replan", start=t, end=x["end"], reason="a title covers the face: put it where the face isn't"),
                x["text"])


def _time_fix(x: dict, name: str, length: float, need: float, graphics: list[dict], clear_by: float) -> dict:
    """A graphic up too briefly: longer (its words' reading time; earlier when that would run into
    the close), else fewer words via a new plan."""
    kind = "flash" if length < FLASH else "too_fast"
    t = x["start"]
    why = (f"It's on screen for only {length:.1f}s, a flash." if kind == "flash" else
           f"It leaves after {length:.1f}s; its words need about {need:.1f}s to read.")
    if x["kind"] == "hook":  # the hook gives way to the first card: that card starts later
        nxt = next(g for g in graphics if g["n"] == x["cut_by"])
        return _fix(kind, f"Give the hook its time: start the next title at {_mmss(need + 0.1)}", why, t, x["end"],
                    _action("graphic", start=round(need + 0.1, 2), item=nxt["n"], field="time",
                            amount=round(nxt["end"] - nxt["start"], 2)), x["text"], area="hook")
    longest = editor.SCENE_LONGEST if x["kind"] in ("drawing", "picture") else CARD_LONGEST
    start = min(t, clear_by - need)
    if need > longest + 0.05 or start < editor.HOOK_SECONDS_SHOWN:
        return _fix(kind, f"Put fewer words on {name} at {_mmss(t)}", why, t, x["end"],
                    _action("replan", start=t, end=x["end"], reason="too many words to read in the time"), x["text"])
    return _fix(kind, f"Keep {name} up {need:.1f}s at {_mmss(t)}", why, t, x["end"],
                _action("graphic", start=round(start, 2), item=x["n"], field="time", amount=round(max(need, FLASH), 2)),
                x["text"])


def _late_fix(x: dict, name: str, clear_by: float, graphics: list[dict]) -> dict:
    """A graphic still on at the close: shortened, moved earlier (clear of the others), or (no room)
    taken off, as editor.clear_of_end does."""
    t, length = x["start"], x["end"] - x["start"]
    shortest = editor.SCENE_MIN if x["kind"] in ("drawing", "picture") else max(READ_MIN, _need(x["text"]) if x["text"] else 1.0)
    why = "It's still on as the video closes, so it ends mid-animation."
    if clear_by - t >= shortest:
        action = _action("graphic", start=t, item=x["n"], field="time", amount=round(clear_by - t, 2))
    elif _free(clear_by - length, clear_by, x, graphics, clear_by):
        action = _action("graphic", start=round(clear_by - length, 2), item=x["n"], field="time", amount=round(length, 2))
    else:
        action = _action("graphic", start=t, item=x["n"], field="remove")
    return _fix("late", f"Finish {name} before the end ({_mmss(clear_by)})", why, t, x["end"], action, x["text"])


# ── Into Studio's read ────────────────────────────────────────────────────────

def _same_place(ours: dict, theirs: dict) -> bool:
    """Is one of Claude's fixes the same change as one of ours (so it isn't listed twice)?"""
    a, b = ours.get("action") or {}, theirs.get("action") or {}
    if a.get("type") != b.get("type") or a.get("type") in (None, "none", "replan"):
        return False
    if a["type"] == "graphic":
        return a.get("item") is not None and a.get("item") == b.get("item")
    if ours.get("start") is None or theirs.get("start") is None:
        return False
    return abs(ours["start"] - theirs["start"]) <= 1.5


def merge(review: dict, pending: Future | None, wait: float = WAIT) -> dict:
    """Studio's read with the check's flags among its fixes (first, and Claude's own fix for the
    same change dropped), the scores and verdict brought in line, and what the check did
    (review["final_check"]). The read as it was when there's nothing to add. Never raises."""
    if pending is None:
        return review
    try:
        result = pending.result(timeout=wait)
    except Exception as e:  # too slow, or failed: the read stands
        log.warning("Studio's listen-back didn't finish in time (%s); the read goes without it", type(e).__name__)
        return review
    if not result:
        return review
    try:
        flags = result["flags"]
        theirs = [f for f in review.get("fixes", []) if not any(_same_place(o, f) for o in flags)]
        review["fixes"] = flags + theirs
        scores = review.setdefault("scores", {})
        for f in flags:
            if f["area"] in scores:
                scores[f["area"]] = "fix"
        todo = sum(1 for f in review["fixes"] if f.get("when", "this_draft") == "this_draft")
        if flags and not re.match(r"(?i)\s*needs work", review.get("verdict", "")):
            review["verdict"] = f"Nearly ready: {todo} thing{'s' if todo != 1 else ''} to fix"
        review["final_check"] = {key: result.get(key) for key in ("seconds", "listened", "watched", "heard", "frames")}
        review["final_check"]["flags"] = len(flags)
    except Exception as e:
        log.warning("Studio couldn't add its listen-back to the read: %s: %s", type(e).__name__, e)
    return review
