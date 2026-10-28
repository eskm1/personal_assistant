"""
Studio's own fixes made as small, exact changes ("patches") instead of a new edit plan.

Studio's read (draft_review.py) gives every fix the editor can make a machine action, with the
exact words and times from the draft it watched. Before, a fix round asked Claude to plan the whole
edit again with the fixes as notes: one fix asked for, the whole cut redone, new slips in, the round
thrown away. Here each action changes only what it names, on the draft's own edit plan (EDL), and the
draft is rendered again with every code check (voice edges, the clipped-word check, loudness):

  caption_word    a caption shows a wrong word: a correction on that word (edl["corrections"],
                  by clip, so it holds through re-cuts, re-renders and Resolve); the words as said stay
  drop_words      a stutter, a repeat or a false start: those words cut out at the voice's edges
  restore_words   words cut at a join (or the draft's start or end) come back
  trim_pause      a dead pause cut down to a breath
  move_cut        a cut point moved by some seconds, to the edge of a word
  graphic         a card, callout, drawn animation or picture (by its number in the read) moved, retimed,
                  reworded or removed, or made a picture ("picture": an AI picture of what it shows, pictures.py)
  hook_text       the hook reworded (its *marked* word, set in the look's accent: brass, or the gallery's italic)
  music_level     the music turned down or up by some dB
  sound_effects   the soft sound effects (sfx.py): one taken off (by its number in the read), all off, or all
                  quieter or louder by some dB
  behind          words behind Bryan (behind.py): one taken off (by its number in the read, B2 = 2) or all, or
                  words put behind him from a start to an end
  layout          a voice-over's picture (voiceover.py): Bryan full screen for a stretch ("full"), the site
                  footage and his window back there ("window"), or his window moved "left" or "right"
  replan          only when nothing smaller fits: the old way, a new edit plan from the editor

Carousels have slide_text (a slide's heading or body, or the caption: carousel.patch) and replan. The
vocabulary itself is draft_review.REEL_ACTIONS / CAROUSEL_ACTIONS (the read's schema).

apply() never guesses: an action whose words or times can't be found in the draft, or that would
break a sentence or change a number, is not applied and says why, so the round can hand it to the
editor's re-plan instead. Times in the read are on the draft it watched; every action is worked out
against that draft before any is made, and the cards move with the footage they sat on.
"""
import copy
import difflib
import logging
import re

import editor
import voice_edges

log = logging.getLogger("studio.fix_actions")

PATCHES = ("caption_word", "drop_words", "restore_words", "trim_pause", "move_cut", "graphic", "hook_text",
           "music_level", "sound_effects", "layout", "behind", "slide_text")
CUTS = ("drop_words", "restore_words", "trim_pause", "move_cut")

NEAR = 3.0               # seconds: how far from the read's time its words or join may be
MAX_DROP_WORDS, MAX_DROP_SECONDS = 10, 5.0
MAX_RESTORE_WORDS = 25
MAX_MOVE = 6.0
MIN_SAVED = 0.1          # a trim that saves less than this did nothing
MUSIC_RANGE = (-30.0, -8.0)
CARD_FIRST = 2.5         # cards stay clear of the hook (editor.place_graphics)


class NotApplied(Exception):
    """An action Studio couldn't make as asked; its message says why, in plain words."""


def _mmss(t) -> str:
    t = max(float(t or 0), 0.0)
    return f"{int(t // 60)}:{int(t % 60):02d}"


def _norm(word: str) -> str:
    return editor._same_word(str(word or ""))


def _tokens(text: str) -> list[str]:
    return [t for t in (_norm(x) for x in editor.unmark(text).split()) if t]


def _num(v):
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


# ── The draft as the read saw it ──────────────────────────────────────────────

