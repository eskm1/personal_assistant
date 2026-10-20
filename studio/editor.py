"""
The editor (phase 5): turns a planned production into a draft Reel.

  1. Claude plans the cut from the approved idea: which stretches of which clips to
     keep, at phrase boundaries (it sees every word with its timestamp), the hook
     text, and the music brief. On a revision it also gets Bryan's notes (and sorts
     each into an editing problem, which Studio learns as a rule, or taste).
  2. Cut points are snapped to word edges with a little air, so no word is clipped.
     Studio's checks on the ends (sentence_guard): never end on a stray word that
     starts a new sentence, never open on the tail of one, no dead air at either end.
     Then pauses inside each kept stretch are cut down to a short breath (jump cuts),
     and lone "um"s go.
  3. Framing (framing.py): horizontal footage is cropped around the person's face,
     and talking shots punch in and out every few seconds.
  4. ffmpeg cuts every piece from the ORIGINAL files in one pass (one encode, so
     lips stay in sync) and frames it 1080x1920.
  5. HyperFrames renders the captions (1-3 words in sentence case, timed to each word,
     in a bundled font: STUDIO_CAPTION_FONT) and the hook as a transparent layer; cards
     stay above the caption line and captions repeating a card's words are hidden.
  6. Sound (mix_final): the voice is cleaned and levelled first, the music set well under
     it (dipped while Bryan talks), the gap measured and guarded, then -14 LUFS. Urban
     Makers posts get an Epidemic Sound track; other accounts get a note on what to add.
  7. Checks: the draft is re-transcribed to confirm every word survived the cuts,
     and its loudness is measured (and corrected if it's off).
  8. Studio's read (draft_review.py), then Studio's own fix rounds (self_fix.py) on the
     fixes the editor can make, and only then To review.
"""
import difflib
import html
import itertools
import json
import logging
import re
import shutil
import tempfile
from pathlib import Path

import anthropic

import catalog
import extract
import framing
import music
import playbook
import render
import settings
from settings import ANTHROPIC_API_KEY, CLAUDE_MODEL, ROOT

log = logging.getLogger("studio.editor")

RENDERS = ROOT / "Renders"
W, H, FPS = 1080, 1920, 30
# Whisper ends a word slightly before the sound dies; keep a little air either side.
HEAD_PAD, TAIL_PAD = 0.08, 0.15
MIN_CUT = 0.4
# Jump cuts: a pause longer than settings.EDIT_MAX_PAUSE is cut down to this much air
# after the last word and before the next (about a breath), and no piece is left
# shorter than MIN_PIECE (it stays joined to its neighbour instead).
BREATH_AFTER, BREATH_BEFORE = 0.07, 0.05
MIN_PIECE = 0.25
# Hesitation sounds dropped when Whisper wrote them as a word of their own, with a gap
# either side (FILLER_GAP seconds at least).
FILLERS = {"um", "umm", "uh", "uhh", "erm", "uhm", "ah", "嗯", "呃", "啊"}
FILLER_GAP = 0.06

EDL_SCHEMA = {
    "type": "object",
    "properties": {
        "cuts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "clip_id": {"type": "integer"},
                    "start": {"type": "number"},
                    "end": {"type": "number"},
                    "why": {"type": "string"},
                },
                "required": ["clip_id", "start", "end", "why"],
                "additionalProperties": False,
            },
        },
        "hook_text": {"type": "string"},
        "music": {
            "type": "object",
            "properties": {
                "mood": {"type": "string"},
                "search": {"type": "string"},
                "tempo_bpm": {"type": "number"},
                "volume_db": {"type": "number"},
            },
            "required": ["mood", "search", "tempo_bpm", "volume_db"],
            "additionalProperties": False,
        },
        "summary": {"type": "string"},
        "graphics": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "clip_id": {"type": "integer"},
                    "at": {"type": "number"},
                    "duration": {"type": "number"},
                    "style": {"type": "string", "enum": ["card", "callout"]},
                    "title": {"type": "string"},
                    "lines": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["clip_id", "at", "duration", "style", "title", "lines"],
                "additionalProperties": False,
            },
        },
        "missing_shots": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"shot": {"type": "string"}, "why": {"type": "string"}},
                "required": ["shot", "why"],
                "additionalProperties": False,
            },
        },
        "notes_addressed": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"note_id": {"type": "integer"}, "how": {"type": "string"},
                               "kind": {"type": "string", "enum": ["editing", "taste"]},
                               "rule": {"type": "string"}, "same_rule": {"type": "integer"}},
                "required": ["note_id", "how", "kind", "rule", "same_rule"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["cuts", "hook_text", "music", "graphics", "missing_shots", "summary", "notes_addressed"],
    "additionalProperties": False,
}

_SYSTEM = """You are the editor for Bryan's Instagram Reels. You decide exactly which stretches \
of footage make the final 9:16 video. Follow Bryan's playbook:

<playbook>
{playbook}
</playbook>

Rules for the cut list:
- Work from the approved plan's segments, but you choose the exact in and out points, and you may \
tighten, split or drop parts of a segment (dead air, false starts, repeated takes) and reorder \
when the story needs it. Use only the clips listed.
- Cut between phrases, never inside a word: put cut points in the gaps between the timestamped \
words, never in the middle of one. Keep the payoff and anything that qualifies a claim.
{pause_rule}- Cuts play in the order you list them. Each is clip_id plus start and end seconds within that clip.
- hook_text: at most 6 words shown on screen for the first ~2.5 seconds, or "" for none. It \
should add to the spoken hook, not repeat it word for word.
- music: describe the bed for this post (mood, a short catalogue search phrase, tempo). \
volume_db is the music's level: -16 is the usual bed (Studio levels Bryan's voice and keeps the \
music well under it while he talks, a little higher between phrases); go lower (-18 to -22) for a \
quieter bed, higher (-12) only for music-led posts.
- {music_rule}
- graphics: 1 to 3 animated B-roll inserts (rendered with HyperFrames) that show what Bryan is \
saying while he says it. Anchor each to the moment the words it illustrates are spoken: clip_id \
and "at" in that clip's own seconds (it must fall inside one of your cuts), plus duration (1.5 to \
3.5 s). style "card" fills the screen with a headline and up to 3 short points (good for steps, \
lists or the key takeaway); style "callout" is a panel over the top third of the footage for one \
number, price, duration or key term (title is the big text, lines one short label). Use Bryan's \
own words and facts; never invent numbers. Title at most 5 words, each line at most 4 words. \
Leave the first 2.5 seconds clear for the hook. Use none if nothing in the speech suits a graphic.
- missing_shots: footage you wished you had while cutting this post, specific enough to film \
(what, framing, roughly how long), each with why it would make the post better. Empty if none.
- summary: two sentences on what you did, for Bryan.
- On a revision, address every note and say how in notes_addressed. Notes are pinned to times \
in the PREVIOUS draft; the previous cut list shows where each piece sat in that draft.
- For each note also give its kind: "editing" when it is an editing craft problem a good editor \
should never repeat on any post (a clipped word, ending on half a sentence, a slow opening, music \
over the voice, a card or caption covering what matters, a dead pause), "taste" when it is Bryan's \
judgement or preference about this post (which take, the message, a look he likes). For an \
editing note from Bryan, rule is the general rule it teaches, as an instruction in at most 14 \
words ("End on the last full sentence, never on a word that starts the next"), and same_rule is \
the number of the learned rule it repeats (R3 = 3) or 0. Otherwise rule is "" and same_rule 0. \
Studio's own fixes (a round of Studio's fixes, not Bryan's notes) are kind "editing", rule "" and \
same_rule 0.
- The playbook's "Editing rules Studio learned" are problems caught in earlier drafts: never \
repeat them."""


# ── Context for Claude ────────────────────────────────────────────────────────

def _words(clip: dict) -> list[dict]:
    path = Path(clip["clip_dir"]) / "transcript.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8")).get("words", [])


def _clip_block(clip: dict, plan_segments: list[dict]) -> str:
    words = _words(clip)
    lines = [f"Clip {clip['id']}: {clip['title']} ({clip['duration']:.1f}s, {clip['orientation']}, "
             f"{'has audio' if clip['has_audio'] else 'no audio'})",
             f"  summary: {clip['summary']}"]
    wanted = [s for s in plan_segments if s["clip_id"] == clip["id"]]
    lines.append("  planned segments: " + "; ".join(f"{s['start']:.1f}-{s['end']:.1f}s ({s['purpose']})"
                                                  for s in wanted))
    if words:
        lines.append("  words (start-end seconds):")
        lines += [f"    {w['start']:.2f}-{w['end']:.2f} {w['word']}" for w in words]
    else:
        lines.append("  no speech")
    return "\n".join(lines)


