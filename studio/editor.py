"""
The editor (phase 5): turns a planned production into a draft Reel.

  1. Claude plans the cut from the approved idea: which stretches of which clips to
     keep, at phrase boundaries (it sees every word with its timestamp), the hook
     text, and the music brief. On a revision it also gets Bryan's notes.
  2. Cut points are snapped to word edges with a little air, so no word is clipped.
     Then pauses inside each kept stretch are cut down to a short breath (jump cuts),
     and lone "um"s go.
  3. Framing (framing.py): horizontal footage is cropped around the person's face,
     and talking shots punch in and out every few seconds.
  4. ffmpeg cuts every piece from the ORIGINAL files in one pass (one encode, so
     lips stay in sync) and frames it 1080x1920.
  5. HyperFrames renders the captions (1-3 words, timed to each word) and the hook
     as a transparent layer; ffmpeg lays it over the footage.
  6. Music: Urban Makers posts get an Epidemic Sound track, ducked under the voice;
     other accounts get a note on what to add in the Instagram app.
  7. Checks: the draft is re-transcribed to confirm every word survived the cuts,
     and its loudness is measured. Then it lands in To review.
  8. Studio's read (draft_review.py): Claude reviews the finished draft for Bryan.
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
                "properties": {"note_id": {"type": "integer"}, "how": {"type": "string"}},
                "required": ["note_id", "how"],
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
volume_db is the music's level before it dips under speech; around -16 is typical, lower for \
talk-heavy posts.
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
in the PREVIOUS draft; the previous cut list shows where each piece sat in that draft."""


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


def _revision_block(conn, production: dict) -> str:
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
    notes = conn.execute(
        "SELECT * FROM feedback WHERE target = 'production' AND target_id = ? AND status = 'open' ORDER BY id",
        (production["id"],)).fetchall()
    note_lines = [f"  note {n['id']}" + (f" at {n['at_seconds']:.1f}s" if n["at_seconds"] is not None else "")
                  + f": {n['body']}" for n in notes]
    return ("\n\nThis is a revision of draft v{v}. Previous cut list:\n".format(v=production["version"])
            + "\n".join(placed)
            + f"\nPrevious hook text: {previous.get('hook_text') or '(none)'}"
            + f"\nPrevious music volume: {previous.get('music', {}).get('volume_db', 'n/a')} dB"
            + "\n\nBryan's notes on that draft:\n" + ("\n".join(note_lines) or "  (none)"))


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


def plan_edit(conn, production: dict, clips: dict[int, dict], client=None) -> dict:
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
            + _revision_block(conn, production))
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
    usage.record("edit", f"production:{production['id']}", response)
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


def caption_chunks(words: list[dict], total: float) -> list[dict]:
    """1-3 words per caption, split at punctuation and pauses, shown 2 frames early."""
    chunks, current = [], []
    for i, w in enumerate(words):
        # Start a new caption rather than let this one grow too wide for the screen.
        if current and len(" ".join(x["word"] for x in current + [w])) > 18:
            chunks.append(current)
            current = []
        current.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else None
        pause = nxt is not None and nxt["start"] - w["end"] > 0.3
        if len(current) == 3 or re.search(r"[.,!?;:]$", w["word"]) or pause or nxt is None:
            chunks.append(current)
            current = []
    out = []
    for i, chunk in enumerate(chunks):
        start = max(0.0, chunk[0]["start"] - 2 / FPS)
        end = chunks[i + 1][0]["start"] - 2 / FPS if i + 1 < len(chunks) else min(total, chunk[-1]["end"] + 0.4)
        end = min(end, chunk[-1]["end"] + 0.8)  # don't hold a caption through a long silence
        text = re.sub(r"[.,;:]+$", "", " ".join(w["word"] for w in chunk)).upper()
        if end - start > 0.05 and text:
            out.append({"text": text, "start": round(start, 3), "end": round(end, 3)})
    return out


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


_OVERLAY_CSS = """
html,body{margin:0;width:1080px;height:1920px;overflow:hidden;background:transparent}
#overlay{position:relative;width:1080px;height:1920px;background:transparent}
.cap,.hook{position:absolute;left:0;right:0;margin:0 auto;width:fit-content;text-align:center;color:#fff;
  font-family:"Montserrat","Arial Black","Segoe UI Black","Segoe UI",Arial,sans-serif;font-weight:900;
  background:rgba(0,0,0,.62);border:2px solid rgba(255,255,255,.38);border-radius:16px;white-space:nowrap}
.cap{top:1240px;padding:12px 26px;font-size:64px;letter-spacing:1px;max-width:940px;white-space:normal}
.hook{top:250px;padding:18px 30px;font-size:58px;max-width:920px;white-space:normal;line-height:1.15}
.cap,.hook{z-index:3}
.card{position:absolute;inset:0;z-index:1;overflow:hidden;color:#fff;
  font-family:"Montserrat","Arial Black","Segoe UI Black","Segoe UI",Arial,sans-serif}
.card .bg{position:absolute;inset:-60px;background:radial-gradient(circle at 30% 20%,#2a2a33,#0b0b0d 70%)}
.card .inner{position:absolute;left:90px;right:90px;top:520px}
.card .bar{width:120px;height:14px;border-radius:7px;background:var(--accent);margin-bottom:44px}
.card .title{font-size:96px;font-weight:900;line-height:1.05;letter-spacing:-1px}
.card ul{list-style:none;margin:56px 0 0;padding:0}
.card li{font-size:58px;font-weight:800;margin:0 0 30px;display:flex;align-items:center;gap:26px}
.card li::before{content:"";flex:0 0 26px;height:26px;border-radius:50%;background:var(--accent)}
.callout{position:absolute;left:70px;right:70px;top:300px;z-index:2;padding:34px 40px;border-radius:24px;
  background:rgba(11,11,13,.82);border-left:14px solid var(--accent);color:#fff;
  font-family:"Montserrat","Arial Black","Segoe UI Black","Segoe UI",Arial,sans-serif}
.callout .big{font-size:120px;font-weight:900;line-height:1}
.callout .label{font-size:50px;font-weight:700;margin-top:14px;opacity:.9}
"""