class Draft:
    """The draft's pieces with their words on its clock (shown text, the words as heard, and
    where each sits in its clip)."""

    def __init__(self, cuts: list[dict], clips: dict[int, dict]):
        self.cuts, self.clips = cuts, clips
        self.offsets, self.words, off = [], [], 0.0
        for k, c in enumerate(cuts):
            self.offsets.append(off)
            say = c.get("say") or {}
            for i, w in enumerate(self.clip_words(c["clip_id"])):
                if w["start"] >= c["start"] - 0.01 and w["end"] <= c["end"] + 0.01:
                    shown = say.get(f"{w['start']:.2f}", w["word"])
                    self.words.append({"word": shown, "raw": w["word"], "start": off + w["start"] - c["start"],
                                       "end": off + w["end"] - c["start"], "piece": k, "clip_id": c["clip_id"],
                                       "i": i, "src": w["start"], "src_end": w["end"]})
            off += c["end"] - c["start"]
        self.total = off
        self.offsets.append(off)

    def clip_words(self, cid: int) -> list[dict]:
        return editor._words(self.clips[cid]) if cid in self.clips else []

    def to_source(self, t: float) -> tuple[int, float] | None:
        for k, c in enumerate(self.cuts):
            if self.offsets[k] - 0.001 <= t <= self.offsets[k + 1] + 0.001:
                return c["clip_id"], c["start"] + (t - self.offsets[k])
        return None

    def to_draft(self, cid: int, src: float) -> float | None:
        for k, c in enumerate(self.cuts):
            if c["clip_id"] == cid and c["start"] - 0.01 <= src <= c["end"] + 0.01:
                return self.offsets[k] + min(max(src - c["start"], 0.0), c["end"] - c["start"])
        later = [k for k, c in enumerate(self.cuts) if c["clip_id"] == cid and c["start"] >= src]
        return self.offsets[min(later, key=lambda k: self.cuts[k]["start"])] if later else None

    def find(self, text: str, near: float | None, window: float = NEAR, raw: bool = False) -> tuple[int, int] | None:
        """The words saying `text` (as shown, or as heard), the run nearest `near`: (first, last)."""
        toks = _tokens(text)
        if not toks:
            return None
        n, cands = len(toks), []
        for i in range(len(self.words) - n + 1):
            seq = self.words[i:i + n]
            if all(_norm(w["raw"] if raw else w["word"]) == t or _norm(w["raw"]) == t for w, t in zip(seq, toks)):
                cands.append(i)
        if not cands and n <= 3:  # the read wrote it a little differently ("MCSC," / "mcsc")
            joined = "".join(toks)
            for i in range(len(self.words) - n + 1):
                said = "".join(_norm(w["word"]) for w in self.words[i:i + n])
                if said and difflib.SequenceMatcher(None, said, joined).ratio() >= 0.8:
                    cands.append(i)
        if near is not None:
            def dist(i):
                a, b = self.words[i]["start"], self.words[i + n - 1]["end"]
                return 0.0 if a - 0.3 <= near <= b + 0.3 else min(abs(near - a), abs(near - b))
            cands = [i for i in cands if dist(i) <= window]
            cands.sort(key=dist)
        return (cands[0], cands[0] + n - 1) if cands else None

    def boundary(self, t: float | None, window: float = NEAR) -> int:
        """The cut point nearest t: 0 = the draft's start, len(cuts) = its end."""
        if t is None:
            raise NotApplied("the read gave no time for the cut")
        b = min(range(len(self.offsets)), key=lambda k: abs(self.offsets[k] - t))
        if abs(self.offsets[b] - t) > window:
            raise NotApplied(f"there's no cut near {_mmss(t)}")
        return b


def _clip(draft: Draft, cid: int) -> tuple[dict, list[dict], object]:
    clip = draft.clips[cid]
    return clip, draft.clip_words(cid), voice_edges.voice(clip)


def _after(draft: Draft, cid: int, k: int, pad: float) -> float:
    """A cut just after word k of the clip, at the voice's real end."""
    clip, words, v = _clip(draft, cid)
    dur = clip.get("duration") or words[k]["end"] + pad
    nxt = words[k + 1]["start"] if k + 1 < len(words) else None
    base = min(words[k]["end"] + pad, nxt if nxt is not None else dur, dur)
    return min(voice_edges.end_point(v, words[k]["end"], base, next_start=nxt, limit=dur), dur)


def _before(draft: Draft, cid: int, k: int, pad: float) -> float:
    """A cut just before word k of the clip, at the voice's real start."""
    _, words, v = _clip(draft, cid)
    prv = words[k - 1]["end"] if k else None
    base = max(words[k]["start"] - pad, prv if prv is not None else 0.0, 0.0)
    return max(voice_edges.start_point(v, words[k]["start"], base, prev_end=prv), 0.0)


# ── Changes to the cut list, worked out against the draft the read saw ────────
# Each is (kind, clip_id, a, b): "remove" the clip's seconds a-b wherever a piece holds them;
# "end"/"start": the piece of that clip whose end/start is a moves to b; "insert_after"/"insert_before"
# (kind, clip_id, a, piece): the new piece next to the piece of that clip ending/starting at a.