def _revision_block(conn, production: dict, fixes: list[dict] | None = None) -> str:
    """The previous draft and what to change: Bryan's open notes, or (a round of Studio's own
    fixes, before Bryan has seen the draft) the fixes from Studio's read."""
    if not production["edl"]:
        return ""
    previous = json.loads(production["edl"])
    offset, placed = 0.0, []
    for group in _parts(previous["cuts"]):
        length = sum(c["end"] - c["start"] for c in group)
        placed.append(f"  {offset:.1f}-{offset + length:.1f}s in the draft = clip {group[0]['clip_id']} "
                      f"{group[0]['start']:.2f}-{group[-1]['end']:.2f}s"
                      + (" (pauses tightened)" if len(group) > 1 and any(
                          a["end"] < b["start"] - 0.001 for a, b in zip(group, group[1:])) else ""))
        offset += length
    graphics = previous.get("graphics") or []
    shown = ("\nPrevious graphics: " + "; ".join(
        f"{g['start']:.1f}-{g['start'] + g['duration']:.1f}s {g['style']} \"{g['title']}\""
        + (f" ({' / '.join(g['lines'])})" if g.get("lines") else "") for g in graphics if "start" in g)
        if graphics else "")
    head = ("\n\nThis is a revision of draft v{v}. Previous cut list:\n".format(v=production["version"])
            + "\n".join(placed)
            + f"\nPrevious hook text: {previous.get('hook_text') or '(none)'}" + shown
            + f"\nPrevious music volume: {previous.get('music', {}).get('volume_db', 'n/a')} dB")
    if fixes is not None:
        # Studio's own round: Bryan hasn't seen this draft. Keep what his notes on the last
        # version asked for (already done in it).
        done = conn.execute(
            """SELECT body FROM feedback WHERE target = 'production' AND target_id = ? AND status = 'addressed'
               AND version = ? ORDER BY id""", (production["id"], production["version"] - 1)).fetchall()
        lines = [f"  fix {f['id']}" + (f" at {f['at_seconds']:.1f}s" if f.get("at_seconds") is not None else "")
                 + f": {f['body']}" for f in fixes]
        return (head.replace(f"a revision of draft v{production['version']}",
                             "a round of Studio's own fixes on the draft below, before Bryan sees it")
                + "\n\nStudio's read of that draft found these problems you can fix from the footage. "
                  "Fix every one, and keep everything else as it is:\n" + "\n".join(lines)
                + ("\n\nBryan's notes on the version before, already done in this draft (keep them done):\n"
                   + "\n".join(f"  - {r['body']}" for r in done) if done else ""))
    notes = conn.execute(
        "SELECT * FROM feedback WHERE target = 'production' AND target_id = ? AND status = 'open' ORDER BY id",
        (production["id"],)).fetchall()
    note_lines = [f"  note {n['id']}" + (f" at {n['at_seconds']:.1f}s" if n["at_seconds"] is not None else "")
                  + f": {n['body']}" for n in notes]
    return head + "\n\nBryan's notes on that draft:\n" + ("\n".join(note_lines) or "  (none)")


def _parts(cuts: list[dict]) -> list[list[dict]]:
    """Cuts grouped back into the stretches Claude planned (jump cuts and framing
    split one planned cut into several pieces, which share its "part")."""
    groups = []
    for c in cuts:
        if groups and c.get("part") is not None and groups[-1][-1].get("part") == c.get("part") \
                and groups[-1][-1]["clip_id"] == c["clip_id"]:
            groups[-1].append(c)
        else:
            groups.append([c])
    return groups


def plan_edit(conn, production: dict, clips: dict[int, dict], client=None, fixes: list[dict] | None = None,
              ref: str | None = None) -> dict:
    """Claude's edit plan (EDL). fixes: a round of Studio's own fixes instead of Bryan's notes;
    ref: where its cost is recorded (Studio's own rounds are counted apart)."""
    plan = json.loads(production["plan"] or "{}")
    segments = plan.get("segments", [])
    music_rule = ("This account uses an Epidemic Sound track; your music brief is used to search for it."
                  if music.allowed_for(production["brand"]) else
                  "This account adds music in the Instagram app instead; your music brief becomes a note "
                  "telling Bryan what to pick, so keep the edit working without music.")
    user = (f"Post: {plan.get('title')} ({production['brand']}, {plan.get('format')}, about "
            f"{plan.get('est_seconds') or 30:.0f}s)\nHook: {plan.get('hook')}\nWhy it works: {plan.get('angle')}\n"
            "Structure: " + " → ".join(plan.get("beats", []))
            + (f"\nCall to action: {plan['cta']}" if plan.get("cta") else "")
            + ("\nStoryboard the team planned (follow its order, on-screen text and lines where the "
               "footage allows):\n" + "\n".join(
                   f"  {i}. {s['description']}" + (f" | says: {s['line']}" if s.get("line") else "")
                   + (f" | on screen: {s['on_screen']}" if s.get("on_screen") else "")
                   for i, s in enumerate(plan["storyboard"], 1)) if plan.get("storyboard") else "")
            + "\n\nFootage:\n\n"
            + "\n\n".join(_clip_block(c, segments) for c in clips.values())
            + _revision_block(conn, production, fixes))
    pause_rule = ("- Pauses are tightened automatically after you plan: silences, breaths and lone \"um\"s "
                  "inside a cut are cut down to a short breath (jump cuts), and so is silence at the start and "
                  "end of a cut with speech. So keep whole phrases in one cut rather than splitting a cut just to "
                  "skip a pause, and give a deliberate silent moment (a reveal, B-roll) a cut of its own.\n"
                  if settings.EDIT_JUMP_CUTS else "")
    client = client or anthropic.Anthropic(api_key=ANTHROPIC_API_KEY or None)
    import llm
    response = llm.final_message(
        client, model=CLAUDE_MODEL, max_tokens=32000,
        betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        output_config={"effort": "high", "format": {"type": "json_schema", "schema": EDL_SCHEMA}},
        system=_SYSTEM.replace("{playbook}", playbook.text()).replace("{music_rule}", music_rule)
        .replace("{pause_rule}", pause_rule),
        messages=[{"role": "user", "content": user}],
    )
    import usage
    usage.record("edit", ref or f"production:{production['id']}", response)
    if response.stop_reason == "refusal":
        raise RuntimeError("Claude declined to plan this edit")
    if response.stop_reason == "max_tokens":
        raise RuntimeError("Claude's edit plan was cut off (max_tokens)")
    return json.loads(next(b.text for b in response.content if b.type == "text"))


# ── Cut points ────────────────────────────────────────────────────────────────

def snap(cut: dict, clip: dict) -> dict | None:
    """Move cut points out of words into the gaps around them, with a little air,
    without reaching into the neighbouring word. None if what's left is too short."""
    words, duration = _words(clip), clip["duration"] or 0
    start, end = max(0.0, cut["start"]), min(duration, cut["end"])
    for w in words:
        if w["start"] < start < w["end"]:
            start = w["start"]
        if w["start"] < end < w["end"]:
            end = w["end"]
    before = [w for w in words if w["end"] <= start]
    after = [w for w in words if w["start"] >= end]
    start = max(start - HEAD_PAD, before[-1]["end"] if before else 0.0, 0.0)
    end = min(end + TAIL_PAD, after[0]["start"] if after else duration, duration)
    if end - start < MIN_CUT:
        return None
    return {**cut, "start": round(start, 3), "end": round(end, 3)}


def _is_filler(word: str) -> bool:
    return re.sub(r"[\s.,!?;:…，。！？、~\-]+", "", word.lower()) in FILLERS


def tighten(cuts: list[dict], clips: dict[int, dict], max_pause: float | None = None) -> list[dict]:
    """Jump cuts: split each cut into pieces that skip its long pauses.

    Inside a cut, a gap between words longer than max_pause (and a lone "um" or
    嗯 with gaps around it) is cut down to a breath: a little air after the last
    word and before the next. Silence longer than max_pause at the start or end of
    the cut is trimmed to the same air the cut points get. Cuts are only ever made
    in the gaps between words, a piece is never shorter than MIN_PIECE (it stays
    joined to its neighbour instead), and a cut with no speech (B-roll) is left as
    it is. Each piece keeps its cut's "part" (its place in Claude's plan)."""
    max_pause = settings.EDIT_MAX_PAUSE if max_pause is None else max_pause
    out = []
    for n, c in enumerate(cuts):
        part = c.get("part", n)
        words = sorted((w for w in _words(clips[c["clip_id"]])
                        if w["start"] >= c["start"] - 0.01 and w["end"] <= c["end"] + 0.01),
                       key=lambda w: w["start"])
        keep, dropped = [], []
        for i, w in enumerate(words):
            before = w["start"] - (words[i - 1]["end"] if i else c["start"])
            after = (words[i + 1]["start"] if i + 1 < len(words) else c["end"]) - w["end"]
            if _is_filler(w["word"]) and before >= FILLER_GAP and after >= FILLER_GAP:
                dropped.append(w)
            else:
                keep.append(w)
        if not keep:  # no speech, or nothing but "um": leave it as planned
            out.append({**c, "part": part})
            continue
        start, end = c["start"], c["end"]
        if keep[0]["start"] - start > max_pause or any(d["end"] <= keep[0]["start"] for d in dropped):
            start = max(start, keep[0]["start"] - HEAD_PAD,
                        max((d["end"] for d in dropped if d["end"] <= keep[0]["start"]), default=start))
        if end - keep[-1]["end"] > max_pause or any(d["start"] >= keep[-1]["end"] for d in dropped):
            end = min(end, keep[-1]["end"] + TAIL_PAD,
                      min((d["start"] for d in dropped if d["start"] >= keep[-1]["end"]), default=end))
        spans = [[start, None]]
        for a, b in zip(keep, keep[1:]):
            between = [d for d in dropped if d["start"] >= a["end"] and d["end"] <= b["start"]]
            if b["start"] - a["end"] <= max_pause and not between:
                continue
            cut_from = min(a["end"] + BREATH_AFTER, between[0]["start"] if between else b["start"])
            cut_to = max(b["start"] - BREATH_BEFORE, between[-1]["end"] if between else a["end"])
            if cut_to - cut_from < 0.05:
                continue
            spans[-1][1] = cut_from
            spans.append([cut_to, None])
        spans[-1][1] = end
        # No piece shorter than MIN_PIECE: join it to the neighbour across the smaller gap.
        while len(spans) > 1:
            short = min(range(len(spans)), key=lambda i: spans[i][1] - spans[i][0])
            if spans[short][1] - spans[short][0] >= MIN_PIECE:
                break
            gap_before = spans[short][0] - spans[short - 1][1] if short else float("inf")
            gap_after = spans[short + 1][0] - spans[short][1] if short + 1 < len(spans) else float("inf")
            i = short - 1 if gap_before <= gap_after else short
            spans[i:i + 2] = [[spans[i][0], spans[i + 1][1]]]
        out += [{**c, "start": round(s, 3), "end": round(e, 3), "part": part} for s, e in spans]
    return out