# Accent colour per account for graphics (the dashboard's colours; swap for brand colours).
ACCENT = {"urban_makers": "#f5a524", "personal": "#7c9cff"}


def _graphic_html(i: int, g: dict) -> tuple[str, str]:
    """Markup and GSAP animation for one B-roll graphic. Everything moves, gently."""
    s, d, gid = g["start"], g["duration"], f"g{i}"
    attrs = f'id="{gid}" data-start="{s:.3f}" data-duration="{d:.3f}" data-track-index="{2 + i}"'
    if g["style"] == "card":
        lines = "".join(f"<li>{html.escape(line)}</li>" for line in g["lines"])
        markup = (f'<div {attrs} class="card clip"><div class="bg"></div><div class="inner"><div class="bar"></div>'
                  f'<div class="title">{html.escape(g["title"])}</div><ul>{lines}</ul></div></div>')
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
                   graphics: list[dict] = (), accent: str = "#ff4d6d", fmt: str = "webm") -> None:
    """Captions, hook and B-roll graphics as a transparent video (HyperFrames, headless Chrome):
    VP9 WebM for the draft, ProRes 4444 MOV (fmt="mov") for DaVinci Resolve."""
    with tempfile.TemporaryDirectory(prefix="studio-overlay-") as tmp:
        project = Path(tmp) / "overlay"
        project.mkdir()
        shutil.copy(render.RENDER_DIR / "node_modules" / "gsap" / "dist" / "gsap.min.js", project)
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
            f"<style>:root{{--accent:{accent}}}{_OVERLAY_CSS}</style></head><body>"
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


def mix_final(cut: Path, overlay: Path | None, music_file: Path | None, volume_db: float, total: float,
              out: Path) -> None:
    args = [settings.FFMPEG, "-y", "-v", "error", "-i", str(cut)]
    graph, video = [], "0:v"
    if overlay:
        args += ["-c:v", "libvpx-vp9", "-i", str(overlay)]  # libvpx keeps the alpha channel
        graph.append("[0:v][1:v]overlay=0:0:format=auto:shortest=1[v]")
        video = "[v]"
    if music_file:
        m = 2 if overlay else 1
        args += ["-stream_loop", "-1", "-i", str(music_file)]
        fade_at = max(0.0, total - 1.5)
        graph += [
            f"[{m}:a]atrim=duration={total:.3f},asetpts=PTS-STARTPTS,aresample=48000,"
            f"aformat=channel_layouts=stereo,volume={volume_db:.1f}dB,afade=t=in:d=0.4,"
            f"afade=t=out:st={fade_at:.3f}:d=1.5[m]",
            "[0:a]asplit=2[voice][key]",
            # The music dips whenever Bryan speaks.
            "[m][key]sidechaincompress=threshold=0.02:ratio=8:attack=15:release=400[ducked]",
            "[voice][ducked]amix=inputs=2:duration=first:normalize=0,loudnorm=I=-14:TP=-1.5:LRA=11[a]",
        ]
    else:
        graph.append("[0:a]loudnorm=I=-14:TP=-1.5:LRA=11[a]")
    args += ["-filter_complex", ";".join(graph), "-map", video, "-map", "[a]",
             "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-profile:v", "high", "-pix_fmt", "yuv420p",
             "-r", str(FPS), "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart",
             "-t", f"{total:.3f}", str(out)]
    extract._run(args)


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