def _plan_drop(draft: Draft, a: dict) -> tuple[list[tuple], str]:
    found = draft.find(a.get("from") or "", _num(a.get("start")), raw=True)
    if found is None and _num(a.get("start")) is not None and _num(a.get("end")) is not None and not _tokens(a.get("from") or ""):
        inside = [k for k, w in enumerate(draft.words) if w["start"] >= a["start"] - 0.1 and w["end"] <= a["end"] + 0.1]
        found = (inside[0], inside[-1]) if inside else None
    if found is None:
        raise NotApplied(f"couldn't find \"{a.get('from') or ''}\" near {_mmss(a.get('start'))}")
    i, j = found
    span = draft.words[i:j + 1]
    # "the the", "excite excites": the first copy goes, the second stays.
    half = len(span) // 2
    if len(span) % 2 == 0 and half and _stutter([_norm(w["raw"]) for w in span[:half]], [_norm(w["raw"]) for w in span[half:]]):
        span = span[:half]
    cid = span[0]["clip_id"]
    if any(w["clip_id"] != cid for w in span) or [w["i"] for w in span] != list(range(span[0]["i"], span[-1]["i"] + 1)):
        raise NotApplied("those words aren't said in one go")
    if len(span) > MAX_DROP_WORDS or span[-1]["src_end"] - span[0]["src"] > MAX_DROP_SECONDS:
        raise NotApplied("that's more than a stutter or a repeat; it needs a new edit")
    if len(span) >= len(draft.words):
        raise NotApplied("that would cut every word")
    words = draft.clip_words(cid)
    lo, hi = span[0]["i"], span[-1]["i"]
    # The same words heard again straight after them in the draft (two takes either side of a join)
    # may go too, though the clip itself goes on differently.
    j = draft.words.index(span[-1])
    heard = [_norm(w["raw"]) for w in draft.words[j + 1:j + 1 + len(span) + 3]]
    cut = [_norm(w["raw"]) for w in span]
    again = _stutter(cut, heard) or editor._restart(cut, heard)
    if not (_droppable(words, lo, hi) or again):
        raise NotApplied("cutting those words would break the sentence they're in")
    cut_from = _after(draft, cid, lo - 1, editor.BREATH_AFTER) if lo > 0 else words[lo]["start"] - 0.02
    cut_to = _before(draft, cid, hi + 1, editor.BREATH_BEFORE) if hi + 1 < len(words) else words[hi]["end"] + 0.02
    cut_from, cut_to = min(cut_from, words[lo]["start"]), max(cut_to, words[hi]["end"])
    said = " ".join(w["raw"].strip() for w in span)
    k = span[0]["piece"]
    if any(n != k and c["clip_id"] == cid and c["start"] < cut_to and c["end"] > cut_from for n, c in enumerate(draft.cuts)):
        # Another shot holds the same seconds of the clip (the repeat): only this shot loses them.
        piece, mine = draft.cuts[k], [w for w in draft.words if w["piece"] == k]
        if any(w["piece"] != k for w in span):
            raise NotApplied("those words run across two shots")
        if mine[-1] is span[-1]:
            return [("end", cid, piece["end"], max(cut_from, piece["start"]))], f"cut \"{said}\" at {_mmss(span[0]['start'])}"
        if mine[0] is span[0]:
            return [("start", cid, piece["start"], min(cut_to, piece["end"]))], f"cut \"{said}\" at {_mmss(span[0]['start'])}"
        raise NotApplied("those words are in two shots")
    return [("remove", cid, cut_from, cut_to)], f"cut \"{said}\" at {_mmss(span[0]['start'])}"


def _stutter(first: list[str], again: list[str]) -> bool:
    """The same words said twice, the first time cut short or not ("the the", "excite excites",
    "rep- replace")."""
    return bool(first) and len(first) <= len(again) and all(
        a and (a == b or (len(a) >= 3 and b.startswith(a))) for a, b in zip(first, again))


def _droppable(words: list[dict], lo: int, hi: int) -> bool:
    """Words Studio may cut by itself: hesitations and stutters, a repeat of the words right after
    them (a false start, editor._restart), or whole sentences. Anything else breaks a sentence."""
    free = editor.droppable(words)
    if all(k in free for k in range(lo, hi + 1)):
        return True
    cut = [_norm(words[k]["word"]) for k in range(lo, hi + 1)]
    after = [_norm(w["word"]) for w in words[hi + 1:hi + 1 + len(cut) + 3]]
    if _stutter(cut, after) or editor._restart(cut, after):
        return True
    starts = lo == 0 or editor.SENTENCE_END.search(words[lo - 1]["word"].strip())
    return bool(starts and editor.SENTENCE_END.search(words[hi]["word"].strip()))