SENTENCE_END = re.compile(r"[.!?。！？…]['\"”’)\]]*$")
# A stray start of the next sentence at the very end (or the tail of the one before at the very
# start) shorter than this is cut; a longer one is a deliberate part of the edit.
MAX_DANGLING = 1.5
# Silence before the first word or after the last one, longer than this, is trimmed.
EDGE_SILENCE = 0.5


def _inside(cut: dict, words: list[dict]) -> list[dict]:
    return [w for w in words if w["start"] >= cut["start"] - 0.01 and w["end"] <= cut["end"] + 0.01]


def sentence_guard(cuts: list[dict], clips: dict[int, dict]) -> tuple[list[dict], list[str]]:
    """Studio's checks on the ends of the draft, before anything is rendered: it never ends on
    a word that starts a new sentence (a stray "and" after the last full stop), never opens on
    the tail of a sentence, and has no dead silence before the first word or after the last.
    Returns the cuts and what it changed, in plain words."""
    cuts = [dict(c) for c in cuts]
    done = []
    spoken = [i for i, c in enumerate(cuts) if _inside(c, _words(clips[c["clip_id"]]))]
    if not spoken:
        return cuts, done
    # The end: the last cut that speaks (only silent pictures may follow it).
    c = cuts[spoken[-1]]
    words = _inside(c, _words(clips[c["clip_id"]]))
    ends = [k for k, w in enumerate(words) if SENTENCE_END.search(w["word"].strip())]
    if ends and ends[-1] < len(words) - 1:
        last, tail = words[ends[-1]], words[ends[-1] + 1:]
        new_end = min(last["end"] + TAIL_PAD, tail[0]["start"])
        if tail[-1]["end"] - tail[0]["start"] <= MAX_DANGLING and new_end - c["start"] >= MIN_CUT:
            c["end"] = round(new_end, 3)
            stray = " ".join(w["word"].strip() for w in tail)
            done.append(f"ended on the last full sentence (cut the stray \"{stray}\" at the end)")
            words = words[:ends[-1] + 1]
    if spoken[-1] == len(cuts) - 1 and c["end"] - words[-1]["end"] > EDGE_SILENCE:
        c["end"] = round(words[-1]["end"] + TAIL_PAD, 3)
        done.append("trimmed the silence after the last word")
    # The start: the first cut that speaks, when it opens partway through a sentence.
    c = cuts[spoken[0]]
    all_words = _words(clips[c["clip_id"]])
    words = _inside(c, all_words)
    before = [w for w in all_words if w["end"] <= words[0]["start"] + 0.001 and w is not words[0]]
    mid = before and words[0]["start"] - before[-1]["end"] < 1.0 and not SENTENCE_END.search(before[-1]["word"].strip())
    if mid:
        k = next((k for k, w in enumerate(words) if SENTENCE_END.search(w["word"].strip())), None)
        if k is not None and k + 1 < len(words) and words[k]["end"] - words[0]["start"] <= MAX_DANGLING:
            new_start = max(words[k]["end"], words[k + 1]["start"] - HEAD_PAD)
            if c["end"] - new_start >= MIN_CUT:
                c["start"] = round(new_start, 3)
                stray = " ".join(w["word"].strip() for w in words[:k + 1])
                done.append(f"opened on a full sentence (cut the end of the one before, \"{stray}\")")
                words = words[k + 1:]
    if spoken[0] == 0 and words[0]["start"] - c["start"] > EDGE_SILENCE:
        c["start"] = round(max(c["start"], words[0]["start"] - HEAD_PAD), 3)
        done.append("trimmed the silence before the first word")
    return cuts, done


def timeline(cuts: list[dict], clips: dict[int, dict]) -> list[dict]:
    """Every kept word on the draft's own clock."""
    out, offset = [], 0.0
    for c in cuts:
        for w in _words(clips[c["clip_id"]]):
            if w["start"] >= c["start"] - 0.01 and w["end"] <= c["end"] + 0.01:
                out.append({"word": w["word"], "start": offset + w["start"] - c["start"],
                            "end": offset + w["end"] - c["start"]})
        offset += c["end"] - c["start"]
    return out


def place_graphics(graphics: list[dict], cuts: list[dict], total: float) -> list[dict]:
    """Move each graphic from its source anchor (clip + seconds) onto the draft's
    clock; drop ones whose moment was cut, that would cover the hook, or overlap."""
    placed, offset_of = [], []
    offset = 0.0
    for c in cuts:
        offset_of.append(offset)
        offset += c["end"] - c["start"]
    for g in graphics:
        start = next((offset_of[i] + g["at"] - c["start"] for i, c in enumerate(cuts)
                      if c["clip_id"] == g["clip_id"] and c["start"] <= g["at"] < c["end"]), None)
        if start is None:
            # Its moment fell in a pause that was cut out: start it as the talking resumes.
            start = next((offset_of[i] for i, c in enumerate(cuts)
                          if c["clip_id"] == g["clip_id"] and g["at"] <= c["start"] <= g["at"] + 1.5), None)
        if start is None:
            continue
        start = max(start, 2.5)
        duration = min(max(g["duration"], 1.2), 4.0, total - start)
        if duration < 1.0:
            continue
        if placed and start < placed[-1]["start"] + placed[-1]["duration"] + 0.3:
            continue
        placed.append({**g, "start": round(start, 3), "duration": round(duration, 3),
                       "lines": [line for line in g["lines"] if line.strip()][:3]})
        placed.sort(key=lambda x: x["start"])
    return placed


# A caption on screen for less than this can't be read: it joins the next one when they fit.
MIN_CAPTION = 0.32
CAPTION_CHARS, JOINED_CHARS = 18, 22


def caption_chunks(words: list[dict], total: float) -> list[dict]:
    """1-3 words per caption, split at punctuation and pauses, shown 2 frames early, in
    sentence case (as Bryan says them, not all capitals). A caption that would flash by too
    fast to read joins the next one."""
    chunks, current = [], []
    for i, w in enumerate(words):
        # Start a new caption rather than let this one grow too wide for the screen.
        if current and len(" ".join(x["word"] for x in current + [w])) > CAPTION_CHARS:
            chunks.append(current)
            current = []
        current.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else None
        pause = nxt is not None and nxt["start"] - w["end"] > 0.3
        if len(current) == 3 or re.search(r"[.,!?;:]$", w["word"]) or pause or nxt is None:
            chunks.append(current)
            current = []
    joined = []
    for i, chunk in enumerate(chunks):
        if joined and chunk:
            prev = joined[-1]
            shown = chunk[0]["start"] - prev[0]["start"]
            text = " ".join(x["word"] for x in prev + chunk)
            if (shown < MIN_CAPTION and len(text) <= JOINED_CHARS and len(prev) + len(chunk) <= 4
                    and not SENTENCE_END.search(prev[-1]["word"].strip())):
                joined[-1] = prev + chunk
                continue
        joined.append(chunk)
    chunks = joined
    out = []
    for i, chunk in enumerate(chunks):
        start = max(0.0, chunk[0]["start"] - 2 / FPS)
        end = chunks[i + 1][0]["start"] - 2 / FPS if i + 1 < len(chunks) else min(total, chunk[-1]["end"] + 0.4)
        end = min(end, chunk[-1]["end"] + 0.8)  # don't hold a caption through a long silence
        text = re.sub(r"[.,;:]+$", "", " ".join(w["word"].strip() for w in chunk)).strip()
        if not out and text:
            text = text[0].upper() + text[1:]
        if end - start > 0.05 and text:
            out.append({"text": text, "start": round(start, 3), "end": round(end, 3)})
    return out


def _plain(text: str) -> set[str]:
    return {w for w in re.findall(r"[\w']+", text.lower()) if len(w) > 1}


def clear_of_cards(chunks: list[dict], graphics: list[dict]) -> tuple[list[dict], int]:
    """While a full-screen card is up, its words are on screen already: a caption saying the
    same words is dropped, so the card is never doubled up by the caption line. Returns the
    captions and how many were dropped."""
    cards = [g for g in graphics if g.get("style") == "card" and "start" in g]
    if not cards:
        return chunks, 0
    out, dropped = [], 0
    for c in chunks:
        card = next((g for g in cards if c["start"] < g["start"] + g["duration"] and c["end"] > g["start"]), None)
        words = _plain(c["text"])
        if card and words and words <= _plain(" ".join([card["title"], *card.get("lines", [])])):
            dropped += 1
            continue
        out.append(c)
    return out, dropped