def edit(conn, production_id: int, client=None, find_track=None) -> Path:
    """Make the next draft of a production. Returns the rendered file."""
    production = dict(conn.execute("SELECT * FROM productions WHERE id = ?", (production_id,)).fetchone())
    plan = json.loads(production["plan"] or "{}")
    ids = list(dict.fromkeys(s["clip_id"] for s in plan.get("segments", [])))
    if not ids:
        raise RuntimeError("this production has no plan to edit from")
    rows = conn.execute(f"SELECT * FROM clips WHERE id IN ({','.join('?' * len(ids))})", ids).fetchall()
    clips = {r["id"]: dict(r) for r in rows if Path(r["video_path"]).exists()}
    if not clips:
        raise RuntimeError("none of the planned clips are in the library any more")
    catalog.set_production_status(conn, production_id, "producing")
    version = production["version"] + 1

    edl = plan_edit(conn, production, clips, client)
    cuts = [s for s in (snap(c, clips[c["clip_id"]]) for c in edl["cuts"] if c["clip_id"] in clips) if s]
    if not cuts:
        raise RuntimeError("the edit plan kept no usable footage")
    edl["cuts"] = finish_cuts(cuts, clips, edl)
    return _render(conn, production, clips, edl, plan, version, find_track)


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


def rerender(conn, production_id: int, find_track=None) -> Path:
    """Render the current draft again from its edit plan, as a new version: picks up
    changed transcripts (captions) with no new Claude plan. Keeps the draft's music."""
    production = dict(conn.execute("SELECT * FROM productions WHERE id = ?", (production_id,)).fetchone())
    if not production.get("edl"):
        raise RuntimeError("this production has no finished draft to render again")
    edl = json.loads(production["edl"])
    ids = list(dict.fromkeys(c["clip_id"] for c in edl["cuts"]))
    rows = conn.execute(f"SELECT * FROM clips WHERE id IN ({','.join('?' * len(ids))})", ids).fetchall()
    clips = {r["id"]: dict(r) for r in rows if Path(r["video_path"]).exists()}
    if set(ids) - set(clips):
        raise RuntimeError("some of the draft's clips are no longer in the library")
    music_before = json.loads(production["music"] or "{}")
    kept = Path(music_before["file"]) if music_before.get("file") else None
    if kept and kept.exists():
        find_track = lambda brief, seconds, out_dir, ref="": (kept, music_before)  # noqa: E731 - same track again
    plan = json.loads(production["plan"] or "{}")
    if music_before.get("recording_id") and not (kept and kept.exists()):
        plan.setdefault("music_plan", {})["chosen"] = {
            "title": music_before.get("title", ""), "artist": music_before.get("artist", ""),
            "recording_id": music_before["recording_id"]}
    catalog.set_production_status(conn, production_id, "producing")
    return _render(conn, production, clips, edl, plan, production["version"] + 1, find_track, placed=True)


def _render(conn, production: dict, clips: dict, edl: dict, plan: dict, version: int, find_track=None,
            placed: bool = False) -> Path:
    """Render an edit plan (EDL) to the next draft: cut, captions, graphics, music, QA."""
    production_id = production["id"]
    cuts = edl["cuts"]
    RENDERS.mkdir(parents=True, exist_ok=True)
    final = RENDERS / f"production_{production_id}_v{version}.mp4"
    with tempfile.TemporaryDirectory(prefix="studio-edit-") as tmp:
        work = Path(tmp)
        total = render_cut(cuts, clips, work / "cut.mp4")
        words = timeline(cuts, clips)
        chunks = caption_chunks(words, total)
        overlay = None
        graphics = edl.get("graphics", []) if placed else place_graphics(edl.get("graphics", []), cuts, total)
        edl["graphics"] = graphics
        if chunks or edl["hook_text"].strip() or graphics:
            overlay = work / "overlay.webm"
            render_overlay(chunks, edl["hook_text"], total, overlay, graphics,
                           ACCENT.get(production["brand"], "#ff4d6d"))

        music_info, music_file = {"mood": edl["music"]["mood"], "tempo_bpm": edl["music"]["tempo_bpm"]}, None
        chosen = (plan.get("music_plan") or {}).get("chosen")
        if music.allowed_for(production["brand"]) and settings.EPIDEMIC_SOUND_API_KEY:
            brief = (f"Use exactly this track the team picked: \"{chosen['title']}\" by {chosen['artist']} "
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

        mix_final(work / "cut.mp4", overlay, music_file, edl["music"]["volume_db"], total, final)
        qa = {**check_loudness(final), **check_words(final, words, work)}

    thumb = final.with_suffix(".jpg")
    extract._run([settings.FFMPEG, "-y", "-v", "error", "-ss", f"{min(1.0, total / 2):.2f}", "-i", str(final),
                  "-frames:v", "1", "-q:v", "3", str(thumb)])
    conn.execute(
        """UPDATE productions SET status = 'ready', version = ?, video_path = ?, thumb_path = ?, duration = ?,
               edl = ?, qa = ?, music = ?, error = NULL, updated_at = ? WHERE id = ?""",
        (version, str(final), str(thumb), round(total, 2), json.dumps(edl), json.dumps(qa),
         json.dumps(music_info), catalog.now(), production_id))
    conn.execute("""UPDATE feedback SET status = 'addressed'
                    WHERE target = 'production' AND target_id = ? AND status = 'open'""", (production_id,))
    conn.commit()
    log.info("Production %s v%d rendered: %.1fs, words %s, %s LUFS", production_id, version, total,
             qa.get("match"), qa.get("lufs"))
    import draft_review
    draft_review.safe_run(conn, production_id)  # Studio's read; a failure leaves the draft as it is
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