def _plan_pause(draft: Draft, a: dict) -> tuple[list[tuple], str]:
    t0 = _num(a.get("start"))
    if t0 is None:
        raise NotApplied("the read gave no time for the pause")
    t1 = _num(a.get("end")) if _num(a.get("end")) is not None else t0 + 1.5
    t0, t1 = min(t0, t1), max(t0, t1)
    W = draft.words
    if not W:
        raise NotApplied("there's no speech to tighten")
    gaps = [(-1, 0.0, W[0]["start"])] + [(k, W[k]["end"], W[k + 1]["start"]) for k in range(len(W) - 1)] \
        + [(len(W) - 1, W[-1]["end"], draft.total)]
    def overlap(g):
        return min(g[2], t1 + 0.3) - max(g[1], t0 - 0.3)
    picked = [g for g in gaps if overlap(g) > 0 and g[2] - g[1] >= 0.25]
    if not picked:
        raise NotApplied(f"there's no pause near {_mmss(t0)}")
    k, g0, g1 = max(picked, key=lambda g: (overlap(g), g[2] - g[1]))
    ops = []
    if k == len(W) - 1 and g1 - g0 <= editor.END_TAIL + 0.35:
        raise NotApplied("that's the short beat on the last shot before the end card")
    if 0 <= k < len(W) - 1 and not editor.pause_to_cut(W[k]["raw"], g1 - g0, 0.0):
        raise NotApplied("that pause is inside a sentence: Studio keeps it, it's how you said it")
    if k == -1:  # silence before the first word
        w = W[0]
        ops.append(("start", w["clip_id"], draft.cuts[w["piece"]]["start"],
                    max(draft.cuts[w["piece"]]["start"], _before(draft, w["clip_id"], w["i"], editor.HEAD_PAD))))
    elif k == len(W) - 1:  # silence after the last word
        w = W[-1]
        ops.append(("end", w["clip_id"], draft.cuts[w["piece"]]["end"],
                    min(draft.cuts[w["piece"]]["end"], _after(draft, w["clip_id"], w["i"], editor.TAIL_PAD))))
    else:
        a_, b_ = W[k], W[k + 1]
        if b_["piece"] - a_["piece"] > 1:
            raise NotApplied("that pause is footage without talking")
        if a_["piece"] == b_["piece"]:
            cut_from = _after(draft, a_["clip_id"], a_["i"], editor.BREATH_AFTER)
            cut_to = _before(draft, b_["clip_id"], b_["i"], editor.BREATH_BEFORE)
            if cut_to - cut_from >= MIN_SAVED:
                ops.append(("remove", a_["clip_id"], cut_from, cut_to))
        else:
            pa, pb = draft.cuts[a_["piece"]], draft.cuts[b_["piece"]]
            end = min(pa["end"], _after(draft, a_["clip_id"], a_["i"], editor.BREATH_AFTER))
            start = max(pb["start"], _before(draft, b_["clip_id"], b_["i"], editor.BREATH_BEFORE))
            if pa["end"] - end + start - pb["start"] >= MIN_SAVED:
                ops += [("end", a_["clip_id"], pa["end"], end), ("start", b_["clip_id"], pb["start"], start)]
    saved = sum((op[3] - op[2]) if op[0] == "remove" else abs(op[3] - op[2]) for op in ops)
    if saved < MIN_SAVED:
        raise NotApplied("that pause is already as short as a breath")
    return ops, f"shortened the pause at {_mmss(g0)} by {saved:.1f}s"


def _plan_move(draft: Draft, a: dict) -> tuple[list[tuple], str]:
    b = draft.boundary(_num(a.get("start")))
    amount = _num(a.get("amount"))
    if amount is None or abs(amount) < 0.05:
        raise NotApplied("the read didn't say how far to move the cut")
    amount = max(-MAX_MOVE, min(MAX_MOVE, amount))
    side = a.get("field") if a.get("field") in ("before", "after") else ("before" if amount > 0 else "after")
    n = len(draft.cuts)
    if side == "before" and b == 0:
        side = "after"
    if side == "after" and b == n:
        side = "before"
    k = b - 1 if side == "before" else b
    p = draft.cuts[k]
    cid = p["clip_id"]
    clip, words, _ = _clip(draft, cid)
    dur = clip.get("duration") or p["end"]
    same = [c for j, c in enumerate(draft.cuts) if j != k and c["clip_id"] == cid]
    if side == "before":  # the end of the piece before the cut point
        if amount > 0:
            target = p["end"] + amount
            ks = [i for i, w in enumerate(words) if p["end"] - 0.01 <= w["start"] < target]
            new = _after(draft, cid, ks[-1], editor.TAIL_PAD) if ks else min(target, dur)
            new = min([new] + [c["start"] for c in same if c["start"] >= p["end"] - 0.001])
            if new <= p["end"] + 0.02:
                raise NotApplied("there's no more footage after that cut")
        else:
            target = p["end"] + amount
            ks = [i for i, w in enumerate(words) if w["start"] >= p["start"] - 0.01 and w["end"] <= target]
            new = min(p["end"], _after(draft, cid, ks[-1], editor.TAIL_PAD)) if ks else max(target, p["start"] + editor.MIN_CUT)
            if new >= p["end"] - 0.02 or new - p["start"] < editor.MIN_PIECE:
                raise NotApplied("that would leave nothing of the shot before the cut")
            _whole(words, [i for i, w in enumerate(words) if w["start"] >= new - 0.01 and w["end"] <= p["end"] + 0.01],
                   ks[-1] if ks else None, "end")
        what = "kept more before" if new > p["end"] else "cut sooner at"
        return [("end", cid, p["end"], new)], f"{what} the cut at {_mmss(draft.offsets[b])} ({new - p['end']:+.1f}s)"
    if amount > 0:  # the start of the piece after the cut point: start earlier
        target = p["start"] - amount
        ks = [i for i, w in enumerate(words) if target < w["end"] <= p["start"] + 0.01]
        new = _before(draft, cid, ks[0], editor.HEAD_PAD) if ks else max(target, 0.0)
        new = max([new] + [c["end"] for c in same if c["end"] <= p["start"] + 0.001])
        if new >= p["start"] - 0.02:
            raise NotApplied("there's no more footage before that cut")
    else:
        target = p["start"] - amount
        ks = [i for i, w in enumerate(words) if w["start"] >= target and w["end"] <= p["end"] + 0.01]
        new = max(p["start"], _before(draft, cid, ks[0], editor.HEAD_PAD)) if ks else min(target, p["end"] - editor.MIN_CUT)
        if new <= p["start"] + 0.02 or p["end"] - new < editor.MIN_PIECE:
            raise NotApplied("that would leave nothing of the shot after the cut")
        _whole(words, [i for i, w in enumerate(words) if w["start"] >= p["start"] - 0.01 and w["end"] <= new + 0.01],
               ks[0] if ks else None, "start")
    what = "started earlier at" if new < p["start"] else "started later at"
    return [("start", cid, p["start"], new)], f"{what} the cut at {_mmss(draft.offsets[b])} ({p['start'] - new:+.1f}s)"