# ── Rendering ─────────────────────────────────────────────────────────────────

RATE = 48000
JOIN_FADE = 0.008  # seconds of fade either side of a join in the sound, so it can't click
GROUP_GAP = 3.0    # pieces of one clip closer than this are read from the file in one go


def _stepwise(values: list, edges: list[int]) -> str:
    """An ffmpeg expression of the frame number n: values[i] from frame edges[i] on."""
    expr = str(values[-1])
    for value, edge in reversed(list(zip(values[:-1], edges[1:]))):
        expr = f"if(lt(n,{edge}),{value},{expr})"
    return expr


def _group_filter(pieces: list[dict], first: list[int], clip: dict, tag: str) -> str:
    """Frame a run of pieces from one read of a clip at 1080x1920: each piece cropped
    as its cut says (crop_x, crop_y, zoom; the centre when not set). first[i] is
    piece i's first frame in the run. A punched-in piece comes from a second, closer
    crop of the same frames, switched in for just its frames."""
    sw, sh = int(clip.get("width") or 0), int(clip.get("height") or 0)
    if not (sw and sh):  # size unknown: fill the frame from the middle, as before
        return f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}"
    windows = [framing.window(c, sw, sh) for c in pieces]

    def crop(keep):  # the crop for the pieces of one zoom; the others hold its last position
        xs, ys, last = [], [], next(win for win, k in zip(windows, keep) if k)
        for win, k in zip(windows, keep):
            last = win if k else last
            xs.append(last[2])
            ys.append(last[3])
        return f"crop={last[0]}:{last[1]}:x='{_stepwise(xs, first)}':y='{_stepwise(ys, first)}',scale={W}:{H}"
    zooms = [(c.get("zoom") or 1.0) > 1.0 for c in pieces]
    if all(zooms) or not any(zooms):
        return crop([True] * len(pieces))
    ends = first[1:] + [None]
    when = "+".join(f"between(n,{a},{b - 1})" for a, b, z in zip(first, ends, zooms) if z and b is not None)
    when += "".join(f"+gte(n,{a})" for a, b, z in zip(first, ends, zooms) if z and b is None)
    return (f"split=2[{tag}w][{tag}c];[{tag}w]{crop([not z for z in zooms])}[{tag}w2];"
            f"[{tag}c]{crop(zooms)}[{tag}c2];[{tag}w2][{tag}c2]overlay=0:0:enable='{when.lstrip('+')}'")


def render_cut(cuts: list[dict], clips: dict[int, dict], out: Path) -> float:
    """All pieces in one ffmpeg run from the originals: frame-exact, one encode.

    Each piece is a whole number of frames, rounded from the running total so the
    picture never drifts from the captions' clock. Pieces of one clip that follow
    each other closely (a stretch with its pauses cut, punch-ins, reframes) are read
    from the file once, dropping the frames in between, which keeps the memory a
    4K clip needs to one read per stretch. Pieces that carry straight on from the
    one before take their sound in one unbroken piece; elsewhere the sound fades
    for a few milliseconds either side of the join, so jump cuts don't click."""
    # Frames per piece, and runs of pieces read together: [first piece, last piece].
    frames, edge, running = [], 0, 0.0
    for c in cuts:
        running += c["end"] - c["start"]
        frames.append(max(1, round(running * FPS) - edge))
        edge += frames[-1]
    groups = []
    for k, c in enumerate(cuts):
        prev = cuts[k - 1] if k else None
        if prev and prev["clip_id"] == c["clip_id"] and prev["end"] - 0.002 <= c["start"] <= prev["end"] + GROUP_GAP:
            groups[-1][1] = k
        else:
            groups.append([k, k])
    # Where each piece starts in its run (frames on the run's own 30 fps clock), and in the clip.
    at, source = [0] * len(cuts), [0.0] * len(cuts)
    for g0, g1 in groups:
        for k in range(g0, g1 + 1):
            carries_on = k > g0 and abs(cuts[k - 1]["end"] - cuts[k]["start"]) < 0.002
            at[k] = (at[k - 1] + frames[k - 1] if carries_on else
                     max(round((cuts[k]["start"] - cuts[g0]["start"]) * FPS), at[k - 1] + frames[k - 1] if k > g0 else 0))
            source[k] = cuts[g0]["start"] + at[k] / FPS

    args, graph, video, n = [settings.FFMPEG, "-y", "-v", "error"], [], [], -1
    for g, (g0, g1) in enumerate(groups):
        clip, total = clips[cuts[g0]["clip_id"]], sum(frames[g0:g1 + 1])
        span = at[g1] + frames[g1]
        n += 1
        args += ["-an", "-ss", f"{source[g0]:.4f}", "-t", f"{span / FPS + 0.3:.3f}", "-i", clip["video_path"]]
        keep = "+".join(f"between(n,{at[k]},{at[k] + frames[k] - 1})" for k in range(g0, g1 + 1))
        first = list(itertools.accumulate([0] + frames[g0:g1]))
        graph.append(f"[{n}:v]setpts=PTS-STARTPTS,fps={FPS},select='{keep}',setpts=N/({FPS}*TB),"
                     f"tpad=stop_mode=clone:stop={total},trim=end_frame={total},setpts=PTS-STARTPTS,"
                     f"{_group_filter(cuts[g0:g1 + 1], first, clip, f'g{g}')},setsar=1,format=yuv420p[v{g}]")
        video.append(f"[v{g}]")

    runs = []  # unbroken sound: [clip, start in the clip, frames]
    for k, c in enumerate(cuts):
        if k and cuts[k - 1]["clip_id"] == c["clip_id"] and abs(source[k - 1] + frames[k - 1] / FPS - source[k]) < 1e-6:
            runs[-1][2] += frames[k]
        else:
            runs.append([clips[c["clip_id"]], source[k], frames[k]])
    sound = []
    for j, (clip, start, length) in enumerate(runs):
        samples = length * RATE // FPS
        seconds = samples / RATE
        if clip["has_audio"]:
            n += 1
            args += ["-vn", "-ss", f"{start:.4f}", "-t", f"{seconds + 0.3:.3f}", "-i", clip["video_path"]]
            fades = (f",afade=t=in:d={JOIN_FADE},afade=t=out:st={seconds - JOIN_FADE:.4f}:d={JOIN_FADE}"
                     if len(runs) > 1 and seconds > 4 * JOIN_FADE else "")
            graph.append(f"[{n}:a]asetpts=PTS-STARTPTS,aresample={RATE},aformat=channel_layouts=stereo,"
                         f"apad=whole_len={samples},atrim=end_sample={samples}{fades}[a{j}]")
        else:
            graph.append(f"anullsrc=r={RATE}:cl=stereo,atrim=end_sample={samples}[a{j}]")
        sound.append(f"[a{j}]")
    graph.append(f"{''.join(video)}concat=n={len(video)}:v=1:a=0[v]")
    graph.append(f"{''.join(sound)}concat=n={len(sound)}:v=0:a=1[a]")
    extract._run(args + ["-filter_complex", ";".join(graph), "-map", "[v]", "-map", "[a]",
                         "-c:v", "libx264", "-preset", "fast", "-crf", "16", "-c:a", "aac", "-b:a", "256k", str(out)])
    return sum(c["end"] - c["start"] for c in cuts)


# The caption fonts are bundled (OFL, latin, variable weight) and loaded from local files, so a
# render never depends on the machine's fonts or the network. STUDIO_CAPTION_FONT picks one.
FONTS = render.RENDER_DIR / "captions" / "fonts"
CAPTION_FONTS = {
    "figtree": ("Figtree", render.RENDER_DIR / "carousel" / "fonts" / "Figtree-latin.woff2"),
    "nunito": ("Nunito", FONTS / "Nunito-latin.woff2"),
    "dm_sans": ("DM Sans", FONTS / "DMSans-latin.woff2"),
    "outfit": ("Outfit", FONTS / "Outfit-latin.woff2"),
}


def caption_font(name: str | None = None) -> tuple[str, Path]:
    """(family, woff2 file) of the caption font: the setting's, or Figtree."""
    key = (name or settings.CAPTION_FONT or "figtree").lower().replace("-", "_").replace(" ", "_")
    if key not in CAPTION_FONTS:
        log.warning("Unknown caption font %r; using Figtree (choose from %s)", key, ", ".join(CAPTION_FONTS))
        key = "figtree"
    return CAPTION_FONTS[key]


# Captions sit in the band from CAPTION_TOP down; a card's words stay above CARD_BOTTOM, so the
# two never overlap.
CAPTION_TOP, CARD_BOTTOM = 1240, 1150

_OVERLAY_CSS = """
@font-face{font-family:"Studio Caption";src:url("caption.woff2") format("woff2");font-weight:100 1000;font-display:block}
html,body{margin:0;width:1080px;height:1920px;overflow:hidden;background:transparent}
#overlay{position:relative;width:1080px;height:1920px;background:transparent}
.cap,.hook,.card,.callout{font-family:"Studio Caption","Noto Sans CJK SC","Noto Sans SC","Microsoft YaHei",sans-serif;
  -webkit-font-smoothing:antialiased;text-rendering:geometricPrecision}
.cap,.hook{position:absolute;left:0;right:0;margin:0 auto;width:fit-content;text-align:center;color:var(--ink);
  white-space:normal;text-wrap:balance;z-index:3}
.cap{top:%(cap_top)dpx;padding:10px 26px 13px;font-size:56px;font-weight:700;line-height:1.2;max-width:900px;
  background:rgba(var(--deep-rgb),.52);border-radius:22px;text-shadow:0 2px 10px rgba(0,0,0,.35)}
.hook{top:250px;padding:20px 34px 22px;font-size:56px;font-weight:800;line-height:1.16;max-width:900px;
  letter-spacing:-.005em;background:rgba(var(--deep-rgb),.84);border-radius:26px}
.card{position:absolute;inset:0;z-index:1;overflow:hidden;color:var(--ink)}
.card .bg{position:absolute;inset:-60px;background:radial-gradient(circle at 30%% 20%%,var(--card-hi),var(--card-lo) 72%%)}
.card .inner{position:absolute;left:90px;right:90px;top:330px;bottom:%(card_gap)dpx;display:flex;flex-direction:column;
  justify-content:center;overflow:hidden}
.card .bar{flex:none;width:120px;height:14px;border-radius:7px;background:var(--accent);margin-bottom:44px}
.card .title{font-weight:800;line-height:1.06;letter-spacing:-.01em;text-wrap:balance}
.card ul{list-style:none;margin:52px 0 0;padding:0}
.card li{font-weight:600;margin:0 0 28px;display:flex;align-items:center;gap:26px;line-height:1.15}
.card li::before{content:"";flex:0 0 24px;height:24px;border-radius:50%%;background:var(--accent)}
.callout{position:absolute;left:70px;right:70px;top:300px;z-index:2;padding:34px 40px;border-radius:26px;
  background:rgba(var(--deep-rgb),.86);border-left:14px solid var(--accent);color:var(--ink)}
.callout .big{font-size:112px;font-weight:800;line-height:1}
.callout .label{font-size:48px;font-weight:600;margin-top:14px;opacity:.92}
""" % {"cap_top": CAPTION_TOP, "card_gap": H - CARD_BOTTOM}

# Each account's look for captions and graphics: Urban Makers' green, warm white and amber
# (as on its carousels).
THEME = {
    "urban_makers": {"accent": "#f5a524", "ink": "#f6f4ee", "deep-rgb": "24,35,31", "card-hi": "#2f4540",
                     "card-lo": "#18231f"},
    "personal": {"accent": "#7c9cff", "ink": "#ffffff", "deep-rgb": "11,11,13", "card-hi": "#2a2a33",
                 "card-lo": "#0b0b0d"},
}
ACCENT = {k: v["accent"] for k, v in THEME.items()}


def _theme_css(accent: str) -> str:
    theme = next((t for t in THEME.values() if t["accent"] == accent), THEME["urban_makers"])
    return ":root{" + ";".join(f"--{k}:{v}" for k, v in {**theme, "accent": accent}.items()) + "}"


def _card_sizes(title: str, lines: list[str]) -> tuple[int, int]:
    """Headline and point sizes for a card, by how much text it has, so it stays clear of the
    caption band (the box also hides any overflow)."""
    line = max((len(x) for x in lines), default=0)
    title_px = 100 if len(title) <= 14 else 92 if len(title) <= 20 else 80
    return title_px, 62 if line <= 16 else 56 if line <= 24 else 48


def _graphic_html(i: int, g: dict) -> tuple[str, str]:
    """Markup and GSAP animation for one B-roll graphic. Everything moves, gently."""
    s, d, gid = g["start"], g["duration"], f"g{i}"
    attrs = f'id="{gid}" data-start="{s:.3f}" data-duration="{d:.3f}" data-track-index="{2 + i}"'
    if g["style"] == "card":
        title_px, line_px = _card_sizes(g["title"], g["lines"])
        lines = "".join(f'<li style="font-size:{line_px}px">{html.escape(line)}</li>' for line in g["lines"])
        markup = (f'<div {attrs} class="card clip"><div class="bg"></div><div class="inner"><div class="bar"></div>'
                  f'<div class="title" style="font-size:{title_px}px">{html.escape(g["title"])}</div>'
                  f'<ul>{lines}</ul></div></div>')
        anim = (f"tl.from('#{gid}',{{opacity:0,duration:.25}},{s:.3f});"
                f"tl.fromTo('#{gid} .bg',{{scale:1.12}},{{scale:1,duration:{d:.3f},ease:'none'}},{s:.3f});"
                f"tl.from('#{gid} .bar',{{scaleX:0,transformOrigin:'left',duration:.4,ease:'power3.out'}},{s + .05:.3f});"
                f"tl.from('#{gid} .title',{{y:50,opacity:0,duration:.45,ease:'power3.out'}},{s + .1:.3f});"
                f"tl.from('#{gid} li',{{x:-50,opacity:0,duration:.35,stagger:.22,ease:'power2.out'}},{s + .35:.3f});")
    else:
        label = html.escape(g["lines"][0]) if g["lines"] else ""
        markup = (f'<div {attrs} class="callout clip"><div class="big">{html.escape(g["title"])}</div>'
                  + (f'<div class="label">{label}</div>' if label else "") + "</div>")
        anim = (f"tl.from('#{gid}',{{y:-40,opacity:0,duration:.35,ease:'power2.out'}},{s:.3f});"
                f"tl.from('#{gid} .big',{{scale:.7,duration:.45,ease:'back.out(2)'}},{s + .05:.3f});")
    return markup, anim


def render_overlay(chunks: list[dict], hook: str, total: float, out: Path,
                   graphics: list[dict] = (), accent: str = "#f5a524", fmt: str = "webm", font: str | None = None) -> None:
    """Captions, hook and B-roll graphics as a transparent video (HyperFrames, headless Chrome):
    VP9 WebM for the draft, ProRes 4444 MOV (fmt="mov") for DaVinci Resolve. The font is one
    of the bundled ones (caption_font), copied next to the page."""
    with tempfile.TemporaryDirectory(prefix="studio-overlay-") as tmp:
        project = Path(tmp) / "overlay"
        project.mkdir()
        shutil.copy(render.RENDER_DIR / "node_modules" / "gsap" / "dist" / "gsap.min.js", project)
        shutil.copy(caption_font(font)[1], project / "caption.woff2")
        clips = []
        if hook.strip():
            clips.append(f'<div id="hook" class="hook clip" data-start="0" data-duration="{min(2.6, total):.3f}" '
                         f'data-track-index="1">{html.escape(hook.strip())}</div>')
        for i, c in enumerate(chunks):
            clips.append(f'<div id="c{i}" class="cap clip" data-start="{c["start"]:.3f}" '
                         f'data-duration="{c["end"] - c["start"]:.3f}" data-track-index="0">{html.escape(c["text"])}</div>')
        animations = []
        for i, g in enumerate(graphics):
            markup, anim = _graphic_html(i, g)
            clips.append(markup)
            animations.append(anim)
        (project / "index.html").write_text(
            '<!doctype html><html><head><meta charset="utf-8"><script src="gsap.min.js"></script>'
            f"<style>{_theme_css(accent)}{_OVERLAY_CSS}</style></head><body>"
            f'<div id="overlay" data-composition-id="overlay" data-start="0" data-duration="{total:.3f}" '
            f'data-width="{W}" data-height="{H}">' + "".join(clips) + "</div>"
            "<script>const tl=gsap.timeline({paused:true});" + "".join(animations) +
            "window.__timelines=window.__timelines||{};window.__timelines['overlay']=tl;</script></body></html>",
            encoding="utf-8")
        (project / "meta.json").write_text(json.dumps({"id": "overlay", "name": "overlay", "width": W,
                                                       "height": H, "fps": FPS}), encoding="utf-8")
        result = render.hyperframes(["render", str(project), "--format", fmt, "--quality", "standard",
                                     "--fps", str(FPS), "--output", str(out)])
        if result.returncode != 0 or not out.exists():
            raise RuntimeError("caption render failed: " + ((result.stderr or "") + (result.stdout or "")).strip()[-400:])


# The sound. The voice is levelled first: a gentle high-pass and noise reduction suited to speech,
# then a fixed gain to VOICE_LUFS (measured, so nothing pumps) and a light compressor. The music
# is set against that levelled voice: BED_BELOW under it when nobody talks, and dipped to
# settings.MUSIC_GAP_DB under it while Bryan talks (smooth ramps from the words' own times).
# Then the whole mix is brought to -14 LUFS / -1.5 dBTP in two passes (a fixed gain, no pumping).
VOICE_LUFS = -16.0
BED_BELOW = 9.0
DUCK_ATTACK, DUCK_RELEASE = 0.25, 0.8
SPEECH_JOIN = 0.8   # gaps between words shorter than this are still "talking"
TARGET_LUFS, TARGET_TP = -14.0, -1.5
_VOICE_CLEAN = "highpass=f=80,afftdn=nr=12:nf=-50:tn=1"
_VOICE_SQUEEZE = "acompressor=threshold=0.1:ratio=2.5:attack=12:release=250:knee=4"
_VOICE_LIMIT = "alimiter=limit=0.89:level=false"