def _whole(words: list[dict], dropped: list[int], kept: int | None, side: str) -> None:
    """A moved cut never stops (or starts) partway through a sentence by dropping real words: the words
    a move cuts must be ones Studio may cut by itself (hesitations, stutters, a false start, whole
    sentences), unless what's kept still ends (starts) a sentence there."""
    if not dropped or kept is None:
        return
    if side == "end":
        whole = editor.ends_clause(words, kept)
    else:
        whole = kept == 0 or bool(editor.SENTENCE_END.search(words[kept - 1]["word"].strip()))
    if not whole and not _droppable(words, dropped[0], dropped[-1]):
        raise NotApplied(f"that would {'stop' if side == 'end' else 'start'} partway through a sentence")


def _plan_restore(draft: Draft, a: dict) -> tuple[list[tuple], str]:
    t = _num(a.get("start"))
    if t is None:
        raise NotApplied("the read gave no time for the words to bring back")
    toks = _tokens(a.get("from") or "")
    n = len(draft.cuts)
    order = sorted(range(n + 1), key=lambda k: abs(draft.offsets[k] - t))
    for b in [k for k in order if abs(draft.offsets[k] - t) <= NEAR][:3]:
        left = draft.cuts[b - 1] if b > 0 else None
        right = draft.cuts[b] if b < n else None
        sides = []
        if left:
            words = draft.clip_words(left["clip_id"])
            stop = right["start"] if right and right["clip_id"] == left["clip_id"] and right["start"] > left["end"] else 1e9
            idx = [i for i, w in enumerate(words) if w["start"] >= left["end"] - 0.01 and w["end"] <= stop + 0.01]
            sides.append(("after", left, idx[:MAX_RESTORE_WORDS]))
        if right:
            words = draft.clip_words(right["clip_id"])
            stop = left["end"] if left and left["clip_id"] == right["clip_id"] and left["end"] < right["start"] else -1e9
            idx = [i for i, w in enumerate(words) if w["end"] <= right["start"] + 0.01 and w["start"] >= stop - 0.01]
            sides.append(("before", right, idx[-MAX_RESTORE_WORDS:]))
        for side, piece, idx in sides:
            if not idx:
                continue
            words = draft.clip_words(piece["clip_id"])
            norms = [_norm(words[i]["word"]) for i in idx]
            if toks:
                hit = next((s for s in range(len(idx) - len(toks) + 1) if norms[s:s + len(toks)] == toks), None)
                if hit is None:
                    continue
                run = idx[hit:hit + len(toks)]
            else:
                if side == "before" and left and left["clip_id"] == piece["clip_id"]:
                    continue  # the same words as "after": once
                run = idx
            if len(run) > MAX_RESTORE_WORDS:
                continue
            cid = piece["clip_id"]
            said = " ".join(words[i]["word"].strip() for i in run)
            start, end = _before(draft, cid, run[0], editor.HEAD_PAD), _after(draft, cid, run[-1], editor.TAIL_PAD)
            if side == "after":
                end = min(end, right["start"]) if right and right["clip_id"] == cid and right["start"] > piece["end"] else end
                if start - piece["end"] <= 0.3:  # carries straight on: the piece before just runs longer
                    return [("end", cid, piece["end"], end)], f"brought back \"{said}\" at {_mmss(draft.offsets[b])}"
                return ([("insert_after", cid, piece["end"], {**piece, "start": round(max(start, piece["end"]), 3),
                                                             "end": round(end, 3), "why": "brought back"})],
                        f"brought back \"{said}\" at {_mmss(draft.offsets[b])}")
            start = max(start, left["end"]) if left and left["clip_id"] == cid and left["end"] < piece["start"] else start
            if piece["start"] - end <= 0.3:
                return [("start", cid, piece["start"], start)], f"brought back \"{said}\" at {_mmss(draft.offsets[b])}"
            return ([("insert_before", cid, piece["start"], {**piece, "start": round(start, 3),
                                                             "end": round(min(end, piece["start"]), 3), "why": "brought back"})],
                    f"brought back \"{said}\" at {_mmss(draft.offsets[b])}")
    raise NotApplied(f"couldn't find \"{a.get('from') or ''}\" cut near {_mmss(t)}")