def speech_spans(words: list[dict], join: float = SPEECH_JOIN) -> list[tuple[float, float]]:
    """When Bryan is talking, on the draft's clock (words closer than `join` run together)."""
    spans = []
    for w in sorted(words, key=lambda w: w["start"]):
        if spans and w["start"] - spans[-1][1] <= join:
            spans[-1][1] = max(spans[-1][1], w["end"])
        else:
            spans.append([w["start"], w["end"]])
    return [(round(a, 3), round(b, 3)) for a, b in spans]


def loudness(path: Path) -> float | None:
    """Integrated loudness (LUFS) of a file's sound; None when it's silent."""
    result = extract._run([settings.FFMPEG, "-hide_banner", "-nostats", "-i", str(path), "-vn",
                           "-af", "ebur128", "-f", "null", "-"])
    found = re.findall(r"I:\s+(-?[\d.]+) LUFS", result.stderr)
    value = float(found[-1]) if found else None
    return value if value is not None and value > -69 else None


def _duck_expr(spans: list[tuple[float, float]], gain_db: float, depth_db: float) -> str:
    """ffmpeg volume expression of t: gain_db, dipped by depth_db while Bryan talks, with smooth
    ramps (DUCK_ATTACK before he starts, DUCK_RELEASE after he stops)."""
    if not spans or depth_db <= 0:
        return f"{10 ** (gain_db / 20):.6f}"
    a, r = DUCK_ATTACK, DUCK_RELEASE
    parts = [f"clip(min((t-{s - a:.3f})/{a},({e + r:.3f}-t)/{r}),0,1)" for s, e in spans]
    while len(parts) > 1:  # a balanced tree of max(), so long posts don't nest deeply
        parts = [f"max({x},{y})" if y else x for x, y in itertools.zip_longest(parts[::2], parts[1::2])]
    return f"pow(10,({gain_db:.2f}-{depth_db:.2f}*{parts[0]})/20)"


# The ear's weighting loudness meters use (EBU R128 K-weighting at 48 kHz), so the gap is measured
# the way it's heard and matches the levels the mix is set in (bass-heavy music isn't over-read).
_K_WEIGHT = ("biquad=b0=1.53512485958697:b1=-2.69169618940638:b2=1.19839281085285:a0=1:a1=-1.69065929318241:"
             "a2=0.73248077421585,biquad=b0=1:b1=-2:b2=1:a0=1:a1=-1.99004745483398:a2=0.99007225036621")


def _read_raw(path: Path, weighted: bool = True):
    """A raw float stereo file as mono samples, K-weighted unless asked not to."""
    import numpy as np
    if weighted:
        out = path.with_name(path.stem + "_k.f32")
        extract._run([settings.FFMPEG, "-y", "-v", "error", "-f", "f32le", "-ar", str(RATE), "-ac", "2", "-i", str(path),
                      "-af", _K_WEIGHT, "-f", "f32le", str(out)])
        samples = np.fromfile(out, dtype="<f4").reshape(-1, 2).mean(axis=1)
        out.unlink(missing_ok=True)
        return samples
    return np.fromfile(path, dtype="<f4").reshape(-1, 2).mean(axis=1)


def _db(x) -> float:
    import numpy as np
    power = float(np.mean(np.square(x, dtype="float64"))) if len(x) else 0.0
    return 10 * np.log10(power) if power > 1e-12 else -120.0


def speech_music_gap(voice, music, words: list[dict], total: float, rate: int = RATE) -> dict:
    """How far the music sits under the voice while Bryan talks (dB, on the words themselves),
    and how far under his talking level it plays where nobody talks (the bed), from the two
    sounds before they're mixed (raw float stereo files, measured K-weighted; or arrays of
    samples, taken as they are)."""
    import numpy as np
    v = _read_raw(voice) if isinstance(voice, Path) else voice
    m = _read_raw(music) if isinstance(music, Path) else music
    n = min(len(v), len(m))
    talk = np.zeros(n, bool)
    for w in words:
        talk[int(w["start"] * rate):int(w["end"] * rate)] = True
    quiet = np.ones(n, bool)  # well away from any talking (past the ramps)
    for s, e in speech_spans(words):
        quiet[max(0, int((s - DUCK_ATTACK - 0.3) * rate)):int((e + DUCK_RELEASE + 0.3) * rate)] = False
    quiet[:int(0.6 * rate)] = False                       # the music's fade in
    quiet[max(0, int((total - 1.6) * rate)):] = False     # and its fade out
    out = {"voice_db": None, "music_under_voice_db": None, "gap_db": None, "bed_db": None, "bed_below_db": None}
    if talk[:n].any():
        voice_db, music_db = _db(v[:n][talk]), _db(m[:n][talk])
        out.update(voice_db=round(voice_db, 1), music_under_voice_db=round(music_db, 1),
                   gap_db=round(voice_db - music_db, 1))
    if quiet.sum() > rate:  # at least a second of music on its own
        out["bed_db"] = round(_db(m[:n][quiet]), 1)
        if out["voice_db"] is not None:
            out["bed_below_db"] = round(out["voice_db"] - out["bed_db"], 1)
    return out


def _loudnorm_args(measure: dict) -> str:
    return (f"loudnorm=I={TARGET_LUFS}:TP={TARGET_TP}:LRA=11:measured_I={measure['input_i']}:"
            f"measured_LRA={measure['input_lra']}:measured_TP={measure['input_tp']}:"
            f"measured_thresh={measure['input_thresh']}:offset={measure['target_offset']}:linear=true:print_format=none")


def _measure_loudnorm(args: list[str]) -> dict:
    """loudnorm's first pass over the sound from these input args."""
    result = extract._run([settings.FFMPEG, "-hide_banner", "-nostats", *args,
                           "-af", f"loudnorm=I={TARGET_LUFS}:TP={TARGET_TP}:LRA=11:print_format=json", "-f", "null", "-"])
    text = result.stderr
    return json.loads(text[text.rindex("{"):text.rindex("}") + 1])


def mix_final(cut: Path, overlay: Path | None, music_file: Path | None, volume_db: float, total: float,
              out: Path, words: list[dict] = ()) -> dict:
    """The final draft: footage, the caption layer, and the sound (the voice levelled, the
    music set under it, see VOICE_LUFS). Returns what it measured and did."""
    raw = ["-f", "f32le", "-ar", str(RATE), "-ac", "2"]
    with tempfile.TemporaryDirectory(prefix="studio-mix-") as tmp:
        work = Path(tmp)
        clean = work / "voice_clean.wav"
        extract._run([settings.FFMPEG, "-y", "-v", "error", "-i", str(cut), "-vn", "-af",
                      f"aresample={RATE},aformat=channel_layouts=stereo,{_VOICE_CLEAN}", "-c:a", "pcm_f32le", str(clean)])
        heard = loudness(clean)
        talking = bool(words) and heard is not None
        voice_gain = min(max(VOICE_LUFS - heard, -12.0), 30.0) if talking else 0.0
        makeup = 0.0
        if talking:  # the compressor takes some level off: measure again and make it up after it
            levelled = work / "voice_levelled.wav"
            extract._run([settings.FFMPEG, "-y", "-v", "error", "-i", str(clean), "-af",
                          f"volume={voice_gain:.2f}dB,{_VOICE_SQUEEZE}", "-c:a", "pcm_f32le", str(levelled)])
            makeup = min(max(VOICE_LUFS - (loudness(levelled) or VOICE_LUFS), 0.0), 12.0)
        report = {"voice_lufs_recorded": heard, "voice_gain_db": round(voice_gain + makeup, 1), "music": bool(music_file)}
        voice_raw, music_raw = work / "voice.f32", work / "music.f32"
        args = [settings.FFMPEG, "-y", "-v", "error", "-i", str(clean)]
        graph = [f"[0:a]volume={voice_gain:.2f}dB,{_VOICE_SQUEEZE},volume={makeup:.2f}dB,{_VOICE_LIMIT},"
                 f"apad=whole_len={round(total * RATE)},atrim=end_sample={round(total * RATE)}[v]"]
        outputs = ["-map", "[v]", *raw, str(voice_raw)]
        if music_file:
            bed_track = work / "music.wav"
            extract._run([settings.FFMPEG, "-y", "-v", "error", "-stream_loop", "-1", "-i", str(music_file), "-t",
                          f"{total:.3f}", "-af", f"aresample={RATE},aformat=channel_layouts=stereo", "-c:a", "pcm_f32le",
                          str(bed_track)])
            track = loudness(bed_track) or -20.0
            # Claude's volume_db: -16 is the usual bed, lower for a quieter one.
            offset = min(max((volume_db if volume_db is not None else -16.0) + 16.0, -8.0), 4.0)
            if talking:
                bed = VOICE_LUFS - BED_BELOW + offset
                depth = max(4.0, bed - (VOICE_LUFS - settings.MUSIC_GAP_DB))
            else:  # nobody talks: the music carries the post
                bed, depth = VOICE_LUFS + offset, 0.0
            music_gain = bed - track
            spans = speech_spans(words)
            fade_at = max(0.0, total - 1.5)
            args += ["-i", str(bed_track)]
            graph.append(f"[1:a]asetnsamples=n=256,volume='{_duck_expr(spans, music_gain, depth)}':eval=frame,"
                         f"afade=t=in:d=0.4,afade=t=out:st={fade_at:.3f}:d=1.5[m]")
            outputs += ["-map", "[m]", *raw, str(music_raw)]
            report.update(music_track_lufs=track, music_gain_db=round(music_gain, 1), music_bed_lufs=round(bed, 1),
                          duck_db=round(depth, 1))
        extract._run(args + ["-filter_complex", ";".join(graph), *outputs])
        lowered = 0.0
        if music_file:
            gap = speech_music_gap(voice_raw, music_raw, list(words), total)
            # The check that can't regress: never less than the gap the setting asks for.
            if gap["gap_db"] is not None and gap["gap_db"] < settings.MUSIC_GAP_DB - 1.0:
                lowered = round(settings.MUSIC_GAP_DB - gap["gap_db"], 1)
                gap = {k: (round(v - lowered, 1) if k in ("music_under_voice_db", "bed_db") and v is not None
                           else round(v + lowered, 1) if k in ("gap_db", "bed_below_db") and v is not None else v)
                       for k, v in gap.items()}
            report.update(gap, music_lowered_db=lowered)
        sound_in = [*raw, "-i", str(voice_raw)] + ([*raw, "-i", str(music_raw)] if music_file else [])
        if music_file:
            mix = ((f"[1:a]volume=-{lowered}dB[ml];[0:a][ml]" if lowered else "[0:a][1:a]")
                   + "amix=inputs=2:duration=first:normalize=0")
        else:
            mix = "[0:a]anull"
        mixed = work / "mix.wav"
        extract._run([settings.FFMPEG, "-y", "-v", "error", *sound_in, "-filter_complex", mix + "[a]", "-map", "[a]",
                      "-c:a", "pcm_f32le", str(mixed)])
        measure = _measure_loudnorm(["-i", str(mixed)])
        report["mix_lufs_before"] = float(measure["input_i"])

        args = [settings.FFMPEG, "-y", "-v", "error", "-i", str(cut)]
        graph, video = [], "0:v"
        if overlay:
            args += ["-c:v", "libvpx-vp9", "-i", str(overlay)]  # libvpx keeps the alpha channel
            graph.append("[0:v][1:v]overlay=0:0:format=auto:shortest=1[v]")
            video = "[v]"
        args += ["-i", str(mixed)]
        graph.append(f"[{2 if overlay else 1}:a]{_loudnorm_args(measure)},aresample={RATE}[a]")
        args += ["-filter_complex", ";".join(graph), "-map", video, "-map", "[a]",
                 "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-profile:v", "high", "-pix_fmt", "yuv420p",
                 "-r", str(FPS), "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart",
                 "-t", f"{total:.3f}", str(out)]
        extract._run(args)
    return report


def fix_loudness(final: Path, measured: dict) -> bool:
    """A last check on the finished file: if its loudness is off Instagram's level by more than
    1 LU or its peaks are too hot, the sound is corrected in place (the picture is copied)."""
    lufs, peak = measured.get("lufs"), measured.get("true_peak")
    if lufs is None or (abs(lufs - TARGET_LUFS) <= 1.0 and (peak is None or peak <= -1.0)):
        return False
    fixed = final.with_name(final.stem + "_level" + final.suffix)
    measure = _measure_loudnorm(["-i", str(final)])
    extract._run([settings.FFMPEG, "-y", "-v", "error", "-i", str(final), "-map", "0:v", "-map", "0:a", "-c:v", "copy",
                  "-af", _loudnorm_args(measure) + f",aresample={RATE}", "-c:a", "aac", "-b:a", "192k",
                  "-movflags", "+faststart", str(fixed)])
    fixed.replace(final)
    return True


# ── Checks ────────────────────────────────────────────────────────────────────

def _norm(word: str) -> str:
    return re.sub(r"[^a-z0-9']", "", word.lower())


def check_words(final: Path, expected: list[dict], work: Path) -> dict:
    """Re-transcribe the draft and compare with the words the cut was meant to keep."""
    import transcribe
    wav = extract.extract_audio(final, work / "check.wav")
    heard = [_norm(w["word"]) for w in transcribe.transcribe(wav)["words"]]
    want = [_norm(w["word"]) for w in expected]
    heard, want = [w for w in heard if w], [w for w in want if w]
    if not want:
        return {"words_expected": 0, "match": None, "missing": []}
    matcher = difflib.SequenceMatcher(a=want, b=heard, autojunk=False)
    missing = [want[i] for tag, i1, i2, _, _ in matcher.get_opcodes() if tag in ("delete", "replace")
               for i in range(i1, i2)]
    return {"words_expected": len(want), "match": round(matcher.ratio(), 3), "missing": missing[:20]}


def check_loudness(final: Path) -> dict:
    result = extract._run([settings.FFMPEG, "-hide_banner", "-nostats", "-i", str(final),
                           "-af", "ebur128=peak=true", "-f", "null", "-"])
    text = result.stderr
    lufs = re.findall(r"I:\s+(-?[\d.]+) LUFS", text)
    peak = re.findall(r"Peak:\s+(-?[\d.]+) dBFS", text)
    return {"lufs": float(lufs[-1]) if lufs else None, "true_peak": float(peak[-1]) if peak else None}


# ── Orchestration ─────────────────────────────────────────────────────────────

def _clips_for(conn, ids: list[int]) -> dict[int, dict]:
    import onedrive
    onedrive.ensure_originals(conn, ids)  # originals the clean-up freed come back from OneDrive first
    rows = conn.execute(f"SELECT * FROM clips WHERE id IN ({','.join('?' * len(ids))})", ids).fetchall() if ids else []
    return {r["id"]: dict(r) for r in rows if Path(r["video_path"]).exists()}


def edit(conn, production_id: int, client=None, find_track=None) -> Path:
    """Make the next draft of a production. Returns the rendered file. Studio's read, its own
    fix rounds (self_fix.py) and the move to To review follow."""
    production = dict(conn.execute("SELECT * FROM productions WHERE id = ?", (production_id,)).fetchone())
    plan = json.loads(production["plan"] or "{}")
    ids = list(dict.fromkeys(s["clip_id"] for s in plan.get("segments", [])))
    if not ids:
        raise RuntimeError("this production has no plan to edit from")
    clips = _clips_for(conn, ids)
    if not clips:
        raise RuntimeError("none of the planned clips are in the library any more")
    catalog.set_production_status(conn, production_id, "producing")
    version = production["version"] + 1
    notes = [dict(r) for r in conn.execute(
        "SELECT * FROM feedback WHERE target = 'production' AND target_id = ? AND status = 'open' ORDER BY id",
        (production_id,))]

    # A track Bryan pasted into a note (this round or an earlier one) is the music from then on.
    linked = music.track_link("\n".join(r["body"] for r in conn.execute(
        "SELECT body FROM feedback WHERE target = 'production' AND target_id = ? ORDER BY id", (production_id,))))
    if linked:
        plan.setdefault("music_plan", {})["chosen"] = linked

    edl = plan_edit(conn, production, clips, client)
    edl["cuts"] = _cut(edl, clips)
    _render(conn, production, clips, edl, plan, version, find_track)
    if notes:  # Bryan's editing notes become rules Studio keeps (learning.py)
        import learning
        learning.from_notes(conn, notes, edl.get("notes_addressed", []), "reel")
    import self_fix
    return Path(self_fix.deliver(conn, production_id, client, find_track)["video_path"])


def _cut(edl: dict, clips: dict[int, dict]) -> list[dict]:
    """Claude's cut list made exact: snapped to the gaps between words, Studio's checks on the
    ends (sentence_guard), then jump cuts and framing. What the checks changed goes in
    edl["checks"]."""
    cuts = [s for s in (snap(c, clips[c["clip_id"]]) for c in edl["cuts"] if c["clip_id"] in clips) if s]
    if not cuts:
        raise RuntimeError("the edit plan kept no usable footage")
    cuts, edl["checks"] = sentence_guard(cuts, clips)
    return finish_cuts(cuts, clips, edl)


def finish_cuts(cuts: list[dict], clips: dict[int, dict], edl: dict) -> list[dict]:
    """After snapping: jump cuts, then framing (reframe, punch-ins), each as the
    settings say. Notes what was done in edl["framing"] and in the summary Bryan
    reads; the cuts carry it all, so a re-render or Resolve project matches."""
    planned = sum(c["end"] - c["start"] for c in cuts)
    cuts = [{**c, "part": n} for n, c in enumerate(cuts)]
    if settings.EDIT_JUMP_CUTS:
        cuts = tighten(cuts, clips)
    removed = planned - sum(c["end"] - c["start"] for c in cuts)
    stats = {}
    if settings.EDIT_REFRAME or settings.EDIT_PUNCH_INS:
        cuts, stats = framing.frame_cuts(cuts, clips, hook=bool(edl.get("hook_text", "").strip()),
                                         reframe_on=settings.EDIT_REFRAME, punch_on=settings.EDIT_PUNCH_INS,
                                         zoom=settings.EDIT_PUNCH_ZOOM)
    edl["framing"] = {"jump_cuts": settings.EDIT_JUMP_CUTS, "max_pause": settings.EDIT_MAX_PAUSE,
                      "pauses_removed": round(removed, 2), "reframe": settings.EDIT_REFRAME,
                      "punch_ins": settings.EDIT_PUNCH_INS, "zoom": settings.EDIT_PUNCH_ZOOM, **stats}
    done = []
    if removed >= 0.3:
        done.append(f"tightened the pauses ({removed:.1f}s of silence and hesitation cut)")
    if stats.get("reframed"):
        done.append("framed the horizontal shots on you")
    if stats.get("punch_ins"):
        done.append(f"punched in {stats['punch_ins']} times on the talking shots")
    if done:
        edl["summary"] = (edl.get("summary", "").rstrip() + " Studio also " + ", ".join(done[:-1])
                          + (" and " if len(done) > 1 else "") + done[-1] + ".").strip()
    return cuts