def _run(pieces: list[dict], ops: list[tuple]) -> list[dict]:
    out = [dict(p) for p in pieces]
    for op in ops:
        kind, cid, x, y = op[:4]
        if kind == "remove":
            new = []
            for p in out:
                if p["clip_id"] != cid or p["end"] <= x or p["start"] >= y:
                    new.append(p)
                    continue
                if x - p["start"] >= 0.1:
                    new.append({**p, "end": round(x, 3)})
                if p["end"] - y >= 0.1:
                    new.append({**p, "start": round(y, 3)})
            out = new
            continue
        k = next((j for j, p in enumerate(out) if p["clip_id"] == cid
                  and abs(p["end" if kind in ("end", "insert_after") else "start"] - x) < 0.003), None)
        if k is None:
            raise NotApplied("that cut moved with another fix in this round")
        if kind == "end":
            out[k]["end"] = round(y, 3)
        elif kind == "start":
            out[k]["start"] = round(y, 3)
        elif kind == "insert_after":  # (kind, clip_id, a, the new piece): _plan_restore's
            out.insert(k + 1, dict(op[-1]))
        else:
            out.insert(k, dict(op[-1]))
    return [p for p in out if p["end"] - p["start"] >= 0.1]


# ── Everything else ───────────────────────────────────────────────────────────

def _caption(draft: Draft, edl: dict, a: dict) -> str:
    found = draft.find(a.get("from") or "", _num(a.get("start")))
    if found is None:
        raise NotApplied(f"couldn't find \"{a.get('from') or ''}\" in the captions near {_mmss(a.get('start'))}")
    to = re.sub(r"\s+", " ", editor.unmark(a.get("to") or "")).strip()
    i, j = found
    said = " ".join(w["word"] for w in draft.words[i:j + 1])
    if not to:
        raise NotApplied("the read didn't say what the caption should say")
    if to.strip(".,!?;: ") == said.strip(".,!?;: "):
        raise NotApplied("the caption already says that")
    if re.findall(r"\d+", to) != re.findall(r"\d+", said):
        raise NotApplied("Studio never changes a number by itself")
    if len(to.split()) > 6:
        raise NotApplied("that's a rewrite, not a word")
    import spelling
    found_c = spelling.corrections_for(draft.words, i, j - i + 1, to, "read")
    if not found_c:
        raise NotApplied("those words run across two shots")
    spelling.merge(edl, found_c)
    # A caption key word (Site Notes' brass, the gallery look's serif italic) follows its word.
    old = {editor._core(w["word"]).lower() for w in draft.words[i:j + 1]}
    if edl.get("key_words") and old & {k.lower() for k in edl["key_words"]}:
        new_core = editor._core(to.split()[-1])
        edl["key_words"] = [new_core if k.lower() in old else k for k in edl["key_words"]]
    return f"captions say \"{to}\" at {_mmss(draft.words[i]['start'])} (was \"{said}\")"


def _graphic(draft: Draft, edl: dict, a: dict) -> str:
    graphics = edl.get("graphics") or []
    n = a.get("item")
    if not isinstance(n, int) or not 1 <= n <= len(graphics):
        raise NotApplied("the read named a graphic the draft doesn't have")
    g = graphics[n - 1]
    scene = g.get("style") in ("scene", "picture")
    field = a.get("field") or ""
    kind = {"scene": "drawn animation", "picture": "picture"}.get(g.get("style"), g.get("style", "card"))
    name = f"the {kind} \"{g.get('title', '')}\""
    if field == "picture":
        return _to_picture(draft, g, a, name)
    if field == "remove":
        graphics.pop(n - 1)
        return f"removed {name}"
    if field == "time":
        start = _num(a.get("start"))
        start = g["start"] if start is None else start
        length = _num(a.get("amount"))
        length = g["duration"] if length is None else length
        lo, hi = (editor.SCENE_MIN, editor.SCENE_LONGEST) if scene else (1.2, 4.0)  # an insert, a card
        start = max(start, editor.SCENE_CLEAR if scene else CARD_FIRST)
        length = min(max(length, lo), hi, draft.total - start)
        if length < 1.0:
            raise NotApplied(f"{name} wouldn't fit before the end")
        others = [o for o in graphics if o is not g and "start" in o]
        if any(start < o["start"] + o["duration"] + 0.2 and start + length > o["start"] - 0.2 for o in others):
            raise NotApplied(f"{name} would overlap another graphic there")
        if abs(start - g["start"]) < 0.05 and abs(length - g["duration"]) < 0.05:
            raise NotApplied(f"{name} is already there")
        g["start"], g["duration"] = round(start, 3), round(length, 3)
        src = draft.to_source(start)
        if src:
            g["clip_id"], g["at"] = src[0], round(src[1], 3)
        return f"moved {name} to {_mmss(start)} for {length:.1f}s"
    if scene:
        raise NotApplied("a drawn animation's words need it drawn again" if g.get("style") == "scene"
                         else "a picture shows no words: what it shows needs it drawn again (field picture)")
    if field == "title":
        title, italic = editor.title_parts(a.get("to") or "")
        if not title or len(title.split()) > 7:
            raise NotApplied("the new title is empty or too long")
        g["title"], g["italic"] = title, italic
        return f"reworded the {g.get('style', 'card')} to \"{title}\""
    if field == "lines":
        lines = [editor.unmark(x) for x in str(a.get("to") or "").split("/") if editor.unmark(x)]
        if not lines:
            raise NotApplied("the read gave no lines")
        g["lines"] = lines[:3]
        return f"reworded the lines of {name}"
    raise NotApplied("the read didn't say what to change on the graphic")