def _same_music(production: dict, plan: dict, find_track=None):
    """find_track and plan for a render that keeps the draft's music (a re-render, or one of
    Studio's own fix rounds): the same file when it's still there, else the same track."""
    music_before = json.loads(production["music"] or "{}")
    kept = Path(music_before["file"]) if music_before.get("file") else None
    if kept and kept.exists():
        find_track = lambda brief, seconds, out_dir, ref="": (kept, music_before)  # noqa: E731 - same track again
    if music_before.get("recording_id") and not (kept and kept.exists()):
        plan.setdefault("music_plan", {})["chosen"] = {
            "title": music_before.get("title", ""), "artist": music_before.get("artist", ""),
            "recording_id": music_before["recording_id"]}
    return find_track, plan


def rerender(conn, production_id: int, find_track=None) -> Path:
    """Render the current draft again from its edit plan, as a new version: picks up
    changed transcripts (captions) with no new Claude plan. Keeps the draft's music."""
    production = dict(conn.execute("SELECT * FROM productions WHERE id = ?", (production_id,)).fetchone())
    if not production.get("edl"):
        raise RuntimeError("this production has no finished draft to render again")
    edl = json.loads(production["edl"])
    ids = list(dict.fromkeys(c["clip_id"] for c in edl["cuts"]))
    clips = _clips_for(conn, ids)
    if set(ids) - set(clips):
        raise RuntimeError("some of the draft's clips are no longer in the library")
    find_track, plan = _same_music(production, json.loads(production["plan"] or "{}"), find_track)
    catalog.set_production_status(conn, production_id, "producing")
    final = _render(conn, production, clips, edl, plan, production["version"] + 1, find_track, placed=True)
    import self_fix
    self_fix.deliver(conn, production_id, rounds=0, count=False)  # the same edit: Studio's read, no rounds
    return final


def self_revise(conn, production_id: int, fixes: list[dict], round_no: int, client=None, find_track=None) -> Path:
    """One of Studio's own fix rounds, before Bryan sees the draft: the editor plans it again
    with the read's fixes (fixes: [{id, at_seconds, body}]) instead of Bryan's notes, keeps
    the music, and renders it as the same version (a file of its own). Its cost is counted
    apart (self_fix.ref)."""
    import self_fix
    production = dict(conn.execute("SELECT * FROM productions WHERE id = ?", (production_id,)).fetchone())
    plan = json.loads(production["plan"] or "{}")
    previous = json.loads(production["edl"] or "{}")
    ids = list(dict.fromkeys([c["clip_id"] for c in previous.get("cuts", [])]
                             + [s["clip_id"] for s in plan.get("segments", [])]))
    clips = _clips_for(conn, ids)
    if not clips:
        raise RuntimeError("none of the draft's clips are in the library any more")
    edl = plan_edit(conn, production, clips, client, fixes=fixes, ref=self_fix.ref(production_id))
    edl["cuts"] = _cut(edl, clips)
    find_track, plan = _same_music(production, plan, find_track)
    return _render(conn, production, clips, edl, plan, production["version"], find_track,
                   suffix=f"_fix{round_no}", address_notes=False)


def _render(conn, production: dict, clips: dict, edl: dict, plan: dict, version: int, find_track=None,
            placed: bool = False, suffix: str = "", address_notes: bool = True) -> Path:
    """Render an edit plan (EDL) to the next draft: cut, captions, graphics, music, QA. The
    draft is stored but stays 'producing': self_fix.deliver sends it to To review."""
    production_id = production["id"]
    cuts = edl["cuts"]
    # What Studio's own checks changed: editing problems (counted in Review's "caught" line) and
    # sound fixes (the recording's level, not an editing mistake).
    checks = [{"did": c, "counted": True} for c in edl.get("checks") or []]
    RENDERS.mkdir(parents=True, exist_ok=True)
    final = RENDERS / f"production_{production_id}_v{version}{suffix}.mp4"
    with tempfile.TemporaryDirectory(prefix="studio-edit-") as tmp:
        work = Path(tmp)
        total = render_cut(cuts, clips, work / "cut.mp4")
        words = timeline(cuts, clips)
        chunks = caption_chunks(words, total)
        overlay = None
        graphics = edl.get("graphics", []) if placed else place_graphics(edl.get("graphics", []), cuts, total)
        edl["graphics"] = graphics
        chunks, doubled = clear_of_cards(chunks, graphics)
        if doubled:
            checks.append({"did": f"hid {doubled} caption{'s' if doubled != 1 else ''} repeating a card's own words",
                           "counted": True})
        if chunks or edl["hook_text"].strip() or graphics:
            overlay = work / "overlay.webm"
            render_overlay(chunks, edl["hook_text"], total, overlay, graphics,
                           ACCENT.get(production["brand"], ACCENT["urban_makers"]))

        music_info, music_file = {"mood": edl["music"]["mood"], "tempo_bpm": edl["music"]["tempo_bpm"]}, None
        chosen = (plan.get("music_plan") or {}).get("chosen")
        if music.allowed_for(production["brand"]) and settings.EPIDEMIC_SOUND_API_KEY:
            brief = (f"Use exactly the track Bryan linked: {chosen['url']} (Epidemic Sound track id "
                     f"{chosen['track_id']}). Look it up by that id or link; don't swap in another track"
                     if chosen and chosen.get("url") else
                     f"Use exactly this track the team picked: \"{chosen['title']}\" by {chosen['artist']} "
                     f"(recording id {chosen['recording_id']})" if chosen else
                     f"{edl['music']['mood']}; search: {edl['music']['search']}; about {edl['music']['tempo_bpm']:.0f} BPM")
            try:
                music_file, track = (find_track or music.find_track)(
                    brief, total, ROOT / "Music", ref=f"production:{production_id}")
                music_info.update(source="epidemic", title=track["title"], artist=track["artist"],
                                  recording_id=track["recording_id"], file=str(music_file))
            except Exception as e:  # a draft without music beats no draft
                log.warning("Music step failed for production %s: %s", production_id, e)
                music_info.update(source="none", error=f"{type(e).__name__}: {e}")
        else:
            music_info.update(source="instagram",
                              note=f"Add music in the Instagram app: {edl['music']['mood']}, around "
                                   f"{edl['music']['tempo_bpm']:.0f} BPM, kept low under your voice.")

        sound = mix_final(work / "cut.mp4", overlay, music_file, edl["music"]["volume_db"], total, final, words)
        if sound.get("music_gain_db") is not None:
            music_info["gain_db"] = sound["music_gain_db"]  # the track's level in the mix (for Resolve)
        if sound.get("voice_gain_db", 0) >= 6:
            checks.append({"did": f"brought your voice up {sound['voice_gain_db']:.0f} dB to a steady level "
                                  "and cleaned up the background noise", "counted": False})
        if sound.get("music_lowered_db"):
            checks.append({"did": f"turned the music down {sound['music_lowered_db']:.0f} dB more so it stays "
                                  "under your voice", "counted": False})
        loud = check_loudness(final)
        if fix_loudness(final, loud):
            loud = check_loudness(final)
            checks.append({"did": "set the loudness to Instagram's level", "counted": False})
        qa = {**loud, **check_words(final, words, work), "sound": sound, "checks": checks}

    thumb = final.with_suffix(".jpg")
    extract._run([settings.FFMPEG, "-y", "-v", "error", "-ss", f"{min(1.0, total / 2):.2f}", "-i", str(final),
                  "-frames:v", "1", "-q:v", "3", str(thumb)])
    conn.execute(
        """UPDATE productions SET version = ?, video_path = ?, thumb_path = ?, duration = ?,
               edl = ?, qa = ?, music = ?, error = NULL, updated_at = ?, self_fix = ? WHERE id = ?""",
        (version, str(final), str(thumb), round(total, 2), json.dumps(edl), json.dumps(qa),
         json.dumps(music_info), catalog.now(), json.dumps({"version": version, "pending": True}), production_id))
    if address_notes:
        conn.execute("""UPDATE feedback SET status = 'addressed'
                        WHERE target = 'production' AND target_id = ? AND status = 'open'""", (production_id,))
    conn.commit()
    log.info("Production %s v%d%s rendered: %.1fs, words %s, %s LUFS, voice %s dB over the music", production_id,
             version, suffix, total, qa.get("match"), qa.get("lufs"), sound.get("gap_db"))
    return final


def next_job(conn) -> int | None:
    row = conn.execute("""SELECT id FROM productions WHERE status IN ('planned', 'revising')
                          ORDER BY updated_at LIMIT 1""").fetchone()
    return row["id"] if row else None


def ready() -> str | None:
    """None when the renderer is installed, else what's missing."""
    try:
        render.node_version()
        render.hyperframes_version()
        return None
    except Exception as e:
        return str(e)