def _to_picture(draft: Draft, g: dict, a: dict, name: str) -> str:
    """A card, callout or drawn animation becomes an AI picture of what it shows (a, "to": what the picture
    shows, else its own title), at the same moment, at least SCENE_MIN long; its drawing stays as the one shown
    if the picture can't be made. A picture with a new "to" shows that instead."""
    import pictures
    subject = editor.unmark(a.get("to") or "")[:80] or editor.unmark(g.get("title") or "")
    if not subject:
        raise NotApplied("the read didn't say what the picture should show")
    if g.get("style") == "picture" and subject.lower() == (g.get("title") or "").lower():
        raise NotApplied(f"{name} is a picture already")
    why = pictures.available()
    if why:
        raise NotApplied(why)
    length = min(max(g["duration"], editor.SCENE_MIN), editor.SCENE_LONGEST, draft.total - g["start"])
    if length < editor.SCENE_MIN - 0.01:
        raise NotApplied(f"{name} is too close to the end for a picture")
    was = g.get("style")
    g.update(style="picture", title=subject, image=pictures.brief_for(subject), lines=[], duration=round(length, 3),
             look=g.get("look") if g.get("look") in ("cutaway", "panel") else "cutaway",
             scene=g.get("scene") if was == "scene" else g.get("scene") or "", why="a picture shows it better")
    g.pop("italic", None)
    g.pop("file", None)
    return f"showed a picture of {subject} instead of {name}" if was != "picture" else f"the picture now shows {subject}"


def _hook(edl: dict, a: dict) -> str:
    text, italic = editor.title_parts(a.get("to") or "")
    if len(text.split()) > 8:
        raise NotApplied("the new hook is longer than 8 words")
    if text == (edl.get("hook_text") or "") and italic == (edl.get("hook_italic") or ""):
        raise NotApplied("the hook already says that")
    import behind
    behind_did = behind.reword_hook(edl, text, italic)  # the hook shown behind him: reworded there, not twice
    if behind_did:
        return behind_did
    edl["hook_text"], edl["hook_italic"] = text, italic
    return f"the hook now reads \"{text}\"" if text else "took the hook text off"


def _music(edl: dict, a: dict) -> str:
    amount = _num(a.get("amount"))
    if amount is None or abs(amount) < 0.5:
        raise NotApplied("the read didn't say how much to change the music")
    music = edl.setdefault("music", {})
    old = float(music.get("volume_db", -16))
    new = round(min(max(old + amount, MUSIC_RANGE[0]), MUSIC_RANGE[1]), 1)
    if abs(new - old) < 0.5:
        raise NotApplied("the music is already as far as it goes")
    music["volume_db"] = new
    return f"turned the music {'down' if new < old else 'up'} {abs(new - old):.0f} dB"


def _effects(edl: dict, a: dict) -> str:
    import sfx
    if not (edl.get("sfx") or {}).get("placed") and a.get("field") == "remove" and a.get("item") is not None:
        raise NotApplied("this draft has no sound effects")
    try:
        return sfx.patch(edl, a)
    except ValueError as e:
        raise NotApplied(str(e))


def _layout(draft: Draft, edl: dict, a: dict) -> str:
    """A voice-over's picture: him full screen from start to end ("full"; a start alone: the sentence said there;
    no time: all through), the site footage and his window back there ("window"), or his window moved to the
    "left" or "right" corner. Kept as asks tied to his words (voiceover.ask_for), so they hold through re-cuts."""
    import voiceover
    if not (edl.get("voiceover") or {}).get("on"):
        raise NotApplied("this draft isn't a voice-over: there's no window to change")
    field = a.get("field") or ""
    if field in ("left", "right"):
        if (edl.get("pip_side") or (edl.get("pip") or {}).get("side")) == field:
            raise NotApplied(f"the window is already on the {field}")
        edl["pip_side"] = field
        return f"moved your window to the {field}"
    if field not in ("full", "window"):
        raise NotApplied("the read didn't say what to change in the picture")
    start, end = _num(a.get("start")), _num(a.get("end"))
    asks = voiceover.ask_for(edl, draft.clips, field, start, end, "fix")
    if not asks:
        raise NotApplied(f"there's no voice at {_mmss(start)}" if start is not None else "there's no voice to change")
    edl["pip_asks"] = (edl.get("pip_asks") or []) + asks
    return (("showed you full screen" if field == "full" else "put the site footage back with you in the window")
            + (f" at {_mmss(start)}" if start is not None else " all through"))


def _behind(draft: Draft, edl: dict, a: dict) -> str:
    import behind
    try:
        return behind.patch(edl, draft, a)
    except ValueError as e:
        raise NotApplied(str(e))


def _remap(old: Draft, new: Draft, t: float) -> float:
    """A moment on the old draft's clock on the new one (the same footage)."""
    src = old.to_source(t)
    at = new.to_draft(*src) if src else None
    return min(t if at is None else at, max(new.total - 0.5, 0.0))


def apply(edl: dict, clips: dict[int, dict], actions: list[dict]) -> tuple[dict, list[dict]]:
    """Make the actions on a copy of the draft's edit plan. actions: [{n (the fix's number),
    action}]. Returns the new plan and, per action, {n, type, ok, did | why}. Cut changes are
    worked out against the draft the read saw, then made together; the graphics move with the
    footage they sat on. The caller renders the plan (editor.self_patch)."""
    new = copy.deepcopy(edl)
    cuts = editor.attach_corrections(new["cuts"], new.get("corrections"))
    draft = Draft(cuts, clips)
    results, ops = [], []
    order = sorted(actions, key=lambda x: (x["action"].get("type") in CUTS, x["n"]))  # words first, cuts last
    for item in order:
        a, kind = item["action"], item["action"].get("type")
        res = {"n": item["n"], "type": kind}
        try:
            if kind == "caption_word":
                res["did"] = _caption(draft, new, a)
            elif kind == "graphic":
                res["did"] = _graphic(draft, new, a)
            elif kind == "hook_text":
                res["did"] = _hook(new, a)
            elif kind == "music_level":
                res["did"] = _music(new, a)
            elif kind == "sound_effects":
                res["did"] = _effects(new, a)
            elif kind == "layout":
                res["did"] = _layout(draft, new, a)
            elif kind == "behind":
                res["did"] = _behind(draft, new, a)
            elif kind in CUTS:
                planned, did = {"drop_words": _plan_drop, "restore_words": _plan_restore,
                                "trim_pause": _plan_pause, "move_cut": _plan_move}[kind](draft, a)
                _run(cuts, ops + planned)  # it still fits with the ones before it
                ops += planned
                res["did"] = did
            else:
                raise NotApplied("that needs a new edit")
            res["ok"] = True
        except NotApplied as e:
            res.update(ok=False, why=str(e))
        except Exception as e:  # a bug in one patch never stops the others
            log.warning("Patch %s failed: %s: %s", kind, type(e).__name__, e)
            res.update(ok=False, why=f"it didn't work ({type(e).__name__})")
        results.append(res)
    if ops:
        pieces = _run(cuts, ops)
        pieces, notes = editor.sentence_guard(pieces, clips)
        pieces = editor.on_frames(pieces, clips)
        if not any(Draft(pieces, clips).words):
            for r in results:
                if r["type"] in CUTS and r.get("ok"):
                    r.update(ok=False, why="that would cut every word")
            pieces = cuts
        else:
            after = Draft(pieces, clips)
            for g in (new.get("graphics") or []) + (new.get("behind") or []):
                if "start" in g:
                    g["start"] = round(_remap(draft, after, g["start"]), 3)
                    g["duration"] = round(min(g["duration"], max(after.total - g["start"], 1.0)), 3)
            new["checks"] = list(new.get("checks") or []) + notes
        cuts = pieces
    new["cuts"] = editor.attach_corrections(cuts, new.get("corrections"))
    new["patched"] = [r for r in results if r.get("ok")]
    return new, results


def describe(action: dict | None) -> str:
    """The action in a few words, for logs and the fix history."""
    a = action or {}
    kind = a.get("type") or "none"
    if kind == "replan":
        return f"re-edit ({a.get('reason') or 'no smaller fix'})"
    bits = [kind] + [f"{k}={a[k]}" for k in ("start", "end", "from", "to", "item", "field", "amount")
                     if a.get(k) not in (None, "")]
    return " ".join(str(b) for b in bits)
