"""
The editor (phase 5): turns a planned production into a draft Reel.

  1. Claude plans the cut from the approved idea: which stretches of which clips to
     keep, at phrase boundaries (it sees every word with its timestamp), the hook
     text, and the music brief. On a revision it also gets Bryan's notes (and sorts
     each into an editing problem, which Studio learns as a rule, or taste).
  2. Cut points are snapped to word edges with a little air, then moved to where the
     voice really stops and starts in the clip's own sound (voice_edges.py: Whisper's
     word ends are often early, which clipped the end of "comfortable"), so no word is
     clipped. The cut never breaks a sentence (join_guard: words cut from inside a kept
     sentence come back, or Claude is asked once whether it still reads). Studio's checks
     on the ends (sentence_guard): never end on a stray word that starts a new sentence,
     never open on the tail of one, no dead air at either end. Then pauses inside each
     kept stretch are cut down to a short breath (jump cuts), and only true hesitations
     ("um", "uh", 嗯, 呃) and stutters ("the the": the first copy) go.
  3. Framing (framing.py): horizontal footage is cropped around the person's face,
     and talking shots punch in and out every few seconds.
  4. ffmpeg cuts every piece from the ORIGINAL files in one pass (one encode, so
     lips stay in sync) and frames it 1080x1920.
  5. HyperFrames renders the captions (1-3 words in sentence case, timed to each word,
     in a bundled font: STUDIO_CAPTION_FONT) and the hook as a transparent layer; cards
     stay above the caption line and captions repeating a card's words are hidden. The look
     (STUDIO_TITLE_STYLE): site, Bryan's pick of 2026-10-22 (Site Notes: one bold font, words on
     dark labels, a key word now and then in brass, cards as a dark panel over the footage), gallery
     (a serif title with one italic word, captions glow, a key word in the serif italic), or the
     classic first look. A draft keeps the look it was made in (edl["look"]).
     Drawn inserts (STUDIO_EDIT_SCENES): where Bryan explains something the footage doesn't
     show, or a shot is missing, Claude may place a scene from the motion library (motion.py);
     Studio checks it (place_scenes: the library's rules, timing to his words, how many, a
     panel only clear of his face), renders it once (cached by its spec) and lays it over the
     footage under the captions in the final encode, while his voice carries on.
  6. Sound (mix_final): the voice is cleaned and levelled first, the music set well under
     it (dipped while Bryan talks), the gap measured and guarded, then -14 LUFS. Urban
     Makers posts get an Epidemic Sound track; other accounts get a note on what to add.
  7. Checks: every join is listened to in the source (is the voice still going right
     before the cut? then the word was clipped: the cut is extended and rendered again),
     the draft is re-transcribed to confirm every word survived the cuts, and its
     loudness is measured (and corrected if it's off).
  8. Studio's read (draft_review.py), then Studio's own fix rounds (self_fix.py) on the
     fixes the editor can make, and only then To review.
  A voice-over (voiceover.py: a talking head Bryan filmed in his studio, plus site B-roll) is cut the same way on
  his voice; the B-roll in the plan is laid over it, with him in a small window and full screen for his first and
  last sentence. A talking head filmed on site, or one with no B-roll, is edited as above.
"""
import difflib
import html
import itertools
import json
import logging
import math
import re
import shutil
import tempfile
from pathlib import Path

import anthropic

import catalog
import extract
import framing
import motion
import music
import playbook
import render
import settings
import voice_edges
import wording
from settings import ANTHROPIC_API_KEY, CLAUDE_MODEL, ROOT

log = logging.getLogger("studio.editor")

RENDERS = ROOT / "Renders"
W, H, FPS = 1080, 1920, 30
# Whisper ends a word slightly before the sound dies; keep a little air either side. These are
# the least air a cut gets: where the clip's sound is known (voice_edges), a cut end moves on to
# where the voice really stops and a cut start back to where it really starts.
HEAD_PAD, TAIL_PAD = 0.08, 0.15
MIN_CUT = 0.4
# Jump cuts: a pause between sentences or clauses (after a full stop, a comma and the like)
# longer than settings.EDIT_MAX_PAUSE, or any pause longer than LONG_PAUSE, is cut down to this
# much air after the last word and before the next (a natural breath, about a quarter of a second;
# at least this much: voice_edges moves it to the voice's real end and start). A shorter pause
# inside a sentence is how Bryan talks and stays (Bryan, 2026-10-21: cutting every pause over
# 0.35 s down to 0.12 s "just cuts out words mid-speech"). No piece is left shorter than MIN_PIECE
# (it stays joined to its neighbour instead).
BREATH_AFTER, BREATH_BEFORE = 0.16, 0.10
LONG_PAUSE = 1.0
CLAUSE_END = re.compile(r"[.!?;:,。！？；：，、…]['\"”’)\]]*$")
MIN_PIECE = 0.25
# Hesitation sounds dropped when Whisper wrote them as a word of their own, with a gap
# either side (FILLER_GAP seconds at least). Only true hesitations: "ah" can start a real
# phrase ("ah, that's why") and 啊 is often a meaningful particle (好啊, 是啊), so those two
# go only when they stand alone with a long pause either side (LONE_GAP). Nothing else is
# ever dropped from inside a sentence by code, except a stutter (STUTTER_GAP below).
FILLERS = {"um", "umm", "uh", "uhh", "erm", "uhm", "嗯", "呃"}
MAYBE_FILLERS = {"ah", "啊"}
FILLER_GAP = 0.06
LONE_GAP = 0.3
# A stutter: the same word (or two or three) said twice in a row, the second within this
# many seconds: the first copy goes. Words people double on purpose stay.
STUTTER_GAP = 0.6
KEEP_DOUBLES = {"that", "had", "is", "very", "really", "so", "no", "yes", "yeah", "bye", "much", "more", "many",
                "too", "far", "long", "again", "over", "round", "around", "go", "well", "ha", "haha", "hey", "now",
                "knock", "come", "wait", "okay", "ok", "right", "there", "here"}
# A join inside a sentence that dropped real words: put back without asking when the words
# cut are this short (seconds), else Claude is asked once whether the sentence still reads.
JOIN_RESTORE = 4.0

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
        "key_words": {"type": "array", "items": {"type": "string"}},
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
                    "style": {"type": "string", "enum": ["card", "callout", "scene"]},
                    "title": {"type": "string"},
                    "lines": {"type": "array", "items": {"type": "string"}},
                    "scene": {"type": "string"},
                    "look": {"type": "string", "enum": ["cutaway", "panel"]},
                    "role": {"type": "string", "enum": ["explain", "gap", "hook"]},
                    "why": {"type": "string"},
                },
                "required": ["clip_id", "at", "duration", "style", "title", "lines", "scene", "look", "role", "why"],
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
                               "rule": {"type": "string"}, "same_rule": {"type": "integer"},
                               "animations": {"type": "string", "enum": ["fewer", "more", "none"]}},
                "required": ["note_id", "how", "kind", "rule", "same_rule", "animations"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["cuts", "hook_text", "key_words", "music", "graphics", "missing_shots", "summary", "notes_addressed"],
    "additionalProperties": False,
}

_SYSTEM = """You are the editor for Bryan's Instagram Reels. You decide exactly which stretches \
of footage make the final 9:16 video. Follow Bryan's playbook:

<playbook>
{playbook}
</playbook>

Rules for the cut list:
- Work from the approved plan's segments, but you choose the exact in and out points, and you may \
drop whole sentences (a repeated take, a false start, a tangent, dead air) and reorder when the story \
needs it. Use only the clips listed.
- Never break a sentence. Every cut starts where a sentence (or a clause that stands on its own) \
starts and ends where one ends; never drop words from inside a sentence you keep, not even one: \
a viewer hears every kept sentence whole, or the sentence stops making sense. If a sentence is too \
long, drop all of it or keep all of it. Hesitations (um, uh) and stutters ("the the") are removed \
by Studio automatically: don't cut around them.
- Complete, coherent sentences beat any pacing target. The pacing numbers in the playbook and the \
house style (a cut every N seconds, shot lengths, pauses) apply to shot changes and B-roll, never to \
cutting into speech: never cut into a sentence, or between the words of one, to hit them.
- Cut between phrases, never inside a word: put cut points in the gaps between the timestamped \
words, never in the middle of one. Keep the payoff and anything that qualifies a claim.
- The ending: the last cut with speech ends on a complete sentence (the payoff, or the call to \
action when Bryan says one). Studio then holds a short beat on that last shot and adds the Urban \
Makers end card (the mark, the account's handle and the post's call to action) while the music fades \
out, so don't end on a stray word and don't plan an outro of your own. Keep cards, callouts and drawn \
animations clear of the last 2 seconds of speech.
{pause_rule}- Cuts play in the order you list them. Each is clip_id plus start and end seconds within that clip.
- hook_text: at most 6 words shown on screen for the first ~2.5 seconds, or "" for none. It \
should add to the spoken hook, not repeat it word for word. Sentence case, never all capitals.
- Titles (hook_text, and a card's or callout's title) pick out at most one word (Studio sets it apart \
in the look's accent: brass, or a serif italic): mark that word with *asterisks*, e.g. "Floors *stripped*", \
"The *screed* goes first". Only a real key word (the material, the thing being explained, the result), never filler; leave a title \
unmarked when no word stands out. Never mark anything else (lines, why, summary, a scene's JSON).
- key_words: the few words of Bryan's speech the captions pick out for emphasis (in the accent), as he says \
them in the transcript, one word each (e.g. "screed", "overlay", "fifteen"): the material, the thing \
being explained, the number of something; never filler. Use them sparingly: at most one per 4 \
seconds of the Reel and usually fewer; [] when nothing stands out, and always [] for Chinese speech.
- music: describe the bed for this post (mood, a short catalogue search phrase, tempo). \
volume_db is the music's level: -16 is the usual bed (Studio levels Bryan's voice and keeps the \
music well under it while he talks, a little higher between phrases); go lower (-18 to -22) for a \
quieter bed, higher (-12) only for music-led posts.
- {music_rule}
- graphics: 1 to 3 animated B-roll inserts (rendered with HyperFrames) that show what Bryan is \
saying while he says it. Anchor each to the moment the words it illustrates are spoken: clip_id \
and "at" in that clip's own seconds (it must fall inside one of your cuts), plus duration (1.5 to \
3.5 s). style "card" shows a headline and up to 3 short points (good for steps, lists, numbers or \
the key takeaway: a point with one number, as "Older HDB 30A", is drawn as a bar; "Now: ..." and "The plan: \
..." as labelled pairs); style "callout" is a panel over the top third of the footage for one \
number, price, duration or key term (title is the big text, lines one short label). Use Bryan's \
own words and facts; never invent numbers. Title at most 5 words, each line at most 4 words. \
Leave the first 2.5 seconds clear for the hook. Use none if nothing in the speech suits a graphic. \
For a card or callout, scene and why are "", look "cutaway" and role "explain".
{scene_rule}{picture_rule}{voiceover_rule}- missing_shots: footage you wished you had while cutting this post, specific enough to film \
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
same_rule 0. animations: "fewer" when a note of Bryan's asks for fewer drawn animations or diagrams in \
general ("too many animations"), "more" when it asks for more of them in general ("more diagrams"), \
otherwise "none" (also "none" for a note about one particular animation, and for Studio's own fixes).
- The playbook's "Editing rules Studio learned" are problems caught in earlier drafts: never \
repeat them."""


# Drawn inserts (style "scene"): a scene from the motion library (motion.py) over the footage.
_SCENE_RULE = """- Drawn animations (style "scene"): a short animation from Studio's motion library laid over \
the footage while Bryan keeps talking. Use one only where it helps the viewer understand: (1) Bryan \
explains something visual the footage doesn't show: a build-up of layers, a dimension, a process in \
steps, a comparison, a layout; or (2) to cover a gap: a moment that is weak or flagged for a retake, a \
jump that needs bridging, or a shot that was never filmed (a fix that says "Draw it for now"). Never as \
decoration, and never just to repeat his words as text (that is a card).
  - Limits: at most {cap} per Reel and about one per 15 seconds of the Reel, none in the first 2 \
seconds (the hook stays on Bryan; only when the hook itself is best shown as a drawing use role "hook" \
and hook_text ""), each 2.5 to 6 seconds, not overlapping a card or callout. Anchor it like a card \
(clip_id, "at" when he starts the words it shows, duration to when he finishes them); Studio snaps \
it to his words.
  - scene: the scene as JSON text: one object with "type" and that type's fields. Types:
{types}
    No "end" type, no pictures, no prices, discounts or promotions, no place names. Use his own \
words and facts; never invent numbers or measurements he didn't say. Keep the text short: it is \
glanced at while he talks. freeform only when no other type fits. Icons: {icons}.
  - look: "cutaway" (the default: the drawing fills the screen, his voice and the captions carry \
on) or "panel" (a card over the top of the picture that keeps him in view; only when he is framed \
low in the shot: Studio makes it a cutaway if it would cover his face).
  - role: "explain" (shows what he says), "gap" (stands in for a missing or weak shot) or "hook". \
title: the scene's title or main words, for Bryan's list. lines: []. why: a few plain words on why \
it is drawn, for Bryan ("the screed isn't filmed", "shows the step he describes").
  - Examples. He says "an overlay goes on top of your old tiles: adhesive, then the new tiles":
    {{"type": "layers", "title": "An overlay builds on top", "layers": [{{"label": "New tiles", "size": \
"medium", "tone": "tile", "tag": "New"}}, {{"label": "Adhesive", "size": "thin", "tone": "adhesive", "tag": \
"New"}}, {{"label": "Existing tiles", "note": "Stay in place", "size": "medium", "tone": "tile"}}, {{"label": \
"Screed", "size": "thick", "tone": "screed"}}]}}
    He says "the kitchen sits about 15 mm higher than the living room":
    {{"type": "measure", "title": "The step at the doorway", "value": "15 mm", "label": "The kitchen \
floor sits higher", "kind": "step", "left": "Kitchen", "right": "Living room"}}
    He says "first we hack the old tiles, then we pour a screed, then we tile":
    {{"type": "steps", "title": "Levelling the floor", "items": ["Hack off the old tiles", "Pour a \
level screed", "Lay the new tiles"], "style": "numbered"}}
  - Re-planning a draft: keep an animation that works exactly as it was (the same scene JSON), so \
it isn't drawn again.{lean}
"""


def _scene_rule() -> str:
    """The prompt's part on drawn inserts, or "" when they're off."""
    import learning
    cap = learning.insert_cap(settings.EDIT_SCENES_MAX)
    if not settings.EDIT_SCENES or cap <= 0:
        return ""
    types = "\n".join(f"    - {k}: {v}" for k, v in motion.SCENE_TYPES.items() if k != "end")
    lean = learning.insert_guidance()
    return _SCENE_RULE.format(cap=cap, types=types, icons=", ".join(sorted(motion.ICONS)),
                              lean=f"\n  - {lean}" if lean else "")


def _schema(pictures_on: bool = False) -> dict:
    """The edit plan's schema; without the scene style when drawn inserts are off, with the picture style (and
    each graphic's image brief) when Studio can make pictures (pictures.py)."""
    schema = EDL_SCHEMA
    if not _scene_rule():
        schema = json.loads(json.dumps(EDL_SCHEMA))
        schema["properties"]["graphics"]["items"]["properties"]["style"]["enum"] = ["card", "callout"]
    if pictures_on:
        import pictures
        schema = pictures.schema(schema)
    return schema


# ── Context for Claude ────────────────────────────────────────────────────────

def _words(clip: dict) -> list[dict]:
    path = Path(clip["clip_dir"]) / "transcript.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8")).get("words", [])


def _clip_block(clip: dict, plan_segments: list[dict], label: str = "") -> str:
    """One clip for the plan: its words with their times, its planned segments, its quality; label: what it
    is in a voice-over (voiceover.labels)."""
    words = _words(clip)
    lines = [f"Clip {clip['id']}: {clip['title']} ({clip['duration']:.1f}s, {clip['orientation']}, "
             f"{'has audio' if clip['has_audio'] else 'no audio'}" + (f"; {label}" if label else "") + ")",
             f"  summary: {clip['summary']}"]
    try:
        q = json.loads(clip.get("quality") or "{}")
    except (TypeError, ValueError):
        q = {}
    if q.get("verdict") in ("check", "retake") and q.get("flags"):
        lines.append(f"  quality: {q['verdict']} ({'; '.join(f.get('text', '') for f in q['flags'][:4])})")
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
    cards = [g for g in graphics if "start" in g and g.get("style") not in ("scene", "picture")]
    shown = ("\nPrevious graphics: " + "; ".join(
        f"{g['start']:.1f}-{g['start'] + g['duration']:.1f}s {g['style']} \"{_with_mark(g['title'], g.get('italic', ''))}\""
        + (f" ({' / '.join(g['lines'])})" if g.get("lines") else "") for g in cards) if cards else "")
    scenes = [g for g in graphics if g.get("style") == "scene" and "start" in g]
    if scenes:
        shown += "\nPrevious drawn animations (clip, at, duration, look, role, why, scene JSON):\n" + "\n".join(
            f"  {g['start']:.1f}-{g['start'] + g['duration']:.1f}s in the draft: clip {g['clip_id']} at {g['at']:.2f}s, "
            f"{g['duration']:.1f}s, {g.get('look', 'cutaway')}, {g.get('role', 'explain')}, why: {g.get('why') or '-'}, "
            f"scene: {json.dumps(g['scene'], ensure_ascii=False)}" for g in scenes)
    pics = [g for g in graphics if g.get("style") == "picture" and "start" in g]
    if pics:
        shown += "\nPrevious pictures (AI, marked Concept; clip, at, duration, look, role, why, title, image brief):\n" + "\n".join(
            f"  {g['start']:.1f}-{g['start'] + g['duration']:.1f}s in the draft: clip {g['clip_id']} at {g['at']:.2f}s, "
            f"{g['duration']:.1f}s, {g.get('look', 'cutaway')}, {g.get('role', 'explain')}, why: {g.get('why') or '-'}, "
            f"title: {g.get('title', '')}, image: {g.get('image', '')}" for g in pics)
    import voiceover
    picture = voiceover.describe(previous.get("pip"))
    if picture:
        shown += "\nPrevious picture (a voice-over; the cut list above is his voice): " + picture
    head = ("\n\nThis is a revision of draft v{v}. Previous cut list:\n".format(v=production["version"])
            + "\n".join(placed)
            + f"\nPrevious hook text: {_with_mark(previous.get('hook_text') or '', previous.get('hook_italic', '')) or '(none)'}"
            + (f"\nPrevious key words (in italic in the captions): {', '.join(previous['key_words'])}"
               if previous.get("key_words") else "") + shown
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
                + ("\nA fix that starts \"Draw it for now\" stands in for a shot nobody filmed: make it with a drawn "
                   "animation (style \"scene\", role \"gap\") at that moment, drawing what it says."
                   if any(f["body"].startswith("Draw it for now") for f in fixes) else "")
                + ("\n\nBryan's notes on the version before, already done in this draft (keep them done):\n"
                   + "\n".join(f"  - {r['body']}" for r in done) if done else ""))
    notes = conn.execute(
        "SELECT * FROM feedback WHERE target = 'production' AND target_id = ? AND status = 'open' ORDER BY id",
        (production["id"],)).fetchall()
    note_lines = [f"  note {n['id']}" + (f" at {n['at_seconds']:.1f}s" if n["at_seconds"] is not None else "")
                  + f": {n['body']}" for n in notes]
    # Studio's read of that draft rides along: Bryan wants its fixes done on every revision without
    # adding them one by one (self_fix.read_notes).
    import self_fix
    read = self_fix.read_notes(production, [dict(n) for n in notes])
    read_lines = [f"  note {f['id']}" + (f" at {f['at_seconds']:.1f}s" if f.get("at_seconds") is not None else "")
                  + f": {f['body']}" for f in read]
    return (head + "\n\nBryan's notes on that draft:\n" + ("\n".join(note_lines) or "  (none)")
            + ("\n\nStudio's read of that draft also found these. Bryan wants them done too: fix every one you can "
               "from the footage (address them in notes_addressed like his notes, kind \"editing\", rule \"\"):\n"
               + "\n".join(read_lines)
               + ("\nA note that starts \"Draw it for now\" stands in for a shot nobody filmed: make it with a drawn "
                  "animation (style \"scene\", role \"gap\") at that moment, drawing what it says."
                  if any(f["body"].startswith("Draw it for now") for f in read) else "") if read else ""))


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


def _fresh_block(conn, production: dict) -> str:
    """Start over (restyle.FRESH): the edit is planned from the footage as if it were the first draft.
    No previous cut list, graphics or key words (they described the old edit, and cutting a cut again
    and again was what Bryan suspected), no Studio's read of it; Bryan's open notes still go in, as
    notes without their times (those were on the old draft)."""
    notes = conn.execute(
        "SELECT * FROM feedback WHERE target = 'production' AND target_id = ? AND status = 'open' ORDER BY id",
        (production["id"],)).fetchall()
    text = ("\n\nThis is a fresh edit: Bryan asked to start over. Plan it from the footage above as if it were the "
            "first draft; earlier versions' cuts are deliberately left out.")
    if notes:
        text += ("\n\nBryan's notes on an earlier version (their times were on that version, so go by what they say; "
                 "address each in notes_addressed):\n" + "\n".join(f"  note {n['id']}: {n['body']}" for n in notes))
    return text


def plan_edit(conn, production: dict, clips: dict[int, dict], client=None, fixes: list[dict] | None = None,
              ref: str | None = None, fresh: bool = False) -> dict:
    """Claude's edit plan (EDL). fixes: a round of Studio's own fixes instead of Bryan's notes;
    ref: where its cost is recorded (Studio's own rounds are counted apart); fresh: start over
    (_fresh_block), nothing of the previous edit in the prompt. A voice-over (voiceover.mode: a studio
    talking head and site B-roll) is planned with the B-roll before the words it shows, and comes back
    split: edl["cuts"] his voice, edl["broll"] what's laid over it (voiceover.split_plan)."""
    import pictures
    import voiceover
    plan = json.loads(production["plan"] or "{}")
    segments = plan.get("segments", [])
    picture_rule = pictures.rule(pictures.post_type(conn, production))
    vo = voiceover.mode(clips) if settings.EDIT_VOICEOVER else {"on": False, "why": "voice-overs are turned off"}
    names = voiceover.labels(vo) if vo["on"] else {}
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
            + "\n\n".join(_clip_block(c, segments, names.get(c["id"], "")) for c in clips.values())
            + (_fresh_block(conn, production) if fresh else _revision_block(conn, production, fixes)))
    pause_rule = ("- Pauses are tightened automatically after you plan: a long pause between sentences or clauses "
                  "is cut down to a natural breath, lone \"um\"s and stutters go (jump cuts), and so does silence at the "
                  "start and end of a cut with speech. Short pauses inside a sentence stay, as Bryan says it. So keep "
                  "whole phrases in one cut rather than splitting a cut just to skip a pause, and give a deliberate "
                  "silent moment (a reveal, B-roll) a cut of its own.\n"
                  if settings.EDIT_JUMP_CUTS else "")
    client = client or anthropic.Anthropic(api_key=ANTHROPIC_API_KEY or None)
    import llm
    response = llm.final_message(
        client, model=CLAUDE_MODEL, max_tokens=32000,
        betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        output_config={"effort": "high", "format": {"type": "json_schema", "schema": voiceover.schema(_schema(bool(picture_rule)))
                                                                       if vo["on"] else _schema(bool(picture_rule))}},
        system=_SYSTEM.replace("{playbook}", playbook.text()).replace("{music_rule}", music_rule)
        .replace("{pause_rule}", pause_rule).replace("{scene_rule}", _scene_rule()).replace("{picture_rule}", picture_rule)
        .replace("{voiceover_rule}", voiceover.plan_rule(vo) + "\n" if vo["on"] else "") + "\n\n" + wording.guide(),
        messages=[{"role": "user", "content": user}],
    )
    import usage
    usage.record("edit", ref or f"production:{production['id']}", response)
    if response.stop_reason == "refusal":
        raise RuntimeError("Claude declined to plan this edit")
    if response.stop_reason == "max_tokens":
        raise RuntimeError("Claude's edit plan was cut off (max_tokens)")
    edl = json.loads(next(b.text for b in response.content if b.type == "text"))
    edl = tidy_plan(polish_plan(edl, ref or f"production:{production['id']}", client))
    picture = edl.pop("picture", None)
    if vo["on"]:
        voiceover.split_plan(edl, vo, picture)
        edl["voiceover"] = {k: vo[k] for k in ("on", "why", "studio", "site", "broll", "narrated")}
        edl["voiceover"]["footage"] = {cid: {k: f.get(k) for k in ("kind", "where", "why", "score")}
                                       for cid, f in vo["footage"].items()}
    return edl


def polish_plan(edl: dict, ref: str, client=None) -> dict:
    """The plan's on-screen words (the hook text, each card's or callout's title and lines, a drawn
    scene's words) through wording.polish: the safe fixes, and one rewrite of whatever still reads
    machine-written (its cost counted with the edit). The italic *marks* are still in; tidy_plan
    takes them out after."""
    fields = {"hook_text": edl.get("hook_text") or ""}
    scenes = {}
    for i, g in enumerate(edl.get("graphics") or []):
        if g.get("style") == "scene":
            raw = g.get("scene")
            try:
                scene = json.loads(raw) if isinstance(raw, str) else raw
            except ValueError:
                continue
            if isinstance(scene, dict):
                scenes[i] = scene
                fields.update({f"g{i}.scene.{k}": v for k, v in wording.scene_texts(scene).items()})
        elif g.get("style") == "picture":  # its title and brief aren't on screen (only the Concept mark is)
            continue
        else:
            fields[f"g{i}.title"] = g.get("title") or ""
            fields.update({f"g{i}.lines.{n}": x for n, x in enumerate(g.get("lines") or []) if isinstance(x, str)})
    out, _ = wording.polish(fields, "edit", ref, client)
    edl["hook_text"] = out["hook_text"]
    graphics = edl.get("graphics") or []
    changed = set()
    for key, text in out.items():
        if key == "hook_text" or text == fields.get(key):
            continue
        head, part, rest = key.split(".", 2) if key.count(".") >= 2 else (*key.split(".", 1), "")
        g = graphics[int(head[1:])]
        if part == "title":
            g["title"] = text
        elif part == "lines":
            g["lines"][int(rest)] = text
        elif part == "scene":
            wording.set_scene_text(scenes[int(head[1:])], rest, text)
            changed.add(int(head[1:]))
    for i, scene in scenes.items():
        if i in changed:
            graphics[i]["scene"] = json.dumps(scene, ensure_ascii=False) if isinstance(graphics[i].get("scene"), str) else scene
    return edl


# ── Cut points ────────────────────────────────────────────────────────────────

def snap(cut: dict, clip: dict) -> dict | None:
    """Move cut points out of words into the gaps around them, with a little air,
    without reaching into the neighbouring word. Where the clip's sound is known, the
    end moves on to where the voice really stops and the start back to where it really
    starts (voice_edges), never into the next or previous word. None if what's left is
    too short."""
    words, duration = _words(clip), clip["duration"] or 0
    start, end = max(0.0, cut["start"]), min(duration, cut["end"])
    for w in words:
        if w["start"] < start < w["end"]:
            start = w["start"]
        if w["start"] < end < w["end"]:
            end = w["end"]
    before = [w for w in words if w["end"] <= start]
    after = [w for w in words if w["start"] >= end]
    first = next((w for w in words if start <= w["start"] < end), None)
    last = next((w for w in reversed(words) if start < w["end"] <= end), None)
    start, end = (max(start - HEAD_PAD, before[-1]["end"] if before else 0.0, 0.0),
                  min(end + TAIL_PAD, after[0]["start"] if after else duration, duration))
    v = voice_edges.voice(clip) if words else None
    if v and first:
        start = voice_edges.start_point(v, first["start"], start, prev_end=before[-1]["end"] if before else None)
    if v and last:
        end = min(voice_edges.end_point(v, last["end"], end, next_start=after[0]["start"] if after else None,
                                        limit=duration), duration)
    if end - start < MIN_CUT:
        return None
    return {**cut, "start": round(start, 3), "end": round(end, 3)}


def _filler_core(word: str) -> str:
    return re.sub(r"[\s.,!?;:…，。！？、~\-]+", "", word.lower())


def _is_filler(word: str) -> bool:
    """A true hesitation sound (um, uh, 嗯, 呃), whatever its gaps."""
    return _filler_core(word) in FILLERS


def _hesitation(word: str, gap_before: float, gap_after: float) -> bool:
    """Dropped as a hesitation: a true one with a little gap either side, or an "ah" / 啊 that
    stands alone with a long pause either side (else it's part of what's said)."""
    core = _filler_core(word)
    if core in FILLERS:
        return gap_before >= FILLER_GAP and gap_after >= FILLER_GAP
    return core in MAYBE_FILLERS and gap_before >= LONE_GAP and gap_after >= LONE_GAP


def _same_word(word: str) -> str:
    return re.sub(r"[^\w']+", "", word.lower())


def stutters(words: list[dict]) -> set[int]:
    """The words (their indices) that are the first copy of a stutter: the same word, or two or
    three, said twice in a row with at most STUTTER_GAP between ("excites excites", "the the",
    "of the of the"). Never Chinese (看看 is one word said twice), never a word doubled on purpose
    (KEEP_DOUBLES: "very very"), never across the end of a sentence ("No. No.")."""
    norms = [_same_word(w["word"]) for w in words]
    drop, i, n = set(), 0, len(words)
    while i < n:
        for size in (3, 2, 1):
            if i + 2 * size > n:
                continue
            first, second = norms[i:i + size], norms[i + size:i + 2 * size]
            if not all(first) or first != second or any(_CJK.search(x) for x in first):
                continue
            if size == 1 and first[0] in KEEP_DOUBLES:
                continue
            if any(SENTENCE_END.search(w["word"].strip()) for w in words[i:i + size]):
                continue
            if words[i + size]["start"] - words[i + size - 1]["end"] > STUTTER_GAP:
                continue
            drop.update(range(i, i + size))
            i += size
            break
        else:
            i += 1
    return drop


def droppable(words: list[dict]) -> set[int]:
    """The words (indices, in a clip's word list) Studio's code may drop: hesitations and the
    first copy of a stutter. Everything else Bryan says inside a kept sentence stays."""
    out = set(stutters(words))
    for i, w in enumerate(words):
        before = w["start"] - words[i - 1]["end"] if i else 9.0
        after = words[i + 1]["start"] - w["end"] if i + 1 < len(words) else 9.0
        if _hesitation(w["word"], before, after):
            out.add(i)
    return out


def pause_to_cut(word: str, gap: float, max_pause: float | None = None) -> bool:
    """Is the pause after this word one the jump cuts shorten? Only between sentences or clauses
    (the word ends with a full stop, comma and the like) when it's longer than max_pause, or any
    pause longer than LONG_PAUSE. A short pause inside a sentence stays: it's how Bryan talks."""
    max_pause = settings.EDIT_MAX_PAUSE if max_pause is None else max_pause
    if gap > max(LONG_PAUSE, max_pause):
        return True
    return gap > max_pause and bool(CLAUSE_END.search(str(word).strip()))


def tighten(cuts: list[dict], clips: dict[int, dict], max_pause: float | None = None) -> list[dict]:
    """Jump cuts: split each cut into pieces that skip its long pauses between sentences.

    Inside a cut, a pause between sentences or clauses longer than max_pause (pause_to_cut: after
    a full stop or comma, or any pause over LONG_PAUSE), a lone "um" or 嗯 with gaps around it, and
    the first copy of a stutter are cut down to a natural breath: a little air after the last word
    and before the next, moved to where the voice really stops and starts when the clip's sound is
    known (voice_edges). A short pause inside a sentence is never cut. Silence longer than
    max_pause at the start or end of the cut is trimmed to the same air the cut points get. Cuts
    are only ever made in the gaps between words, a piece is never shorter than MIN_PIECE (it stays
    joined to its neighbour instead), and a cut with no speech (B-roll) is left as it is. Each
    piece keeps its cut's "part" (its place in Claude's plan)."""
    max_pause = settings.EDIT_MAX_PAUSE if max_pause is None else max_pause
    out = []
    for n, c in enumerate(cuts):
        part = c.get("part", n)
        clip = clips[c["clip_id"]]
        words = sorted((w for w in _words(clip)
                        if w["start"] >= c["start"] - 0.01 and w["end"] <= c["end"] + 0.01),
                       key=lambda w: w["start"])
        stutter = stutters(words)
        keep, dropped = [], []
        for i, w in enumerate(words):
            before = w["start"] - (words[i - 1]["end"] if i else c["start"])
            after = (words[i + 1]["start"] if i + 1 < len(words) else c["end"]) - w["end"]
            if i in stutter or _hesitation(w["word"], before, after):
                dropped.append(w)
            else:
                keep.append(w)
        if not keep:  # no speech, or nothing but "um": leave it as planned
            out.append({**c, "part": part})
            continue
        v = voice_edges.voice(clip)
        start, end = c["start"], c["end"]
        prior = [d for d in dropped if d["end"] <= keep[0]["start"]]
        if keep[0]["start"] - start > max_pause or prior:
            base = max(start, keep[0]["start"] - HEAD_PAD, max((d["end"] for d in prior), default=start))
            start = voice_edges.start_point(v, keep[0]["start"], base, prev_end=prior[-1]["end"] if prior else None,
                                            limit=c["start"])
        later = [d for d in dropped if d["start"] >= keep[-1]["end"]]
        if end - keep[-1]["end"] > max_pause or later:
            base = min(end, keep[-1]["end"] + TAIL_PAD, min((d["start"] for d in later), default=end))
            end = voice_edges.end_point(v, keep[-1]["end"], base, next_start=later[0]["start"] if later else None,
                                        limit=c["end"])
        spans = [[start, None]]
        for a, b in zip(keep, keep[1:]):
            between = [d for d in dropped if d["start"] >= a["end"] and d["end"] <= b["start"]]
            if not between and not pause_to_cut(a["word"], b["start"] - a["end"], max_pause):
                continue
            nxt = between[0]["start"] if between else b["start"]
            prv = between[-1]["end"] if between else a["end"]
            cut_from = voice_edges.end_point(v, a["end"], min(a["end"] + BREATH_AFTER, nxt), next_start=nxt)
            cut_to = voice_edges.start_point(v, b["start"], max(b["start"] - BREATH_BEFORE, prv), prev_end=prv)
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
# Talking, people chain clauses with "and" or "so" where writing has a full stop, and Whisper writes
# none: "...the width of beam that is left there and once it's hacked...". Stopping before such a word
# ends a whole clause, not half a sentence (Bryan heard v9 of #12 end on "left there" as complete,
# 2026-10-21, while Studio flagged it). Only after a clause of a few words that doesn't end on a word
# that needs the next one ("the", "of", "is"...).
CLAUSE_JOINERS = {"and", "so", "but", "then"}
CLAUSE_MIN_WORDS = 5
_NEEDS_MORE = {"a", "an", "the", "of", "to", "in", "on", "at", "for", "with", "from", "by", "into", "my", "your",
               "our", "their", "his", "her", "its", "this", "that", "these", "those", "is", "are", "was", "were",
               "be", "been", "will", "would", "can", "could", "should", "have", "has", "had", "do", "does", "and",
               "or", "but", "so", "if", "because", "when", "very", "more", "most", "about", "like", "than"}


def _bare(word: str) -> str:
    return re.sub(r"[^\w']+", "", str(word or "").lower())


def ends_clause(words: list[dict], k: int) -> bool:
    """words[k] may end what's said: a full stop, or (no punctuation) the end of a clause the next
    word chains on with "and", "so", "but" or "then"."""
    if not 0 <= k < len(words):
        return False
    if SENTENCE_END.search(words[k]["word"].strip()):
        return True
    if k + 1 >= len(words) or _bare(words[k + 1]["word"]) not in CLAUSE_JOINERS or _bare(words[k]["word"]) in _NEEDS_MORE:
        return False
    run = 0
    for w in reversed(words[:k + 1]):
        if SENTENCE_END.search(w["word"].strip()):
            break
        run += 1
    return run >= CLAUSE_MIN_WORDS
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
    all_words = _words(clips[c["clip_id"]])
    v = voice_edges.voice(clips[c["clip_id"]])
    words = _inside(c, all_words)
    ends = [k for k, w in enumerate(words) if SENTENCE_END.search(w["word"].strip())]
    if ends and ends[-1] < len(words) - 1:
        last, tail = words[ends[-1]], words[ends[-1] + 1:]
        new_end = voice_edges.end_point(v, last["end"], min(last["end"] + TAIL_PAD, tail[0]["start"]),
                                        next_start=tail[0]["start"], limit=c["end"])
        if tail[-1]["end"] - tail[0]["start"] <= MAX_DANGLING and new_end - c["start"] >= MIN_CUT:
            c["end"] = round(new_end, 3)
            stray = " ".join(w["word"].strip() for w in tail)
            done.append(f"ended on the last full sentence (cut the stray \"{stray}\" at the end)")
            words = words[:ends[-1] + 1]
    if not SENTENCE_END.search(words[-1]["word"].strip()):
        # Still stopping mid-sentence: the rest of the sentence comes back when it's short.
        rest = _rest_of_sentence(all_words, words[-1])
        k = len(rest) and all_words.index(rest[-1]) + 1
        others = [o["start"] for o in cuts if o is not c and o["clip_id"] == c["clip_id"] and o["start"] >= c["end"] - 0.001]
        if rest and rest[-1]["end"] - words[-1]["end"] <= JOIN_RESTORE and not any(t < rest[-1]["end"] for t in others):
            nxt = all_words[k] if k < len(all_words) else None
            dur = clips[c["clip_id"]].get("duration") or rest[-1]["end"] + TAIL_PAD
            limit = min([dur] + others)
            c["end"] = round(voice_edges.end_point(v, rest[-1]["end"], min(rest[-1]["end"] + TAIL_PAD,
                                                                           nxt["start"] if nxt else limit, limit),
                                                   next_start=nxt["start"] if nxt else None, limit=limit), 3)
            done.append(f"finished the last sentence (kept \"{_quoted(rest)}\" at the end)")
            words = words + rest
    if spoken[-1] == len(cuts) - 1 and c["end"] - words[-1]["end"] > EDGE_SILENCE:
        nxt = next((w for w in all_words if w["start"] >= words[-1]["end"] and w is not words[-1]), None)
        c["end"] = voice_edges.end_point(v, words[-1]["end"], round(words[-1]["end"] + TAIL_PAD, 3),
                                         next_start=nxt["start"] if nxt else None, limit=c["end"])
        done.append("trimmed the silence after the last word")
    # The start: the first cut that speaks, when it opens partway through a sentence.
    c = cuts[spoken[0]]
    all_words = _words(clips[c["clip_id"]])
    v = voice_edges.voice(clips[c["clip_id"]])
    words = _inside(c, all_words)
    before = [w for w in all_words if w["end"] <= words[0]["start"] + 0.001 and w is not words[0]]
    mid = before and words[0]["start"] - before[-1]["end"] < 1.0 and not SENTENCE_END.search(before[-1]["word"].strip())
    if mid:
        k = next((k for k, w in enumerate(words) if SENTENCE_END.search(w["word"].strip())), None)
        if k is not None and k + 1 < len(words) and words[k]["end"] - words[0]["start"] <= MAX_DANGLING:
            new_start = voice_edges.start_point(v, words[k + 1]["start"],
                                                max(words[k]["end"], words[k + 1]["start"] - HEAD_PAD),
                                                prev_end=words[k]["end"])
            if c["end"] - new_start >= MIN_CUT:
                c["start"] = round(new_start, 3)
                stray = " ".join(w["word"].strip() for w in words[:k + 1])
                done.append(f"opened on a full sentence (cut the end of the one before, \"{stray}\")")
                words = words[k + 1:]
    if spoken[0] == 0 and words[0]["start"] - c["start"] > EDGE_SILENCE:
        c["start"] = voice_edges.start_point(v, words[0]["start"], round(max(c["start"], words[0]["start"] - HEAD_PAD), 3),
                                             limit=c["start"])
        done.append("trimmed the silence before the first word")
    return cuts, done


# ── Whole sentences ───────────────────────────────────────────────────────────

def _rest_of_sentence(words: list[dict], last: dict, most: int = 12) -> list[dict]:
    """The words after `last` up to the end of its sentence (at most `most` of them), or [] when
    the sentence doesn't end by then."""
    try:
        k = next(i for i, w in enumerate(words) if w is last)
    except StopIteration:
        return []
    rest = []
    for w in words[k + 1:k + 1 + most]:
        rest.append(w)
        if SENTENCE_END.search(w["word"].strip()):
            return rest
    return []


def _start_of_sentence(words: list[dict], first: dict, most: int = 12) -> list[dict]:
    """The words before `first` back to the start of its sentence (at most `most`), or []."""
    try:
        k = next(i for i, w in enumerate(words) if w is first)
    except StopIteration:
        return []
    head = []
    for i in range(k - 1, max(k - 1 - most, -1), -1):
        if SENTENCE_END.search(words[i]["word"].strip()):
            return head
        head.insert(0, words[i])
    return head if k - len(head) == 0 else []


def whole_ends(cuts: list[dict], clips: dict[int, dict]) -> tuple[list[dict], list[dict]]:
    """The cut never stops a sentence at a join to another shot (join_guard checks joins inside one
    clip): a cut with speech that ends partway through a sentence, where the next cut doesn't carry
    on with the next word, gets the rest of that sentence back when it's short (JOIN_RESTORE seconds,
    a dozen words at most), and one that opens partway through a sentence the cut before didn't say
    gets its start back the same way. Never into another cut of the same clip; the draft's first
    start and last end are sentence_guard's. Returns the cuts and notes for the draft's checks."""
    cuts, notes = [dict(c) for c in cuts], []
    spoken = [i for i, c in enumerate(cuts) if c["clip_id"] in clips and len(_inside(c, _words(clips[c["clip_id"]]))) >= 3]
    for i in spoken:
        c = cuts[i]
        clip = clips[c["clip_id"]]
        words = _words(clip)
        inside = _inside(c, words)
        v = voice_edges.voice(clip)
        dur = clip.get("duration") or words[-1]["end"]
        nxt_cut = cuts[i + 1] if i + 1 < len(cuts) else None
        prv_cut = cuts[i - 1] if i else None
        same = [o for o in cuts if o is not c and o["clip_id"] == c["clip_id"]]
        if i != spoken[-1] and not SENTENCE_END.search(inside[-1]["word"].strip()):
            rest = _rest_of_sentence(words, inside[-1])
            carries = nxt_cut and nxt_cut["clip_id"] == c["clip_id"] and rest and _inside(nxt_cut, words)[:1] == rest[:1]
            limit = min([dur] + [o["start"] for o in same if o["start"] >= c["end"] - 0.001])
            if rest and not carries and rest[-1]["end"] - inside[-1]["end"] <= JOIN_RESTORE and rest[-1]["end"] < limit:
                k = words.index(rest[-1]) + 1
                after = words[k]["start"] if k < len(words) else None
                c["end"] = round(voice_edges.end_point(v, rest[-1]["end"], min(rest[-1]["end"] + TAIL_PAD, after or limit, limit),
                                                       next_start=after, limit=limit), 3)
                notes.append(_restore_note(c["clip_id"], rest))
        if i != spoken[0]:
            first = inside[0]
            k = words.index(first)
            mid = k and first["start"] - words[k - 1]["end"] < 1.0 and not SENTENCE_END.search(words[k - 1]["word"].strip())
            head = _start_of_sentence(words, first) if mid else []
            said = prv_cut and prv_cut["clip_id"] == c["clip_id"] and head and _inside(prv_cut, words)[-1:] == head[-1:]
            floor = max([0.0] + [o["end"] for o in same if o["end"] <= c["start"] + 0.001])
            if head and not said and first["start"] - head[0]["start"] <= JOIN_RESTORE and head[0]["start"] > floor:
                j = words.index(head[0])
                before = words[j - 1]["end"] if j else None
                c["start"] = round(voice_edges.start_point(v, head[0]["start"], max(head[0]["start"] - HEAD_PAD, before or floor, floor),
                                                           prev_end=before, limit=floor), 3)
                notes.append(_restore_note(c["clip_id"], head))
    return cuts, notes


def _sentence_ids(words: list[dict]) -> list[int]:
    ids, n = [], 0
    for w in words:
        ids.append(n)
        if SENTENCE_END.search(w["word"].strip()):
            n += 1
    return ids


def _restart(cut_words: list[str], next_words: list[str]) -> bool:
    """A false start Claude was right to cut: the words cut begin like the words kept after them
    ("we use the" before "we always use the adhesive")."""
    if not cut_words or not next_words or len(cut_words) > 8:
        return False
    if cut_words[0] == next_words[0]:
        return True
    head = set(next_words[:len(cut_words) + 3])
    return len(set(cut_words) & head) / len(set(cut_words)) >= 0.6


def _broken_join(a: dict, b: dict, clips: dict[int, dict]) -> dict | None:
    """The join between cut a and the cut after it, b, when it sits inside one sentence and
    dropped real words (anything but hesitations, stutters and silence): {clip_id, words (the
    clip's), last, first (indices of the kept words either side), cut (the indices between)}."""
    if a["clip_id"] != b["clip_id"] or a["clip_id"] not in clips:
        return None
    words = _words(clips[a["clip_id"]])
    kept_a = [k for k, w in enumerate(words) if w["start"] >= a["start"] - 0.01 and w["end"] <= a["end"] + 0.01]
    kept_b = [k for k, w in enumerate(words) if w["start"] >= b["start"] - 0.01 and w["end"] <= b["end"] + 0.01]
    if not kept_a or not kept_b or kept_b[0] <= kept_a[-1] + 1:
        return None
    last, first = kept_a[-1], kept_b[0]
    ids = _sentence_ids(words)
    if ids[last] != ids[first]:
        return None
    free = droppable(words)
    real = [k for k in range(last + 1, first) if k not in free]
    if not real:
        return None
    if _restart([_same_word(words[k]["word"]) for k in real],
                [_same_word(words[k]["word"]) for k in kept_b[:len(real) + 3]]):
        return None
    return {"clip_id": a["clip_id"], "words": words, "last": last, "first": first, "cut": list(range(last + 1, first)),
            "real": real}


def _quoted(words: list[dict]) -> str:
    text = " ".join(w["word"].strip() for w in words)
    parts = text.split()
    return " ".join(parts[:8]) + (" ..." if len(parts) > 8 else "")


def _restore_note(clip_id: int, words: list[dict]) -> dict:
    """A note for the draft's checks, timed once the cut is final (_timed_notes)."""
    return {"clip_id": clip_id, "t": words[0]["start"],
            "did": f"kept \"{_quoted(words)}\" at {{at}} so the sentence makes sense"}


_JOIN_SYSTEM = """You check a Reel's cut for broken sentences. At each join below, the words before and \
after it were kept from one sentence of Bryan's, and the numbered words between were cut. A viewer hears \
the kept words once, straight through. Say whether the kept sentence still reads complete and coherent, \
with the same meaning Bryan gave it. If it doesn't, list the numbers of the cut words that must come back \
for it to read right: as few as that takes, in whole phrases. A false start or a repeated take may stay \
cut. Never restore a word only because it was cut; restore what the sentence needs.{notes}"""

_JOIN_SCHEMA = {
    "type": "object",
    "properties": {"joins": {"type": "array", "items": {
        "type": "object",
        "properties": {"id": {"type": "integer"}, "reads_complete": {"type": "boolean"},
                       "restore": {"type": "array", "items": {"type": "integer"}}},
        "required": ["id", "reads_complete", "restore"], "additionalProperties": False}}},
    "required": ["joins"],
    "additionalProperties": False,
}


def _ask_joins(asks: list[dict], client=None, ref: str = "", notes: list[str] = ()) -> dict[int, list[int]]:
    """One Claude call (text only, effort low) over the joins: {join id: [indices in the clip's
    words to restore]}. notes: what this edit was asked to change (Bryan's notes, or Studio's own
    fixes); a cut they asked for stays cut. Its cost is counted with the edit. {} when Claude
    can't be asked."""
    if not asks or not (client or ANTHROPIC_API_KEY):
        return {}
    import llm
    import usage
    blocks = []
    for n, j in enumerate(asks, 1):
        words, ids = j["words"], _sentence_ids(j["words"])
        sentence = [k for k in range(len(words)) if ids[k] == ids[j["last"]]]
        before = " ".join(words[k]["word"].strip() for k in sentence if k <= j["last"])
        after = " ".join(words[k]["word"].strip() for k in sentence if k >= j["first"])
        cut = " ".join(f"[{m}] {words[k]['word'].strip()}" for m, k in enumerate(j["cut"], 1))
        blocks.append(f"join {n}\n  kept before: {before}\n  cut: {cut}\n  kept after: {after}")
    client = client or anthropic.Anthropic(api_key=ANTHROPIC_API_KEY or None)
    response = llm.final_message(
        client, model=CLAUDE_MODEL, max_tokens=4000,
        betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": _JOIN_SCHEMA}},
        system=_JOIN_SYSTEM.replace("{notes}", (
            "\n\nThis edit was asked to make these changes; words they ask to cut stay cut:\n"
            + "\n".join(f"- {n}" for n in notes)) if notes else ""),
        messages=[{"role": "user", "content": "\n\n".join(blocks)}])
    usage.record("edit", ref, response)
    if response.stop_reason in ("refusal", "max_tokens"):
        raise RuntimeError(f"the sentence check stopped ({response.stop_reason})")
    answer = json.loads(next(b.text for b in response.content if b.type == "text"))
    out = {}
    for item in answer.get("joins") or []:
        if not isinstance(item, dict) or not isinstance(item.get("id"), int) or not 1 <= item["id"] <= len(asks):
            continue
        j = asks[item["id"] - 1]
        picks = sorted({j["cut"][m - 1] for m in item.get("restore") or [] if isinstance(m, int) and 1 <= m <= len(j["cut"])})
        if picks and not item.get("reads_complete"):
            out[item["id"] - 1] = picks
    return out


def _runs(indices: list[int]) -> list[list[int]]:
    runs = []
    for k in indices:
        if runs and k == runs[-1][-1] + 1:
            runs[-1].append(k)
        else:
            runs.append([k])
    return runs


def _join_up(cuts: list[dict], clips: dict[int, dict]) -> list[dict]:
    """Neighbouring cuts of one clip with nothing Bryan said between them (at most a second of
    silence, hesitations or stutters) become one cut."""
    out = [dict(cuts[0])] if cuts else []
    for c in cuts[1:]:
        p = out[-1]
        if p["clip_id"] == c["clip_id"] and c["start"] >= p["start"] and c["clip_id"] in clips:
            words = _words(clips[c["clip_id"]])
            free = droppable(words)
            between = [k for k, w in enumerate(words) if w["start"] >= p["end"] - 0.01 and w["end"] <= c["start"] + 0.01]
            if c["start"] <= p["end"] + 0.01 or (c["start"] - p["end"] <= 1.0 and all(k in free for k in between)):
                p["end"] = max(p["end"], c["end"])
                continue
        out.append(dict(c))
    return out


def join_guard(cuts: list[dict], clips: dict[int, dict], client=None, ref: str = "",
               asked: list[str] = ()) -> tuple[list[dict], list[dict]]:
    """The cut never breaks a sentence. Where two cuts meet inside one sentence and the words
    between them were real words (not hesitations, stutters or silence), they come back: when
    the words cut last JOIN_RESTORE seconds or less, the two cuts simply become one; longer ones
    go to Claude once (all of them in one call) to say whether the sentence still reads complete,
    and the words it says are needed come back. asked: the changes this edit was asked for
    (Bryan's notes, Studio's own fixes): then every such join goes to Claude with them, so a cut
    Bryan asked for ("drop the 'as you can see'") stays cut. Returns the cuts and notes for the
    draft's checks ({clip_id, t, did}; _timed_notes puts the draft time in)."""
    if not cuts:
        return cuts, []
    out, notes, asks = [dict(cuts[0])], [], []
    for c in cuts[1:]:
        j = _broken_join(out[-1], c, clips)
        if j and not asked and j["words"][j["first"]]["start"] - j["words"][j["last"]]["end"] <= JOIN_RESTORE:
            out[-1] = {**out[-1], "end": c["end"]}
            notes.append(_restore_note(j["clip_id"], [j["words"][k] for k in j["real"]]))
            continue
        if j:
            asks.append({**j, "after": len(out) - 1})
        out.append(dict(c))
    if asks:
        try:
            answers = _ask_joins(asks, client, ref, list(asked))
            if client or ANTHROPIC_API_KEY:
                # A join Claude judged reads complete stays as it is: Studio's listen-back
                # (final_check.py) doesn't bring its words back (say, a cut Bryan asked for).
                for n, j in enumerate(asks):
                    if n not in answers:
                        out[j["after"] + 1]["sentence_ok"] = True
        except Exception as e:  # the cut as planned beats no draft
            log.warning("The sentence check on the joins didn't work (%s); the cut stays as planned", e)
            answers = {}
        for n in sorted(answers, key=lambda n: asks[n]["after"], reverse=True):
            j = asks[n]
            clip = clips[j["clip_id"]]
            new = []
            for run in _runs(answers[n]):
                piece = snap({**out[j["after"]], "start": j["words"][run[0]]["start"], "end": j["words"][run[-1]]["end"],
                              "why": "kept so the sentence makes sense"}, clip)
                if piece:
                    new.append(piece)
                    notes.append(_restore_note(j["clip_id"], [j["words"][k] for k in run]))
            out[j["after"] + 1:j["after"] + 1] = new
        out = _join_up(out, clips)
    return out, notes


def _timed_notes(notes: list[dict], cuts: list[dict]) -> list[str]:
    """join_guard's notes with the moment on the draft's clock (m:ss)."""
    out = []
    for note in notes:
        offset, at = 0.0, None
        for c in cuts:
            if c["clip_id"] == note["clip_id"] and c["start"] - 0.2 <= note["t"] <= c["end"]:
                at = offset + max(0.0, note["t"] - c["start"])
                break
            offset += c["end"] - c["start"]
        at = 0.0 if at is None else at
        out.append(note["did"].replace("{at}", f"{int(at // 60)}:{int(at % 60):02d}"))
    return out


def timeline(cuts: list[dict], clips: dict[int, dict], sound: bool = False) -> list[dict]:
    """Every kept word on the draft's own clock (with its clip and its start there, "clip_id" and
    "src"). A word the draft corrects (a piece's "say", attach_corrections: a mis-heard "MCSC"
    becomes "MCST") reads as corrected, and a word a correction folds into the one before ("") is
    left out, so the captions, Studio's read, Review and the Resolve captions all show the fix.
    sound: each word also gets "sound_end", where its voice really stops (voice_edges; never past
    its piece), when the clip's sound is known."""
    out, offset = [], 0.0
    for c in cuts:
        words = _words(clips[c["clip_id"]])
        v = voice_edges.voice(clips[c["clip_id"]]) if sound and words else None
        say = c.get("say") or {}
        for k, w in enumerate(words):
            if w["start"] >= c["start"] - 0.01 and w["end"] <= c["end"] + 0.01:
                text = say.get(f"{w['start']:.2f}", w["word"])
                if not str(text).strip():
                    if out:  # folded into the word before: it lasts to this word's end
                        out[-1]["end"] = offset + w["end"] - c["start"]
                        if v and "sound_end" in out[-1]:
                            nxt = words[k + 1]["start"] if k + 1 < len(words) else None
                            real = voice_edges.end_point(v, w["end"], w["end"], next_start=nxt, limit=c["end"])
                            out[-1]["sound_end"] = offset + max(w["end"], real - voice_edges.BREATH_OUT) - c["start"]
                    continue
                out.append({"word": text, "start": offset + w["start"] - c["start"],
                            "end": offset + w["end"] - c["start"], "clip_id": c["clip_id"], "src": w["start"]})
                if v:
                    nxt = words[k + 1]["start"] if k + 1 < len(words) else None
                    real = voice_edges.end_point(v, w["end"], w["end"], next_start=nxt, limit=c["end"])
                    out[-1]["sound_end"] = offset + max(w["end"], real - voice_edges.BREATH_OUT) - c["start"]
        offset += c["end"] - c["start"]
    return out


def attach_corrections(cuts: list[dict], corrections: list[dict] | None) -> list[dict]:
    """The draft's caption corrections (edl["corrections"]: [{clip_id, at, from, to}], at = the
    word's start in its clip) put on the pieces that hold those words, as each piece's "say"
    ({"12.34": "MCST"}), which timeline() reads. Keyed to the clip, not the draft's clock, so they
    hold through any re-cut, a re-render and the Resolve project."""
    by_clip: dict[int, dict] = {}
    for f in corrections or []:
        try:
            by_clip.setdefault(int(f["clip_id"]), {})[f"{float(f['at']):.2f}"] = str(f.get("to") or "")
        except (KeyError, TypeError, ValueError):
            continue
    out = []
    for c in cuts:
        c = {k: v for k, v in c.items() if k != "say"}
        say = {t: to for t, to in by_clip.get(c["clip_id"], {}).items() if c["start"] - 0.02 <= float(t) <= c["end"] + 0.01}
        if say:
            c["say"] = say
        out.append(c)
    return out


def place_graphics(graphics: list[dict], cuts: list[dict], total: float) -> list[dict]:
    """Move each graphic from its source anchor (clip + seconds) onto the draft's
    clock; drop ones whose moment was cut, that would cover the hook, or overlap."""
    placed, offset_of = [], []
    offset = 0.0
    for c in cuts:
        offset_of.append(offset)
        offset += c["end"] - c["start"]
    for g in (g for g in graphics if g.get("style") not in ("scene", "picture")):  # inserts: place_scenes, pictures.place
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
        # A capital to open the draft and each new sentence (a stutter's first copy, "The the", may
        # have taken the capital with it).
        if text and (not out or SENTENCE_END.search(chunks[i - 1][-1]["word"].strip())):
            text = text[0].upper() + text[1:]
        if end - start > 0.05 and text:
            out.append({"text": text, "start": round(start, 3), "end": round(end, 3)})
    return out


def _plain(text: str) -> set[str]:
    return {w for w in re.findall(r"[\w']+", text.lower()) if len(w) > 1}


def clear_of_cards(chunks: list[dict], graphics: list[dict]) -> tuple[list[dict], int]:
    """While a full-screen card (or a drawn insert) is up, its words are on screen already: a
    caption saying the same words is dropped, so the card is never doubled up by the caption
    line. Returns the captions and how many were dropped."""
    cards = [g for g in graphics if g.get("style") in ("card", "scene") and "start" in g]
    if not cards:
        return chunks, 0
    out, dropped = [], 0
    for c in chunks:
        card = next((g for g in cards if c["start"] < g["start"] + g["duration"] and c["end"] > g["start"]), None)
        words = _plain(c["text"])
        shown = (motion.scene_text(card.get("scene")) if card and card.get("style") == "scene"
                 else " ".join([card["title"], *card.get("lines", [])]) if card else "")
        if card and words and words <= _plain(shown):
            dropped += 1
            continue
        out.append(c)
    return out, dropped


# ── Drawn inserts (motion library scenes over the footage) ────────────────────
# The opening stays on Bryan (SCENE_CLEAR, or the hook text's time when there is one); each
# insert lasts SCENE_MIN to SCENE_LONGEST seconds, timed to his words; about one per SCENE_EVERY
# seconds of draft, at most settings.EDIT_SCENES_MAX (fewer when Bryan asked for fewer), with at
# least SCENE_GAP seconds of footage between two of them and clear of the cards and callouts.
SCENE_MIN, SCENE_LONGEST = 2.5, 6.0
SCENE_CLEAR, HOOK_SECONDS = 2.0, 2.6
SCENE_EVERY, SCENE_GAP = 15.0, 4.0
# A panel (motion.css .pcard) covers y 190-1150 of the frame: it may go over the footage only
# when no face is in that band.
PANEL_TOP, PANEL_BOTTOM = 190, 1150
INSERTS = RENDERS / "inserts"     # rendered inserts, by their spec's hash (scene_key)
INSERT_KEEP_DAYS = 45


def scene_cap(total: float) -> int:
    """How many drawn inserts a draft of this length may have."""
    import learning
    if not settings.EDIT_SCENES:
        return 0
    cap = learning.insert_cap(settings.EDIT_SCENES_MAX)
    return min(cap, max(1, int(total // SCENE_EVERY))) if cap > 0 else 0


def _no_pictures(scene: dict) -> dict:
    """Inserts carry no pictures (the footage is already there): a scene's image is dropped."""
    scene = {k: v for k, v in scene.items() if k not in ("image", "duration")}
    for side in ("before", "after"):
        if isinstance(scene.get(side), dict):
            scene[side] = {k: v for k, v in scene[side].items() if k != "image"}
    return scene


def _snap_start(t: float, words: list[dict]) -> float:
    """An insert starts just before the word nearest its anchor (within 0.6 s)."""
    near = [w for w in words if abs(w["start"] - t) <= 0.6]
    if not near:
        return t
    return max(0.0, min(near, key=lambda w: abs(w["start"] - t))["start"] - 0.1)


def _snap_end(start: float, want: float, words: list[dict]) -> float:
    """An insert ends just after a word, as close to start + want as its limits allow (never much
    shorter than asked: a drawing needs its time to build); the end of a phrase (a comma or full
    stop) is preferred when it's within 0.8 s."""
    lo, hi = start + SCENE_MIN - TAIL_PAD, start + SCENE_LONGEST - TAIL_PAD
    fits = [w for w in words if lo <= w["end"] <= hi]
    if not fits:
        return min(max(start + want, start + SCENE_MIN), start + SCENE_LONGEST)
    target = start + want - TAIL_PAD
    best = min([w for w in fits if w["end"] >= target - 0.2] or fits, key=lambda w: abs(w["end"] - target))
    phrase = [w for w in fits if re.search(r"[.,!?;:。，！？]$", w["word"].strip()) and abs(w["end"] - target) <= 0.8]
    if phrase:
        best = min(phrase, key=lambda w: abs(w["end"] - target))
    # Where the word's voice really stops (timeline(sound=True)), when that's later than Whisper's end.
    return max(best["end"] + TAIL_PAD, best.get("sound_end", 0.0) + voice_edges.BREATH_OUT)


def face_boxes(cuts: list[dict], clips: dict[int, dict], start: float, end: float) -> list[tuple[float, float, float]]:
    """Where a face is on screen between start and end (draft seconds), as framed in the draft:
    (centre x, centre y, width) in the Reel's pixels, from framing.py's face finder (its
    faces.json, so footage already looked at isn't looked at again)."""
    out, offset = [], 0.0
    for c in cuts:
        length = c["end"] - c["start"]
        a, b = max(start, offset), min(end, offset + length)
        if b > a:
            clip = clips[c["clip_id"]]
            sw, sh = framing._dims(clip)
            seen = framing.faces(clip, c["start"] + a - offset, c["start"] + b - offset, every=2)
            w, h, x, y = framing.exact_window(c, sw, sh)
            for f in seen.values():
                if f:
                    cx, cy, size = (f[0] * sw - x) / w * W, (f[1] * sh - y) / h * H, f[2] * sw / w * W
                    if -size < cx < W + size and -size < cy < H + size:
                        out.append((round(cx), round(cy), round(size)))
        offset += length
    return out


def choose_look(asked: str, cuts: list[dict], clips: dict[int, dict], start: float, end: float) -> tuple[str, str]:
    """cutaway (the default) or panel: a panel only when it covers no face (a face's box runs
    from about 0.75 of its width above its centre to 1.1 below). Returns (look, why changed)."""
    if asked != "panel":
        return "cutaway", ""
    try:
        faces = face_boxes(cuts, clips, start, end)
    except Exception as e:  # no OpenCV, an unreadable clip: the safe choice
        log.warning("Couldn't find the face for a panel: %s", e)
        return "cutaway", "Studio couldn't tell where your face was"
    covered = [f for f in faces if f[1] - 0.75 * f[2] < PANEL_BOTTOM + 20 and f[1] + 1.1 * f[2] > PANEL_TOP - 20]
    if covered:
        return "cutaway", "a panel would have covered your face"
    return "panel", ""


def place_scenes(graphics: list[dict], cuts: list[dict], clips: dict[int, dict], total: float, words: list[dict],
                 hook_text: str, others: list[dict] = ()) -> tuple[list[dict], list[str]]:
    """Claude's drawn inserts on the draft's clock, checked: its scene must pass the motion
    library's checks (motion.validate: lengths, no prices, an insert is one scene and not an end
    card), and the limits above hold (timing, how many, clear of the hook and of `others`, the
    cards and callouts already placed). Returns the placed inserts (in time order) and, in plain
    words, why any was left out."""
    wanted = [g for g in graphics if g.get("style") == "scene"]
    if not wanted:
        return [], []
    cap = scene_cap(total)
    if cap <= 0:
        return [], [f"left out {len(wanted)} drawn animation{'s' if len(wanted) != 1 else ''} (they're turned off)"]
    offset_of, offset = [], 0.0
    for c in cuts:
        offset_of.append(offset)
        offset += c["end"] - c["start"]
    hook = bool((hook_text or "").strip())
    placed, notes = [], []
    for g in wanted:
        name = (g.get("title") or "an animation").strip()

        def drop(why):
            notes.append(f"left out the animation \"{name}\": {why}")
        raw = g.get("scene")
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except ValueError:
                drop("its drawing couldn't be read")
                continue
        if not isinstance(raw, dict) or not raw.get("type"):
            drop("it had no drawing")
            continue
        raw = _no_pictures(raw)
        start = next((offset_of[i] + g["at"] - c["start"] for i, c in enumerate(cuts)
                      if c["clip_id"] == g.get("clip_id") and c["start"] <= g.get("at", -1) < c["end"]), None)
        if start is None:  # its moment fell in a pause that was cut: start it as the talking resumes
            start = next((offset_of[i] for i, c in enumerate(cuts)
                          if c["clip_id"] == g.get("clip_id") and g.get("at", -9) <= c["start"] <= g.get("at", -9) + 1.5), None)
        if start is None:
            drop("the moment it was for was cut")
            continue
        role = g.get("role") if g.get("role") in ("explain", "gap", "hook") else "explain"
        earliest = 0.0 if role == "hook" and not hook else HOOK_SECONDS if hook else SCENE_CLEAR
        start = round(max(_snap_start(start, words), earliest), 3)
        want = min(max(float(g.get("duration") or SCENE_MIN), SCENE_MIN), SCENE_LONGEST)
        end = round(min(_snap_end(start, want, words), total - 0.2), 3)
        if end - start < SCENE_MIN:
            drop("there wasn't room for it before the end")
            continue
        if len(placed) >= cap:
            drop(f"Studio keeps to {cap} drawn animation{'s' if cap != 1 else ''} in a Reel this long")
            continue
        if any(start < o["start"] + o["duration"] + 0.3 and end > o["start"] - 0.3 for o in others):
            drop("it would have clashed with a card")
            continue
        if any(start < p["start"] + p["duration"] + SCENE_GAP and end > p["start"] - SCENE_GAP for p in placed):
            drop("it came too soon after another animation")
            continue
        try:
            spec = motion.validate({"scenes": [{**raw, "duration": round(end - start, 2)}]}, insert=True)
        except motion.SpecError as e:
            drop("; ".join(e.problems[:2]))
            continue
        look, changed = choose_look(g.get("look", "cutaway"), cuts, clips, start, end)
        scene = spec["scenes"][0]
        placed.append({"clip_id": g.get("clip_id"), "at": g.get("at"), "style": "scene", "start": start,
                       "duration": round(end - start, 3), "title": (scene.get("title") or scene.get("text") or
                                                                   scene.get("value") or name).replace("\n", " ")[:80],
                       "lines": [], "type": scene["type"], "scene": raw, "look": look, "role": role,
                       "why": (g.get("why") or "").strip()[:120], **({"look_changed": changed} if changed else {})})
        if changed:
            notes.append(f"showed \"{name}\" full screen instead of as a panel ({changed})")
    return sorted(placed, key=lambda p: p["start"]), notes


def scene_key(g: dict) -> str:
    """The cache key of a drawn insert: its scene, look and length, the Reel's size and the library (and
    Site Notes' dark panel, g["titles"] "site")."""
    import hashlib
    blob = json.dumps({"scene": g["scene"], "look": g["look"], "duration": round(g["duration"], 3),
                       "frame": [W, H, FPS], "library": motion.library_key(),
                       **({"theme": "site"} if g.get("titles") == "site" else {})}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:24]


def render_scenes(scenes: list[dict]) -> tuple[list[dict], list[str], dict]:
    """Each insert as a transparent WebM at the Reel's size and frame rate (motion.render_insert:
    checked for fonts, the safe area, overlaps and cut-off text first), cached in INSERTS by
    scene_key, so a fix round or re-render that keeps an insert doesn't draw it again. Returns the
    inserts that rendered (each with its "file"), why any didn't, and {drawn, reused}."""
    INSERTS.mkdir(parents=True, exist_ok=True)
    done, notes, tally = [], [], {"drawn": 0, "reused": 0}
    for g in scenes:
        key = scene_key(g)
        out = INSERTS / f"{key}.webm"
        if out.exists() and out.stat().st_size > 0:
            out.touch()
            tally["reused"] += 1
        else:
            part = INSERTS / f"{key}.part.webm"
            try:
                motion.render_insert(g["scene"], g["duration"], part, style=g["look"],
                                     theme="site" if g.get("titles") == "site" else "paper")
                part.replace(out)
                tally["drawn"] += 1
            except Exception as e:  # a draft without the insert beats no draft
                part.unlink(missing_ok=True)
                log.warning("Insert \"%s\" didn't render: %s: %s", g.get("title"), type(e).__name__, e)
                why = str(e)
                why = ("it didn't fit the screen" if "check" in why else "it didn't render")
                notes.append(f"left out the animation \"{g.get('title')}\": {why}")
                continue
        done.append({**g, "file": key})
    cutoff = __import__("time").time() - INSERT_KEEP_DAYS * 86400
    for old in INSERTS.glob("*.webm"):  # inserts no draft has used for a while
        try:
            if old.stat().st_mtime < cutoff:
                old.unlink()
        except OSError:
            pass
    return done, notes, tally


def insert_file(g: dict) -> Path:
    return INSERTS / f"{g.get('file') or scene_key(g)}.webm"


def scenes_layer(scenes: list[dict], total: float, out: Path) -> int:
    """The drawn inserts and the pictures as one transparent ProRes 4444 layer the length of the draft, for
    the DaVinci Resolve project (an insert no longer in the cache is drawn again; a picture is rendered again
    from the kept picture). Returns how many it holds."""
    import pictures
    missing = [g for g in scenes if not insert_file(g).exists() and g.get("style") != "picture"]
    if missing:
        render_scenes(missing)
    for g in scenes:
        if g.get("style") == "picture":
            pictures.ensure_file(g)  # from the kept picture: never drawn again
    ready = [g for g in scenes if insert_file(g).exists()]
    if not ready:
        return 0
    args = [settings.FFMPEG, "-y", "-v", "error", "-f", "lavfi",
            "-i", f"color=c=black@0.0:s={W}x{H}:r={FPS}:d={total:.3f},format=yuva444p"]
    graph, below = [], "[0:v]"
    for k, g in enumerate(ready):
        args += ["-c:v", "libvpx-vp9", "-i", str(insert_file(g))]
        graph.append(f"[{k + 1}:v]setpts=PTS-STARTPTS+{g['start']:.3f}/TB[s{k}];"
                     f"{below}[s{k}]overlay=0:0:format=auto:eof_action=pass[b{k}]")
        below = f"[b{k}]"
    graph.append(f"{below}format=yuva444p10le[v]")
    extract._run(args + ["-filter_complex", ";".join(graph), "-map", "[v]", "-c:v", "prores_ks", "-profile:v", "4444",
                         "-pix_fmt", "yuva444p10le", "-r", str(FPS), "-t", f"{total:.3f}", str(out)])
    return len(ready)


def scene_summary(scenes: list[dict]) -> str:
    """One sentence for Bryan on what was drawn rather than filmed."""
    if not scenes:
        return ""
    parts = [f"{g['start']:.0f}-{g['start'] + g['duration']:.0f} s: {g['title']}"
             + (f" ({g['why']})" if g.get("why") else "") for g in scenes]
    n = len(scenes)
    return (f"Studio drew {'an animation' if n == 1 else f'{n} animations'} over the footage, not filmed "
            f"({'; '.join(parts)}).")


# ── Rendering ─────────────────────────────────────────────────────────────────

RATE = 48000
JOIN_FADE = 0.012  # seconds of fade either side of a join in the sound, so it can't click
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


def _frame_plan(cuts: list[dict]) -> tuple[list[int], list[list[int]], list[int], list[float]]:
    """How render_cut lays the pieces out: frames per piece (whole frames, rounded from the
    running total), runs of pieces read together ([first, last]), where each piece starts in its
    run (frames) and in its clip (seconds). The sound of piece k is source[k] to
    source[k] + frames[k] / FPS in its clip."""
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
    return frames, groups, at, source


def render_cut(cuts: list[dict], clips: dict[int, dict], out: Path) -> float:
    """All pieces in one ffmpeg run from the originals: frame-exact, one encode.

    Each piece is a whole number of frames, rounded from the running total so the
    picture never drifts from the captions' clock. Pieces of one clip that follow
    each other closely (a stretch with its pauses cut, punch-ins, reframes) are read
    from the file once, dropping the frames in between, which keeps the memory a
    4K clip needs to one read per stretch. Pieces that carry straight on from the
    one before take their sound in one unbroken piece; elsewhere the sound fades
    for a few milliseconds either side of the join, so jump cuts don't click."""
    frames, groups, at, source = _frame_plan(cuts)

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


# The fonts are bundled (OFL, latin) and loaded from local files, so a render never depends on the
# machine's fonts or the network. STUDIO_CAPTION_FONT picks the captions' font: family and its faces
# (a woff2 file and the weights it covers).
FONTS = render.RENDER_DIR / "captions" / "fonts"
CAPTION_FONTS = {
    "inter": ("Inter", ((FONTS / "Inter-latin-500.woff2", "500"), (FONTS / "Inter-latin-600.woff2", "600"))),
    "figtree": ("Figtree", ((render.RENDER_DIR / "carousel" / "fonts" / "Figtree-latin.woff2", "100 1000"),)),
    "nunito": ("Nunito", ((FONTS / "Nunito-latin.woff2", "100 1000"),)),
    "dm_sans": ("DM Sans", ((FONTS / "DMSans-latin.woff2", "100 1000"),)),
    "outfit": ("Outfit", ((FONTS / "Outfit-latin.woff2", "100 1000"),)),
    "archivo": ("Archivo", tuple((FONTS / f"Archivo-latin-{w}.woff2", w) for w in ("700", "750", "800"))),
}
# The serif of the gallery titles and of the captions' key word: (family, regular, italic).
TITLE_FONTS = {
    "instrument_serif": ("Instrument Serif", FONTS / "InstrumentSerif-latin-400.woff2",
                         FONTS / "InstrumentSerif-latin-400-italic.woff2"),
}
# How the hook, callouts, cards, captions and end card look (STUDIO_TITLE_STYLE). site (Site Notes, the
# default): Archivo only, words on tight dark labels, the key word in brass, cards as a dark panel over
# the footage, the end card over the last frame. gallery: a serif title with one italic word, a
# hairline, no box, a soft shade from the top while it's up; captions glow, with a key word in the
# serif italic. classic: the first look (boxed titles, the amber bar, captions on a rounded box).
TITLE_STYLES = {"site": {"font": "archivo"}, "gallery": {"serif": "instrument_serif"}, "classic": {}}


def caption_font(name: str | None = None) -> tuple[str, tuple]:
    """(family, faces) of the caption font: the setting's, or Inter."""
    key = _font_key(name)
    return CAPTION_FONTS[key]


def _font_key(name: str | None = None) -> str:
    key = (name or settings.CAPTION_FONT or "inter").lower().replace("-", "_").replace(" ", "_")
    if key not in CAPTION_FONTS:
        log.warning("Unknown caption font %r; using Inter (choose from %s)", key, ", ".join(CAPTION_FONTS))
        key = "inter"
    return key


def title_style(name: str | None = None) -> str:
    """The title style: the setting's, or site."""
    key = (name or settings.TITLE_STYLE or "site").lower().strip()
    if key not in TITLE_STYLES:
        log.warning("Unknown title style %r; using site (choose from %s)", key, ", ".join(TITLE_STYLES))
        key = "site"
    return key


def look_now() -> dict:
    """The look a new draft is made in, kept in its edit plan (edl["look"]). Site Notes has one font
    for everything (Archivo), whatever STUDIO_CAPTION_FONT says."""
    titles = title_style()
    return {"titles": titles, "caption_font": TITLE_STYLES[titles].get("font") or _font_key()}


def draft_look(edl: dict) -> dict:
    """The look a draft was made in, so a re-render or its Resolve project doesn't change it. Drafts
    from before the look was kept were classic, in the caption font of the day (Figtree, the old
    default, unless another was set)."""
    look = edl.get("look") or {}
    if look.get("titles"):
        titles = title_style(look["titles"])
        return {"titles": titles, "caption_font": TITLE_STYLES[titles].get("font") or _font_key(look.get("caption_font"))}
    old = settings.CAPTION_FONT if settings.CAPTION_FONT in CAPTION_FONTS and settings.CAPTION_FONT != "inter" else "figtree"
    return {"titles": "classic", "caption_font": old}


# ── The italic word ───────────────────────────────────────────────────────────
# In a title (the hook, a callout, a card) Claude marks the one word to set in italic with
# *asterisks*. The plan keeps the plain text (so summaries, Review, the read and Resolve never see an
# asterisk) and the word apart: edl["hook_italic"], a graphic's "italic". In captions, the plan's
# key_words: the first caption saying one gets it in the serif italic (key_word_captions).
_MARK = re.compile(r"\*+([^*]+?)\*+")
_CJK = re.compile(r"[　-〿㐀-鿿豈-﫿＀-￯]")
_EDGES = re.compile(r"^(\W*)(.*?)(\W*)$", re.S)


def unmark(text) -> str:
    """Text without the italic marks."""
    return re.sub(r"\s{2,}", " ", str(text or "").replace("*", "")).strip()


def _core(word: str) -> str:
    """A word without the punctuation around it."""
    return _EDGES.match(word).group(2)


def title_parts(text: str) -> tuple[str, str]:
    """A title's plain text and its italic word: the one marked with *asterisks*, else (a title of
    two or three words) its last word when it's a word of at least three letters (not "mm" or "15"),
    else none. Never in Chinese (no serif Chinese font is bundled)."""
    text = str(text or "")
    plain = unmark(text)
    if _CJK.search(plain):
        return plain, ""
    marked = _MARK.search(text)
    if marked:
        inside = marked.group(1).split()
        return plain, _core(inside[-1]) if inside else ""
    words = plain.split()
    last = _core(words[-1]) if words else ""
    if 2 <= len(words) <= 3 and last.isalpha() and len(last) >= 3:
        return plain, last
    return plain, ""


def _with_mark(text: str, word: str) -> str:
    """The title as Claude wrote it (its italic word between asterisks), for a re-plan."""
    if not word:
        return text
    tokens = text.split(" ")
    for i, t in enumerate(tokens):
        pre, core, post = _EDGES.match(t).groups()
        if core.lower() == word.lower():
            tokens[i] = f"{pre}*{core}*{post}"
            break
    return " ".join(tokens)


def tidy_plan(edl: dict) -> dict:
    """Claude's edit plan with its italic marks taken out of the text and kept apart, and its key
    words cleaned (one word each). Asterisks anywhere else (a summary, a line, a drawing) go too."""
    edl["hook_text"], edl["hook_italic"] = title_parts(edl.get("hook_text", ""))
    for g in edl.get("graphics") or []:
        if g.get("style") in ("scene", "picture"):
            g["title"] = unmark(g.get("title"))
            if g.get("image"):
                g["image"] = unmark(g["image"])
            if isinstance(g.get("scene"), str):
                g["scene"] = g["scene"].replace("*", "")
            elif isinstance(g.get("scene"), dict):
                g["scene"] = json.loads(json.dumps(g["scene"], ensure_ascii=False).replace("*", ""))
        else:
            g["title"], g["italic"] = title_parts(g.get("title", ""))
        g["lines"] = [unmark(x) for x in g.get("lines") or []]
        if "why" in g:
            g["why"] = unmark(g["why"])
    edl["summary"] = unmark(edl.get("summary", ""))
    for m in edl.get("missing_shots") or []:
        m.update({k: unmark(m.get(k)) for k in ("shot", "why")})
    for n in edl.get("notes_addressed") or []:
        n.update({k: unmark(n.get(k)) for k in ("how", "rule")})
    keys = []
    for k in edl.get("key_words") or []:
        parts = unmark(k).split()
        word = _core(parts[-1]) if parts else ""
        if word and not _CJK.search(word) and word.lower() not in (x.lower() for x in keys):
            keys.append(word)
    edl["key_words"] = keys
    return edl


# At most one caption key word per KEY_EVERY seconds of speech, and two italic captions at least
# KEY_GAP seconds apart.
KEY_EVERY, KEY_GAP = 4.0, 2.0


def key_word_captions(chunks: list[dict], key_words: list[str]) -> list[dict]:
    """Marks the captions' key words (the gallery look sets them in the serif italic, Site Notes in
    brass): the first
    caption that says one of the plan's key words gets it, on that word (its "key": the word's
    place in the caption). At most one per caption, each key word once, none in Chinese captions;
    nothing else is ever italic."""
    out = [{k: v for k, v in c.items() if k != "key"} for c in chunks]
    if not out or not key_words:
        return out
    speech = max(out[-1]["end"] - out[0]["start"], 0.0)
    want = [k.lower() for k in key_words][:max(1, int(speech // KEY_EVERY))]
    used, last = set(), -1e9
    for c in out:
        if _CJK.search(c["text"]) or c["start"] - last < KEY_GAP:
            continue
        for i, t in enumerate(c["text"].split(" ")):
            word = _core(t).lower()
            if word in want and word not in used:
                c["key"], last = i, c["start"]
                used.add(word)
                break
    return out


# Bright footage behind the captions (a white wall, sunlit rubble): the gallery look's glow alone
# isn't enough there, so a caption over it gets a stronger glow and a soft shade that hugs its words
# (class "lit"). The caption band is measured on the cut before the captions go on (LIT_FPS frames a
# second, small and grey); brighter than LIT_LUMA (0-255) is bright.
LIT_LUMA, LIT_FPS = 150, 5


def bright_spans(video: Path) -> list[list[float]]:
    """When the footage behind the caption band is bright: [[start, end], ...] in the video's seconds.
    [] when it can't be measured (a draft with the plain glow beats no draft)."""
    import subprocess
    w, h = 54, 10
    try:
        raw = subprocess.run([settings.FFMPEG, "-v", "error", "-i", str(video), "-an", "-vf",
                              f"fps={LIT_FPS},crop={W}:220:0:{CAPTION_TOP - 20},scale={w}:{h}:flags=area,format=gray",
                              "-f", "rawvideo", "-"], capture_output=True, timeout=600, check=True).stdout
    except Exception as e:
        log.warning("Couldn't measure the footage under the captions: %s", e)
        return []
    spans, step = [], 1 / LIT_FPS
    for i in range(len(raw) // (w * h)):
        frame = raw[i * w * h:(i + 1) * w * h]
        if sum(frame) / len(frame) > LIT_LUMA:
            t = i * step
            if spans and t - spans[-1][1] < step / 2:
                spans[-1][1] = round(t + step, 3)
            else:
                spans.append([round(t, 3), round(t + step, 3)])
    return spans


def lit_captions(chunks: list[dict], spans: list) -> list[dict]:
    """Marks the captions shown mostly over bright footage ("lit")."""
    out = []
    for c in chunks:
        c = {k: v for k, v in c.items() if k != "lit"}
        over = sum(max(0.0, min(c["end"], b) - max(c["start"], a)) for a, b in spans or [])
        if over >= 0.4 * max(c["end"] - c["start"], 0.01):
            c["lit"] = True
        out.append(c)
    return out


# Captions sit in the band from CAPTION_TOP down; a card's words stay above CARD_BOTTOM, so the
# two never overlap. Instagram's own buttons and text cover the top ~220 px and the bottom ~380 px
# (and a column on the right): titles start at 318, captions end above 1540.
CAPTION_TOP, CARD_BOTTOM = 1240, 1150
HOOK_SECONDS_SHOWN = 2.6

_OVERLAY_BASE = """
html,body{margin:0;width:1080px;height:1920px;overflow:hidden;background:transparent}
#overlay{position:relative;width:1080px;height:1920px;background:transparent}
"""

# classic: the first look.
_OVERLAY_CSS = """
.cap,.hook,.card,.callout{font-family:"Studio Caption","Noto Sans CJK SC","Noto Sans SC","Microsoft YaHei",sans-serif;
  -webkit-font-smoothing:antialiased;text-rendering:geometricPrecision}
.cap,.hook{position:absolute;left:0;right:0;margin:0 auto;width:fit-content;text-align:center;color:var(--ink);
  white-space:normal;text-wrap:balance;z-index:3}
.cap{top:%(cap_top)dpx;padding:10px 26px 13px;font-size:56px;font-weight:700;line-height:1.2;max-width:900px;
  background:rgba(var(--deep-rgb),.52);border-radius:22px;text-shadow:0 2px 10px rgba(0,0,0,.35)}
.cap.solid{background:rgba(var(--deep-rgb),.9);text-shadow:none}
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

# gallery (Bryan's pick, 2026-10-20: "A with C's italic key word in captions"): no box and no
# accent colour; the shade colour (--shade-rgb) darkens the footage under the words.
_GALLERY_CSS = """
.cap,.t,.card{font-family:"Studio Caption","Noto Sans CJK SC","Noto Sans SC","Microsoft YaHei",sans-serif;
  -webkit-font-smoothing:antialiased;text-rendering:geometricPrecision;font-kerning:normal}
.t h1,.card .title,.cap em{font-family:"Studio Serif","Noto Serif CJK SC","Noto Sans CJK SC","Noto Sans SC",serif}
.mask{display:inline-block;overflow:hidden;vertical-align:top;padding-bottom:.12em;margin-bottom:-.12em}
.mask>span{display:inline-block}
.scrim,.capscrim{position:absolute;left:0;right:0;pointer-events:none}
.scrim{top:0;height:980px;
  background:linear-gradient(180deg,rgba(var(--shade-rgb),.64) 0%%,rgba(var(--shade-rgb),.52) 56%%,rgba(var(--shade-rgb),0) 100%%)}
.capscrim{bottom:0;height:1000px;z-index:0;
  background:linear-gradient(0deg,rgba(var(--shade-rgb),.5) 0%%,rgba(var(--shade-rgb),.34) 50%%,rgba(var(--shade-rgb),0) 100%%)}
.title-clip{position:absolute;inset:0;z-index:2}
.t{position:absolute;left:92px;right:140px;top:318px;color:var(--ink)}
.t h1{margin:0;font-weight:400;line-height:.96;letter-spacing:-.012em;text-wrap:balance;
  text-shadow:0 0 40px rgba(var(--shade-rgb),.3)}
.t h1 em,.card .title em{font-style:italic;letter-spacing:-.005em}
.rule{width:104px;height:2px;background:var(--ink);opacity:.85;margin:40px 0 26px;transform-origin:left center}
.t p{margin:0;font-size:38px;font-weight:500;line-height:1.3;letter-spacing:.004em;color:var(--ink);opacity:.95;
  text-shadow:0 0 22px rgba(var(--shade-rgb),.55)}
.cap{position:absolute;left:96px;right:150px;top:%(cap_top)dpx;margin:0;text-align:center;text-wrap:balance;z-index:3;
  color:var(--ink);font-size:54px;font-weight:500;line-height:1.2;letter-spacing:-.006em;
  text-shadow:0 1px 2px rgba(var(--shade-rgb),.45),0 0 32px rgba(var(--shade-rgb),.5)}
.cap.lit{text-shadow:0 1px 3px rgba(var(--shade-rgb),.6),0 0 12px rgba(var(--shade-rgb),.45),0 0 36px rgba(var(--shade-rgb),.6)}
.cap>span{display:inline}
.cap.lit>span{display:inline-block;position:relative}
.cap.lit>span::before{content:"";position:absolute;left:-90px;right:-90px;top:-60px;bottom:-60px;z-index:-1;
  background:radial-gradient(closest-side,rgba(var(--shade-rgb),.55),rgba(var(--shade-rgb),.38) 50%%,rgba(var(--shade-rgb),0))}
.cap em{font-style:italic;font-weight:400;font-size:1.15em;line-height:1;letter-spacing:-.005em}
.cap.solid>span{color:var(--paper-ink);background:rgba(var(--paper-rgb),.94);padding:2px 18px 8px;border-radius:10px;
  -webkit-box-decoration-break:clone;box-decoration-break:clone;text-shadow:none}
.card{position:absolute;inset:0;z-index:1;overflow:hidden;color:var(--ink)}
.card .bg{position:absolute;inset:-60px;background:radial-gradient(circle at 30%% 20%%,var(--card-hi),var(--card-lo) 72%%)}
.card .inner{position:absolute;left:92px;right:140px;top:330px;bottom:%(card_gap)dpx;display:flex;flex-direction:column;
  justify-content:center;overflow:hidden}
.card .title{font-weight:400;line-height:.98;letter-spacing:-.012em;text-wrap:balance}
.card .rule{flex:none;margin:40px 0 0}
.card ul{list-style:none;margin:44px 0 0;padding:0}
.card li{font-weight:500;margin:0 0 26px;display:flex;align-items:baseline;gap:24px;line-height:1.2}
.card li::before{content:"";flex:0 0 28px;height:2px;background:var(--ink);opacity:.6;transform:translateY(-.32em)}
""" % {"cap_top": CAPTION_TOP, "card_gap": H - CARD_BOTTOM}

# The end card (close_ending, end_card), in the gallery look whatever the draft's titles: the
# account's deep green, the Urban Makers mark, the post's ask (or the name) in the serif, a hairline,
# the handle. Full screen, over everything; captions are off while it's up.
_END_CSS = """
.endcard{position:absolute;inset:0;z-index:6;overflow:hidden;color:var(--ink);text-align:center;
  font-family:"Studio Caption","Noto Sans CJK SC","Noto Sans SC",sans-serif;-webkit-font-smoothing:antialiased}
.endcard .bg{position:absolute;inset:-60px;background:radial-gradient(circle at 50% 40%,var(--card-hi),var(--card-lo) 74%)}
.endcard .inner{position:absolute;left:120px;right:120px;top:0;bottom:0;display:flex;flex-direction:column;
  align-items:center;justify-content:center;padding-bottom:120px}
.endcard .mk svg{display:block;width:200px;height:auto}
.endcard h2{margin:56px 0 0;font-family:"Studio Serif","Noto Serif CJK SC",serif;font-weight:400;line-height:1.02;
  letter-spacing:-.012em;text-wrap:balance;max-width:840px}
.endcard h2 em{font-style:italic}
.endcard .mask{display:inline-block;overflow:hidden;vertical-align:top;padding-bottom:.12em;margin-bottom:-.12em}
.endcard .mask>span{display:inline-block}
.endcard .rule{width:104px;height:2px;background:var(--ink);opacity:.85;margin:40px auto 30px;transform-origin:center}
.endcard .hd{font-size:40px;font-weight:500;letter-spacing:.01em;opacity:.92}
"""


def _end_card_html(card: dict) -> tuple[str, str]:
    """The end card's markup and animation: the shade comes up over the last frame, the mark rises,
    the words rise out of their masks, the hairline draws, the handle fades up; then it holds."""
    s, d = card["start"], card["duration"]
    text, italic = title_parts(card.get("ask") or "") if card.get("ask") else ("Urban Makers", "")
    px = 92 if len(text) <= 22 else 76 if len(text) <= 36 else 64
    handle = card.get("handle") or ""
    markup = (f'<div id="endcard" class="endcard clip" data-start="{s:.3f}" data-duration="{d:.3f}" data-track-index="40">'
              f'<div class="bg"></div><div class="inner"><div class="mk">{motion.mark_svg()}</div>'
              f'<h2 style="font-size:{px}px">{_italic_html(text, italic, masks=True)}</h2><div class="rule"></div>'
              + (f'<div class="hd">{html.escape(handle)}</div>' if handle else "") + "</div></div>")
    anim = (f"tl.from('#endcard',{{opacity:0,duration:.45,ease:'power1.out'}},{s:.3f});"
            f"tl.fromTo('#endcard .bg',{{scale:1.06}},{{scale:1,duration:{d:.3f},ease:'none'}},{s:.3f});"
            f"tl.from('#endcard .mk',{{opacity:0,y:28,duration:.6,ease:'power3.out'}},{s + .15:.3f});"
            f"tl.from('#endcard h2 .mask>span',{{yPercent:105,duration:.7,ease:'power3.out',stagger:.07}},{s + .3:.3f});"
            f"tl.from('#endcard .rule',{{scaleX:0,duration:.55,ease:'power2.inOut'}},{s + .5:.3f});"
            + (f"tl.from('#endcard .hd',{{opacity:0,y:10,duration:.45,ease:'power2.out'}},{s + .65:.3f});" if handle else ""))
    return markup, anim


# ── Site Notes (Bryan's pick, 2026-10-22: "go with A for now") ─────────────────
# One family, Archivo (bundled at 88% width, 700/750/800), for every word in the Reel; words on tight
# dark labels; one accent, brass (THEME "brass"), for the key word, a callout's label and the handle.
# Cards are a dark panel over the top of the footage (big digits and bars for numbers, label pairs for
# "Now" / "The plan"); the only full-screen card is the end card, over the last frame with a dark shade.
# Sizes are the proposal's, in Reel pixels (https://claude.ai/artifact/TwCheZsi7ywNKNEAEJ3FcM).
SITE_FONTS = {w: FONTS / f"Archivo-latin-{w}.woff2" for w in ("700", "750", "800")}
SITE_LEFT, SITE_RIGHT, SITE_TOP, SITE_PANEL_TOP = 64, 120, 300, 250  # the labels from x 64, clear of the buttons
SITE_HOOK_PX, SITE_CALLOUT_PX, SITE_PAIR_PX, SITE_END_PX = 104, 92, 76, 118
SITE_PIP_CAPTION_PX = 60  # a caption beside a voice-over's window (about 480 px of room)
# Each kind of label: (font px, weight, letter-spacing in em, padding top/right/bottom/left).
SITE_LABELS = {"lb": (SITE_HOOK_PX, "800", -0.015, (10, 24, 18, 24)), "s": (50, "700", 0.0, (10, 20, 14, 20)),
               "k": (34, "700", 0.12, (10, 16, 10, 16))}
SITE_GAP = 12             # between two labels of a stack
_PANEL_W = W - 2 * 64 - 88  # the words' width inside a panel (64 px each side, 44 px padding)
_SITE_MEASURE = {}

_SITE_CSS = """
.cap,.lbs,.panel{font-family:"Archivo","Noto Sans CJK SC","Noto Sans SC","Microsoft YaHei",sans-serif;
  -webkit-font-smoothing:antialiased;text-rendering:geometricPrecision;font-kerning:normal}
.site{position:absolute;inset:0;z-index:2}
.cap{position:absolute;left:90px;right:150px;top:%(cap_top)dpx;margin:0;text-align:center;text-wrap:balance;z-index:3;
  color:#fff;font-size:74px;font-weight:750;line-height:1.1;letter-spacing:-.01em;
  text-shadow:0 2px 4px rgba(0,0,0,.5),0 0 30px rgba(0,0,0,.45)}
.cap em{font-style:normal;color:var(--brass)}
.cap>span{display:inline}
.cap.lit{text-shadow:0 2px 4px rgba(0,0,0,.62),0 0 14px rgba(0,0,0,.5),0 0 36px rgba(0,0,0,.6)}
.cap.lit>span{display:inline-block;position:relative}
.cap.lit>span::before{content:"";position:absolute;left:-90px;right:-90px;top:-60px;bottom:-60px;z-index:-1;
  background:radial-gradient(closest-side,rgba(var(--label-rgb),.55),rgba(var(--label-rgb),.38) 50%%,rgba(var(--label-rgb),0))}
.lbs{position:absolute;left:%(left)dpx;right:%(right)dpx;display:flex;flex-direction:column;align-items:flex-start;
  gap:%(gap)dpx;z-index:2}
.lb{display:block;background:rgba(var(--label-rgb),.9);color:#fff;font-weight:800;font-size:104px;line-height:1;
  padding:10px 24px 18px;letter-spacing:-.015em;white-space:nowrap}
.lb em{font-style:normal;color:var(--brass)}
.lb.s{font-size:50px;font-weight:700;padding:10px 20px 14px;letter-spacing:0}
.lb.k{font-size:34px;font-weight:700;letter-spacing:.12em;text-transform:uppercase;padding:10px 16px}
.lb.br{background:var(--brass);color:var(--brass-ink)}
.lb.br em{color:inherit}
.panel{position:absolute;left:64px;right:64px;top:%(panel_top)dpx;z-index:1;color:#fff;background:rgba(var(--label-rgb),.9);
  padding:40px 44px 48px;display:flex;flex-direction:column;gap:30px;overflow:hidden;max-height:%(panel_max)dpx}
.panel .key{font-size:34px;font-weight:700;line-height:1;letter-spacing:.12em;text-transform:uppercase;color:var(--brass)}
.panel .ttl{font-size:76px;font-weight:800;line-height:1.04;letter-spacing:-.015em}
.panel .ttl em{font-style:normal;color:var(--brass)}
.panel .pt{display:flex;align-items:baseline;gap:24px;font-size:52px;font-weight:700;line-height:1.15}
.panel .pt::before{content:"";flex:0 0 28px;height:6px;background:rgba(255,255,255,.55);transform:translateY(-.3em)}
.panel .row{display:grid;align-items:center;gap:22px;font-size:48px;font-weight:700;line-height:1.1}
.panel .bar{height:26px;background:rgba(255,255,255,.35);transform-origin:left center}
.panel .row.hi .bar{background:var(--brass)}
.panel .num{font-size:84px;font-weight:800;line-height:1;text-align:right;font-variant-numeric:tabular-nums;letter-spacing:-.02em}
.panel .row.hi .num{color:var(--brass)}
.lead{position:absolute;inset:0;z-index:1}
.lead svg{position:absolute;left:0;top:0;width:%(w)dpx;height:%(h)dpx;overflow:visible}
.lead .dot{position:absolute;width:44px;height:44px;margin:-22px 0 0 -22px;box-sizing:border-box;border-radius:50%%;
  border:8px solid var(--brass);background:rgba(var(--label-rgb),.5)}
""" % {"cap_top": CAPTION_TOP, "left": SITE_LEFT, "right": SITE_RIGHT, "gap": SITE_GAP, "panel_top": SITE_PANEL_TOP,
       "panel_max": CARD_BOTTOM - SITE_PANEL_TOP, "w": W, "h": H}

# The end card in Site Notes: the last frame under a dark shade, the mark, the ask in Archivo with one
# brass word, the handle on a brass label; left-aligned like the labels.
_SITE_END_CSS = """
.endcard{position:absolute;inset:0;z-index:6;overflow:hidden;color:#fff;
  font-family:"Archivo","Noto Sans CJK SC","Noto Sans SC",sans-serif;-webkit-font-smoothing:antialiased}
.endcard .shade{position:absolute;inset:0;background:rgba(var(--label-rgb),.74)}
.endcard .inner{position:absolute;left:%(left)dpx;right:%(right)dpx;top:560px;display:flex;flex-direction:column;
  align-items:flex-start;gap:44px}
.endcard .mk svg{display:block;width:170px;height:auto}
.endcard h2{margin:0;font-weight:800;line-height:1;letter-spacing:-.02em}
.endcard h2 em{font-style:normal;color:var(--brass)}
.endcard .ln{display:block;overflow:hidden;padding-bottom:.1em;margin-bottom:-.1em;white-space:nowrap}
.endcard .ln>span{display:block}
.endcard .hd{display:block;background:var(--brass);color:var(--brass-ink);font-size:50px;font-weight:700;line-height:1;
  padding:10px 20px 14px}
""" % {"left": SITE_LEFT, "right": SITE_RIGHT}


def _site_width(text: str, px: float, weight: str = "800", track: float = 0.0) -> float:
    """How wide Archivo sets text at px (measured from the bundled face; an estimate when Pillow can't read
    it). Chinese is a square a character (it comes from the system's CJK font)."""
    cjk = len(_CJK.findall(text))
    rest = _CJK.sub("", text)
    try:
        if weight not in _SITE_MEASURE:
            from PIL import ImageFont
            _SITE_MEASURE[weight] = ImageFont.truetype(str(SITE_FONTS[weight]), 200)
        width = _SITE_MEASURE[weight].getlength(rest) * px / 200
    except Exception:
        width = len(rest) * 0.56 * px
    return width + cjk * px + track * px * len(text)


def site_lines(text: str, px: float, width: float, weight: str = "800", track: float = -0.015) -> list[str]:
    """Words into the fewest lines that fit width, as even as they'll go (each line is a label of its own).
    Chinese, which has no spaces, breaks between characters. A word wider than width keeps a line of its own."""
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    sep = "" if _CJK.search(text) and " " not in text else " "
    words = list(text) if sep == "" else text.split(" ")
    if not words or not text:
        return []
    def wide(ws):
        return _site_width(sep.join(ws), px, weight, track)
    n, cur = 1, []
    for w in words:
        if cur and wide(cur + [w]) > width:
            n, cur = n + 1, [w]
        else:
            cur.append(w)
    if n == 1:
        return [text]
    if len(words) > 18:  # long: as they come
        lines, cur = [], []
        for w in words:
            if cur and wide(cur + [w]) > width:
                lines.append(sep.join(cur))
                cur = []
            cur.append(w)
        return lines + [sep.join(cur)]
    import itertools
    best = None
    for breaks in itertools.combinations(range(1, len(words)), n - 1):
        parts = [words[a:b] for a, b in zip((0,) + breaks, breaks + (len(words),))]
        widest = max(wide(p) for p in parts)
        if best is None or widest < best[0] - 0.5:
            best = (widest, parts)
    return [sep.join(p) for p in best[1]]


def _site_fit(text: str, px: int, width: float, most: int = 3, weight: str = "800", track: float = -0.015,
              smallest: int = 64) -> tuple[list[str], int]:
    """A title's lines and size: px, smaller (to `smallest`) while it takes more than `most` lines or a word won't fit."""
    while True:
        lines = site_lines(text, px, width, weight, track)
        if px <= smallest or (len(lines) <= most and all(_site_width(x, px, weight, track) <= width for x in lines)):
            return lines, px
        px -= 6


def _site_label_box(text: str, kind: str, px: int | None = None) -> tuple[float, float]:
    """A label's width and height on the Reel."""
    size, weight, track, (t, r, b, left) = SITE_LABELS[kind]
    px = px or size
    shown = text.upper() if kind == "k" else text
    return _site_width(shown, px, weight, track) + left + r, px + t + b


_NUMBER = re.compile(r"(?<![\w.,])(\$?\d[\d,]*(?:\.\d+)?)\s?(%|mm|cm|m|m2|sqft|kg|kW|A|amps?|V|k|days?|weeks?|months?|"
                     r"years?|yrs?|hours?|hrs?|h|mins?|x)?(?![\w])", re.I)
_PAIR = re.compile(r"^\s*([^:]{1,24}?)\s*:\s*(\S.*)$")


def _site_numbers(lines: list[str]) -> list[dict] | None:
    """A card's points as numbers to draw as bars ("Older HDB 30A", "HDB: 40A"): each point one number and a
    label, at least two, all above zero. None when they aren't."""
    rows = []
    for line in lines:
        found = list(_NUMBER.finditer(line))
        if len(found) != 1:
            return None
        m = found[0]
        label = (line[:m.start()] + " " + line[m.end():]).strip(" :–-—,")
        label = re.sub(r"\s{2,}", " ", label)
        try:
            value = float(m.group(1).replace("$", "").replace(",", ""))
        except ValueError:
            return None
        if not label or value <= 0:
            return None
        rows.append({"label": label, "value": value, "shown": m.group(0).strip()})
    return rows if len(rows) >= 2 else None


def _site_pairs(lines: list[str]) -> list[tuple[str, str]] | None:
    """A card's points as label pairs ("Now: dark, even by day", "The plan: lit with our own lights"): every
    point a short key (at most 3 words) and its words, at least two. None when they aren't."""
    pairs = []
    for line in lines:
        m = _PAIR.match(line)
        if not m or len(m.group(1).split()) > 3:
            return None
        pairs.append((m.group(1), m.group(2)))
    return pairs if len(pairs) >= 2 else None


def site_layout(kind: str, title: str, italic: str = "", lines: list[str] = (), point=None) -> dict:
    """Where a Site Notes graphic's words go: {"form": stack | panel-points | panel-numbers, "items": the labels
    or panel rows, "box": (x0, y0, x1, y1) on the Reel}. kind: hook, callout or card. The overlay draws from it
    and the watch-through (final_check) measures the same box."""
    lines = [unmark(x) for x in lines if unmark(x)]
    width = W - SITE_LEFT - SITE_RIGHT
    if kind in ("hook", "callout"):
        cls, px = ("lb br", SITE_CALLOUT_PX) if kind == "callout" else ("lb", SITE_HOOK_PX)
        rows, px = _site_fit(title, px, width - 48)
        items = [{"text": x, "cls": cls, "px": px, "kind": "lb"} for x in rows]
        if kind == "callout" and lines:
            items.append({"text": lines[0], "cls": "lb s", "px": None, "kind": "s"})
        top = SITE_TOP
        form = "stack"
    else:
        pairs, numbers = _site_pairs(lines), _site_numbers(lines)
        if pairs:
            form, top, items = "stack", SITE_TOP - 30, []
            if title:
                items.append({"text": title, "cls": "lb s", "px": None, "kind": "s"})
            for n, (key, said) in enumerate(pairs):
                items.append({"text": key, "cls": "lb k" + (" br" if n == len(pairs) - 1 else ""), "px": None,
                              "kind": "k", "gap": 22 if n else None})
                rows, px = _site_fit(said, SITE_PAIR_PX, width - 48, 2, smallest=56)
                items += [{"text": x, "cls": "lb", "px": px, "kind": "lb"} for x in rows]
        else:
            form, top = ("panel-numbers" if numbers else "panel-points"), SITE_PANEL_TOP
            items = []
            h = 40 + 48
            if numbers:
                if title:
                    items.append({"text": title, "cls": "key"})
                    h += 34 + 30
                hi = next((k for k, r in enumerate(numbers) if italic and italic.lower() in r["label"].lower() + " "
                           + r["shown"].lower()), max(range(len(numbers)), key=lambda k: numbers[k]["value"]))
                most = max(r["value"] for r in numbers)
                for k, r in enumerate(numbers):
                    label_lines = len(site_lines(r["label"], 48, 300, "700", 0.0))
                    items.append({**r, "cls": "row" + (" hi" if k == hi else ""), "share": r["value"] / most})
                    h += max(84, label_lines * 53) + 30
                h -= 30
            else:
                tl, tpx = _site_fit(title, 76, _PANEL_W, 3, smallest=60)
                if title:
                    items.append({"text": title, "cls": "ttl", "px": tpx})
                    h += len(tl) * round(tpx * 1.04) + 30
                for x in lines:
                    n = len(site_lines(x, 52, _PANEL_W - 52, "700", 0.0))
                    items.append({"text": x, "cls": "pt"})
                    h += n * 60 + 30
                h -= 30
            return {"form": form, "items": items,
                    "box": (64.0, float(top), float(W - 64), float(min(top + h, CARD_BOTTOM)))}
    y, widest = float(top), 0.0
    for n, it in enumerate(items):
        w, h = _site_label_box(it["text"], it["kind"], it.get("px"))
        y += (it.get("gap") or SITE_GAP) if n else 0
        widest = max(widest, w)
        y += h
    box = (float(SITE_LEFT), float(top), float(SITE_LEFT + min(widest, width)), y)
    return {"form": form, "items": items, "box": box, **({"point": _site_point(point, box)} if point else {})}


def _site_point(point, box) -> dict | None:
    """A callout's leader: from under its labels down to the thing it names (point [x, y] on the Reel, given by the
    plan), a brass line and a dot. None when the point can't be pointed at from below (above the labels, past
    the captions' line, off the frame)."""
    try:
        px, py = float(point[0]), float(point[1])
    except (TypeError, ValueError, IndexError):
        return None
    x0, y0 = box[0] + 56, box[3] + 24
    if not (40 <= px <= W - 40) or py > CARD_BOTTOM or py < y0 + 60:
        return None
    bend = max(y0 + 40, py - abs(px - x0))
    return {"x": round(px), "y": round(py), "path": f"M{x0:.0f} {y0:.0f} L{x0:.0f} {bend:.0f} L{px:.0f} {py:.0f}"}


def _site_label_html(it: dict, italic: str, used: list) -> str:
    """One label; the key word (the first time it comes) in <em>."""
    word = italic if italic and not used and any(_core(t).lower() == italic.lower() for t in it["text"].split(" ")) else ""
    if word:
        used.append(word)
    style = (f'font-size:{it["px"]}px;' if it.get("px") and it["px"] != SITE_LABELS[it["kind"]][0] else "") + (
        f'margin-top:{it["gap"] - SITE_GAP}px' if it.get("gap") else "")
    return f'<span class="{it["cls"]}"' + (f' style="{style}"' if style else "") + f'>{_italic_html(it["text"], word)}</span>'


def _site_title(gid: str, attrs: str, s: float, d: float, kind: str, title: str, italic: str,
                lines: list[str] = (), point=None) -> tuple[str, str]:
    """The hook, a callout or a label-pair card on dark labels, stacked from the top left: the labels slide in one
    after another, a callout's leader draws to its dot; it fades out at its end."""
    lay = site_layout(kind, title, italic, lines, point)
    used = []
    labels = "".join(_site_label_html(it, italic, used) for it in lay["items"])
    lead = lay.get("point")
    markup = (f'<div {attrs} class="site clip">'
              + (f'<div class="lead"><svg viewBox="0 0 {W} {H}"><path d="{lead["path"]}" stroke="var(--brass)" '
                 f'stroke-width="7" fill="none" pathLength="1" stroke-dasharray="1"/></svg>'
                 f'<span class="dot" style="left:{lead["x"]}px;top:{lead["y"]}px"></span></div>' if lead else "")
              + f'<div class="lbs" style="top:{lay["box"][1]:.0f}px">{labels}</div></div>')
    anim = (f"tl.from('#{gid} .lb',{{x:-36,opacity:0,duration:.42,ease:'power3.out',stagger:.1}},{s:.3f});"
            + (f"tl.fromTo('#{gid} .lead path',{{strokeDashoffset:1}},{{strokeDashoffset:0,duration:.5,ease:'power2.inOut'}},"
               f"{s + .35:.3f});tl.from('#{gid} .dot',{{scale:0,opacity:0,duration:.3,ease:'power2.out'}},{s + .75:.3f});"
               if lead else "")
            + (f"tl.to('#{gid}',{{opacity:0,duration:.3,ease:'power1.in'}},{s + d - .3:.3f});" if d > 1.2 else ""))
    return markup, anim


def _site_card(gid: str, attrs: str, s: float, d: float, title: str, italic: str, lines: list[str]) -> tuple[str, str]:
    """A card in Site Notes: label pairs ("Now" / "The plan") as a stack, numbers as big digits and bars, anything
    else a headline and its points; never full screen."""
    lay = site_layout("card", title, italic, lines)
    if lay["form"] == "stack":
        return _site_title(gid, attrs, s, d, "card", title, italic, lines)
    rows = []
    if lay["form"] == "panel-numbers":
        numw = max(_site_width(it["shown"], 84, "800", -0.02) for it in lay["items"] if it["cls"].startswith("row")) + 6
        for it in lay["items"]:
            if it["cls"] == "key":
                rows.append(f'<div class="key">{html.escape(it["text"])}</div>')
            else:
                rows.append(f'<div class="{it["cls"]}" style="grid-template-columns:300px 1fr {numw:.0f}px">'
                            f'<span>{html.escape(it["label"])}</span><span class="bar" style="width:{it["share"] * 100:.0f}%"></span>'
                            f'<span class="num">{html.escape(it["shown"])}</span></div>')
    else:
        for it in lay["items"]:
            if it["cls"] == "ttl":
                rows.append(f'<div class="ttl" style="font-size:{it["px"]}px">{_italic_html(it["text"], italic)}</div>')
            else:
                rows.append(f'<div class="pt"><span>{html.escape(it["text"])}</span></div>')
    markup = f'<div {attrs} class="site clip"><div class="panel">{"".join(rows)}</div></div>'
    anim = (f"tl.from('#{gid} .panel',{{opacity:0,y:24,duration:.4,ease:'power3.out'}},{s:.3f});"
            f"tl.from('#{gid} .panel>div',{{opacity:0,x:-24,duration:.38,ease:'power2.out',stagger:.14}},{s + .15:.3f});"
            + (f"tl.from('#{gid} .bar',{{scaleX:0,duration:.6,ease:'power3.inOut',stagger:.14}},{s + .3:.3f});"
               if lay["form"] == "panel-numbers" else "")
            + (f"tl.to('#{gid}',{{opacity:0,duration:.25,ease:'power1.in'}},{s + d - .25:.3f});" if d > 1.2 else ""))
    return markup, anim


def _site_end_html(card: dict) -> tuple[str, str]:
    """The end card in Site Notes: the shade comes up over the last frame, the mark rises, the ask's lines rise out
    of their masks (one brass word: the marked one, else its last word), the handle's label slides in; then it holds."""
    s, d = card["start"], card["duration"]
    text, italic = title_parts(card.get("ask") or "") if card.get("ask") else ("Urban Makers", "")
    if not italic and not _CJK.search(text):
        last = _core(text.split()[-1]) if text.split() else ""
        italic = last if last.isalpha() and len(last) >= 3 else ""
    rows, px = _site_fit(text, SITE_END_PX, W - SITE_LEFT - SITE_RIGHT, 3, track=-0.02, smallest=76)
    used = []
    h2 = ""
    for x in rows:
        word = italic if italic and not used and any(_core(t).lower() == italic.lower() for t in x.split(" ")) else ""
        if word:
            used.append(word)
        h2 += f'<span class="ln"><span>{_italic_html(x, word)}</span></span>'
    handle = card.get("handle") or ""
    markup = (f'<div id="endcard" class="endcard clip" data-start="{s:.3f}" data-duration="{d:.3f}" data-track-index="40">'
              f'<div class="shade"></div><div class="inner"><div class="mk">{motion.mark_svg()}</div>'
              f'<h2 style="font-size:{px}px">{h2}</h2>'
              + (f'<span class="hd">{html.escape(handle)}</span>' if handle else "") + "</div></div>")
    anim = (f"tl.from('#endcard .shade',{{opacity:0,duration:.45,ease:'power1.out'}},{s:.3f});"
            f"tl.from('#endcard .mk',{{opacity:0,y:28,duration:.55,ease:'power3.out'}},{s + .12:.3f});"
            f"tl.from('#endcard h2 .ln>span',{{yPercent:105,duration:.6,ease:'power3.out',stagger:.08}},{s + .25:.3f});"
            + (f"tl.from('#endcard .hd',{{opacity:0,x:-30,duration:.4,ease:'power2.out'}},{s + .6:.3f});" if handle else ""))
    return markup, anim


def end_css(style: str | None = None) -> str:
    """The end card's styles for a look: Site Notes' own, else the gallery card (classic drafts have it too)."""
    return _SITE_END_CSS if title_style(style) == "site" else _END_CSS


def end_card_html(card: dict, style: str | None = None) -> tuple[str, str]:
    """The end card's markup and animation in a look (end_css)."""
    return _site_end_html(card) if title_style(style) == "site" else _end_card_html(card)


# Each account's look for captions and graphics: Urban Makers' green, warm white and amber (as on its
# carousels; the amber only in the classic look). shade-rgb: the gallery look's shade under the words;
# paper: the drawn inserts' paper and ink (motion.css), for a caption over a cutaway. Site Notes: brass,
# its one accent (and the ink on a brass label), and label-rgb, the dark of its labels, panels and shades.
THEME = {
    "urban_makers": {"accent": "#f5a524", "ink": "#f6f4ee", "deep-rgb": "24,35,31", "card-hi": "#2f4540",
                     "card-lo": "#18231f", "shade-rgb": "14,18,20", "brass": "#F2B65A", "brass-ink": "#16140F",
                     "label-rgb": "18,17,14"},
    "personal": {"accent": "#7c9cff", "ink": "#ffffff", "deep-rgb": "11,11,13", "card-hi": "#2a2a33",
                 "card-lo": "#0b0b0d", "shade-rgb": "11,11,13", "brass": "#F2B65A", "brass-ink": "#16140F",
                 "label-rgb": "18,17,14"},
}
SITE_KEYS = ("brass", "brass-ink", "label-rgb")   # only on a Site Notes page (the other looks' pages stay as they were)
PAPER = {"paper-rgb": "251,250,247", "paper-ink": "#1E2A32"}
ACCENT = {k: v["accent"] for k, v in THEME.items()}


def _theme_css(accent: str, style: str | None = None) -> str:
    theme = next((t for t in THEME.values() if t["accent"] == accent), THEME["urban_makers"])
    site = style == "site"
    return ":root{" + ";".join(f"--{k}:{v}" for k, v in {**theme, **PAPER, "accent": accent}.items()
                               if site or k not in SITE_KEYS) + "}"


def _card_sizes(title: str, lines: list[str], style: str = "classic") -> tuple[int, int]:
    """Headline and point sizes for a card, by how much text it has, so it stays clear of the
    caption band (the box also hides any overflow). The serif (gallery) is set about 1.15x larger:
    it is lighter and narrower."""
    line = max((len(x) for x in lines), default=0)
    if style == "gallery":
        title_px = 116 if len(title) <= 14 else 106 if len(title) <= 20 else 92
    else:
        title_px = 100 if len(title) <= 14 else 92 if len(title) <= 20 else 80
    return title_px, 62 if line <= 16 else 56 if line <= 24 else 48


def _title_px(text: str) -> int:
    """A gallery title's size (hook and callout): 128 px for a short one, smaller as it grows."""
    n = len(text)
    return 128 if n <= 16 else 112 if n <= 24 else 96


def _italic_html(text: str, word: str, masks: bool = False) -> str:
    """The title's words (each in a mask for the rise, when masks), its italic word in <em>."""
    out, done = [], False
    for t in text.split(" "):
        pre, core, post = _EDGES.match(t).groups()
        if word and not done and core.lower() == word.lower():
            piece = f"{html.escape(pre)}<em>{html.escape(core)}</em>{html.escape(post)}"
            done = True
        else:
            piece = html.escape(t)
        out.append(f'<span class="mask"><span>{piece}</span></span>' if masks else piece)
    return " ".join(out)


def _caption_html(c: dict, style: str) -> str:
    """A caption's words; in the gallery and Site Notes looks its key word in <em>."""
    if style not in ("gallery", "site"):
        return html.escape(c["text"])
    tokens = [html.escape(t) for t in c["text"].split(" ")]
    k = c.get("key")
    if isinstance(k, int) and 0 <= k < len(tokens):
        pre, core, post = _EDGES.match(c["text"].split(" ")[k]).groups()
        tokens[k] = f"{html.escape(pre)}<em>{html.escape(core)}</em>{html.escape(post)}"
    return "<span>" + " ".join(tokens) + "</span>"


def _graphic_html(i: int, g: dict, style: str = "classic") -> tuple[str, str]:
    """Markup and GSAP animation for one B-roll graphic. Everything moves, gently."""
    s, d, gid = g["start"], g["duration"], f"g{i}"
    attrs = f'id="{gid}" data-start="{s:.3f}" data-duration="{d:.3f}" data-track-index="{2 + i}"'
    title, italic = (unmark(g["title"]), g["italic"]) if "italic" in g else title_parts(g["title"])
    lines = [unmark(x) for x in g["lines"]]
    if style == "site":
        if g["style"] == "card":
            return _site_card(gid, attrs, s, d, title, italic, lines)
        return _site_title(gid, attrs, s, d, "callout", title, italic, lines, g.get("point"))
    if style == "gallery":
        return _gallery_graphic(gid, attrs, s, d, g["style"], title, italic, lines)
    if g["style"] == "card":
        title_px, line_px = _card_sizes(title, lines)
        items = "".join(f'<li style="font-size:{line_px}px">{html.escape(line)}</li>' for line in lines)
        markup = (f'<div {attrs} class="card clip"><div class="bg"></div><div class="inner"><div class="bar"></div>'
                  f'<div class="title" style="font-size:{title_px}px">{html.escape(title)}</div>'
                  f'<ul>{items}</ul></div></div>')
        anim = (f"tl.from('#{gid}',{{opacity:0,duration:.25}},{s:.3f});"
                f"tl.fromTo('#{gid} .bg',{{scale:1.12}},{{scale:1,duration:{d:.3f},ease:'none'}},{s:.3f});"
                f"tl.from('#{gid} .bar',{{scaleX:0,transformOrigin:'left',duration:.4,ease:'power3.out'}},{s + .05:.3f});"
                f"tl.from('#{gid} .title',{{y:50,opacity:0,duration:.45,ease:'power3.out'}},{s + .1:.3f});"
                f"tl.from('#{gid} li',{{x:-50,opacity:0,duration:.35,stagger:.22,ease:'power2.out'}},{s + .35:.3f});")
    else:
        label = html.escape(lines[0]) if lines else ""
        markup = (f'<div {attrs} class="callout clip"><div class="big">{html.escape(title)}</div>'
                  + (f'<div class="label">{label}</div>' if label else "") + "</div>")
        anim = (f"tl.from('#{gid}',{{y:-40,opacity:0,duration:.35,ease:'power2.out'}},{s:.3f});"
                f"tl.from('#{gid} .big',{{scale:.7,duration:.45,ease:'back.out(2)'}},{s + .05:.3f});")
    return markup, anim


def _gallery_title(gid: str, attrs: str, s: float, d: float, title: str, italic: str, sub: str = "") -> tuple[str, str]:
    """A gallery title over the footage (the hook, a callout): the shade from the top, the serif
    title (its words rise out of a mask one after another), the hairline drawn, the line under it
    fading up; it fades out at its end."""
    markup = (f'<div {attrs} class="title-clip clip"><div class="scrim"></div><div class="t">'
              f'<h1 style="font-size:{_title_px(title)}px">{_italic_html(title, italic, masks=True)}</h1>'
              '<div class="rule"></div>' + (f"<p>{html.escape(sub)}</p>" if sub else "") + "</div></div>")
    anim = (f"tl.from('#{gid} .scrim',{{opacity:0,duration:.5,ease:'none'}},{s:.3f});"
            f"tl.from('#{gid} h1 .mask>span',{{yPercent:105,duration:.85,ease:'power3.out',stagger:.09}},{s + .08:.3f});"
            f"tl.from('#{gid} .rule',{{scaleX:0,duration:.7,ease:'power2.inOut'}},{s + .3:.3f});"
            + (f"tl.from('#{gid} p',{{opacity:0,y:10,duration:.5,ease:'power2.out'}},{s + .5:.3f});" if sub else "")
            + (f"tl.to('#{gid}',{{opacity:0,duration:.3,ease:'power1.in'}},{s + d - .3:.3f});" if d > 1.2 else ""))
    return markup, anim


def _gallery_graphic(gid, attrs, s, d, kind, title, italic, lines) -> tuple[str, str]:
    if kind != "card":
        return _gallery_title(gid, attrs, s, d, title, italic, lines[0] if lines else "")
    title_px, line_px = _card_sizes(title, lines, "gallery")
    items = "".join(f'<li style="font-size:{line_px}px">{html.escape(line)}</li>' for line in lines)
    markup = (f'<div {attrs} class="card clip"><div class="bg"></div><div class="inner">'
              f'<div class="title" style="font-size:{title_px}px">{_italic_html(title, italic, masks=True)}</div>'
              f'<div class="rule"></div><ul>{items}</ul></div></div>')
    anim = (f"tl.from('#{gid}',{{opacity:0,duration:.3}},{s:.3f});"
            f"tl.fromTo('#{gid} .bg',{{scale:1.08}},{{scale:1,duration:{d:.3f},ease:'none'}},{s:.3f});"
            f"tl.from('#{gid} .title .mask>span',{{yPercent:105,duration:.8,ease:'power3.out',stagger:.09}},{s + .1:.3f});"
            f"tl.from('#{gid} .rule',{{scaleX:0,duration:.6,ease:'power2.inOut'}},{s + .35:.3f});"
            f"tl.from('#{gid} li',{{opacity:0,y:12,duration:.45,stagger:.2,ease:'power2.out'}},{s + .5:.3f});"
            + (f"tl.to('#{gid}',{{opacity:0,duration:.25,ease:'power1.in'}},{s + d - .25:.3f});" if d > 1.2 else ""))
    return markup, anim


def _split_at(runs: list[tuple[float, float]], spans: list) -> list[tuple[float, float]]:
    """Runs cut where a voice-over's window comes up or goes (so each piece is wholly beside it or not)."""
    out = []
    for a, b in runs:
        cuts = sorted(t for x, y in spans for t in (x, y) if a + 0.05 < t < b - 0.05)
        edges = [a] + cuts + [b]
        out += [(round(x, 3), round(y, 3)) for x, y in zip(edges, edges[1:]) if y - x >= 0.05]
    return out


def _pip_css(side: str, style: str | None = None) -> str:
    """The captions beside a voice-over's window (voiceover.caption_room), and their shade fading out before it.
    Site Notes sets them a little smaller there (SITE_PIP_CAPTION_PX): about 480 px of room, a long word fits."""
    import voiceover
    left, right = voiceover.caption_room(side)
    x = voiceover.box(side)[0]
    c = side[0]
    fade = "90deg" if side == "left" else "270deg"
    edge = f"left:{x + voiceover.PIP_W - 60}px" if side == "left" else f"right:{W - x - 60}px"
    size = f";font-size:{SITE_PIP_CAPTION_PX}px" if style == "site" else ""
    return (f".cap.pip{c}{{left:{left}px;right:{right}px;max-width:{W - left - right}px{size}}}"
            f".capscrim.pip{c}{{{edge};-webkit-mask-image:linear-gradient({fade},transparent,#000 160px);"
            f"mask-image:linear-gradient({fade},transparent,#000 160px)}}")


def caption_shade(chunks: list[dict], paper: list[tuple[float, float]] = (), join: float = 1.2) -> list[tuple[float, float]]:
    """When the gallery look's soft shade sits behind the captions: while captions are up (gaps
    shorter than join seconds bridged), never over a cutaway's paper."""
    runs = []
    for c in sorted(chunks, key=lambda c: c["start"]):
        if runs and c["start"] - runs[-1][1] < join:
            runs[-1][1] = max(runs[-1][1], c["end"])
        else:
            runs.append([c["start"], c["end"]])
    for a, b in sorted(paper):
        cut = []
        for s, e in runs:
            if e <= a or s >= b:
                cut.append([s, e])
                continue
            if s < a:
                cut.append([s, a])
            if e > b:
                cut.append([b, e])
        runs = cut
    return [(round(s, 3), round(e, 3)) for s, e in runs if e - s >= 0.3]


def _overlay_assets(project: Path, font: str | None = None, serif: bool = False, site: bool = False) -> list[str]:
    """GSAP and the bundled fonts copied into a HyperFrames project: the caption font (caption_font) and,
    with serif, the gallery look's serif (titles, the end card); with site, Archivo alone (Site Notes'
    one family, whatever the caption font). Returns their @font-face rules."""
    shutil.copy(render.RENDER_DIR / "node_modules" / "gsap" / "dist" / "gsap.min.js", project)
    css = []
    if site:
        for weight, path in SITE_FONTS.items():
            shutil.copy(path, project / f"archivo-{weight}.woff2")
            css.append(f'@font-face{{font-family:"Archivo";src:url("archivo-{weight}.woff2") format("woff2");'
                       f'font-weight:{weight};font-style:normal;font-display:block}}')
        return css
    for n, (path, weight) in enumerate(caption_font(font)[1]):
        name = "caption.woff2" if n == 0 else f"caption-{n + 1}.woff2"
        shutil.copy(path, project / name)
        css.append(f'@font-face{{font-family:"Studio Caption";src:url("{name}") format("woff2");'
                   f'font-weight:{weight};font-display:block}}')
    if serif:
        _, regular, italic = TITLE_FONTS[TITLE_STYLES["gallery"]["serif"]]
        shutil.copy(regular, project / "serif.woff2")
        shutil.copy(italic, project / "serif-italic.woff2")
        css.append('@font-face{font-family:"Studio Serif";src:url("serif.woff2") format("woff2");font-weight:400;'
                   'font-style:normal;font-display:block}'
                   '@font-face{font-family:"Studio Serif";src:url("serif-italic.woff2") format("woff2");'
                   'font-weight:400;font-style:italic;font-display:block}')
    return css


def render_overlay(chunks: list[dict], hook: str, total: float, out: Path,
                   graphics: list[dict] = (), accent: str = "#f5a524", fmt: str = "webm", font: str | None = None,
                   paper: list[tuple[float, float]] = (), style: str | None = None, hook_italic: str | None = None,
                   end: dict | None = None, pip: dict | None = None) -> None:
    """Captions, hook and B-roll graphics as a transparent video (HyperFrames, headless Chrome):
    VP9 WebM for the draft, ProRes 4444 MOV (fmt="mov") for DaVinci Resolve. style: the title style
    (title_style; the draft's own look, draft_look). The fonts are bundled ones (caption_font, and the
    serif of the gallery look; Archivo alone in Site Notes), copied next to the page. Drawn inserts (style
    "scene") are their own layer (render_scenes), under this one; a caption over a full-screen one (paper: its
    (start, end) times) is set for the paper: on a box (classic) or in ink on a paper-coloured label (gallery).
    Site Notes has no paper (a drawing is a dark panel over the footage), so its captions stay as they are.
    hook_italic: the hook's italic word (None: from the hook's text, title_parts). end: the end card
    ({start, duration, ask, handle}, _end_card_html); no caption is shown from its start. pip: a voice-over's
    window ({side, spans}, voiceover.py): while it's up the captions and their shade move beside it."""
    style = title_style(style)
    site = style == "site"
    if site:
        paper = ()
    spans = (pip or {}).get("spans") or []
    beside = lambda a, b: f" pip{pip['side'][0]}" if any(a < y and b > x for x, y in spans) else ""  # noqa: E731
    if end:
        chunks = [{**c, "end": min(c["end"], end["start"])} for c in chunks if c["start"] < end["start"] - 0.1]
    graphics = [g for g in graphics if g.get("style") in ("card", "callout")]
    hook_plain, auto_italic = title_parts(hook)
    hook_italic = auto_italic if hook_italic is None else hook_italic
    with tempfile.TemporaryDirectory(prefix="studio-overlay-") as tmp:
        project = Path(tmp) / "overlay"
        project.mkdir()
        css = _overlay_assets(project, font, serif=not site and (style == "gallery" or bool(end)), site=site)
        clips, animations = [], []
        if hook_plain:
            # The hook gives way to the first card or callout.
            shown = min(HOOK_SECONDS_SHOWN, end["start"] if end else total, *[g["start"] for g in graphics if g["start"] > 0.8])
            attrs = f'id="hook" data-start="0" data-duration="{shown:.3f}" data-track-index="1"'
            if style in ("gallery", "site"):
                markup, anim = (_site_title("hook", attrs, 0.0, shown, "hook", hook_plain, hook_italic) if site else
                                _gallery_title("hook", attrs, 0.0, shown, hook_plain, hook_italic))
                clips.append(markup)
                animations.append(anim)
            else:
                clips.append(f'<div {attrs} class="hook clip">{html.escape(hook_plain)}</div>')
        for i, c in enumerate(chunks):
            solid = " solid" if any(c["start"] < b and c["end"] > a for a, b in paper) else (
                " lit" if c.get("lit") and style in ("gallery", "site") else "")
            clips.append(f'<div id="c{i}" class="cap{solid}{beside(c["start"], c["end"])} clip" data-start="{c["start"]:.3f}" '
                         f'data-duration="{c["end"] - c["start"]:.3f}" data-track-index="0">{_caption_html(c, style)}</div>')
            if style in ("gallery", "site"):
                animations.append(f"tl.from('#c{i}',{{opacity:0,y:6,duration:.14,ease:'power1.out'}},{c['start']:.3f});")
        if style == "gallery":
            for k, (a, b) in enumerate(_split_at(caption_shade(chunks, paper), spans)):
                clips.append(f'<div id="cs{k}" class="capscrim{beside(a + 0.01, b - 0.01)} clip" data-start="{a:.3f}" '
                             f'data-duration="{b - a:.3f}" data-track-index="20"></div>')
                fade = min(.35, (b - a) / 3)
                animations.append(f"tl.from('#cs{k}',{{opacity:0,duration:{fade:.2f},ease:'none'}},{a:.3f});"
                                  f"tl.to('#cs{k}',{{opacity:0,duration:{fade:.2f},ease:'none'}},{b - fade:.3f});")
        for i, g in enumerate(graphics):
            markup, anim = _graphic_html(i, g, style)
            clips.append(markup)
            animations.append(anim)
        if end:
            markup, anim = end_card_html(end, style)
            clips.append(markup)
            animations.append(anim)
        looks = _SITE_CSS if site else _GALLERY_CSS if style == "gallery" else _OVERLAY_CSS
        (project / "index.html").write_text(
            '<!doctype html><html><head><meta charset="utf-8"><script src="gsap.min.js"></script>'
            f"<style>{''.join(css)}{_theme_css(accent, style)}{_OVERLAY_BASE}"
            f"{looks}{end_css(style) if end else ''}"
            f"{_pip_css(pip['side'], style) if spans else ''}</style></head><body>"
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
              out: Path, words: list[dict] = (), inserts: list[tuple[Path, float]] = (),
              footage: float | None = None, voice_out: tuple[float, float] | None = None,
              under: tuple[Path, float] | None = None, effects: list[dict] = (), effects_below: float | None = None) -> dict:
    """The final draft: footage, the drawn inserts (transparent WebMs, each laid over the footage
    from its start: (file, seconds)), the caption layer on top of them, and the sound (the voice
    levelled, the music set under it, see VOICE_LUFS), all in the one encode. footage: how long the
    cut is when the draft runs on past it (the close: its last frame held under the end card);
    voice_out: (start, seconds) the shot's own sound fades out over, after the last word. The music
    fades out over the last END_FADE. under: a voice-over's site sound (a WAV on the draft's clock) and
    the gain (dB) that sets it well under the levelled voice (voiceover.SITE_BELOW), mixed in as it is.
    effects: the soft sound effects on the graphics (sfx.plan: [{file, at, seconds, duck_db, dips}]), each one's
    loudest moment effects_below dB under the loud parts of the levelled voice (effects_stem). Returns what it measured and did."""
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
        fade_voice = (f"afade=t=out:st={voice_out[0]:.3f}:d={voice_out[1]:.3f},"
                      if voice_out and voice_out[1] >= 0.05 else "")
        graph = [f"[0:a]volume={voice_gain:.2f}dB,{_VOICE_SQUEEZE},volume={makeup:.2f}dB,{_VOICE_LIMIT},{fade_voice}"
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
            fade = min(END_FADE, total / 3)
            args += ["-i", str(bed_track)]
            graph.append(f"[1:a]asetnsamples=n=256,volume='{_duck_expr(spans, music_gain, depth)}':eval=frame,"
                         f"afade=t=in:d=0.4,afade=t=out:st={max(0.0, total - fade):.3f}:d={fade:.3f}[m]")
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
        if effects:
            fx_raw = work / "sfx.f32"
            report["sfx"] = effects_stem(effects, voice_raw, list(words), total, effects_below, fx_raw)
        sound_in = [*raw, "-i", str(voice_raw)] + ([*raw, "-i", str(music_raw)] if music_file else [])
        if music_file:
            mix = ((f"[1:a]volume=-{lowered}dB[ml];[0:a][ml]" if lowered else "[0:a][1:a]")
                   + "amix=inputs=2:duration=first:normalize=0")
        else:
            mix = "[0:a]anull"
        if under:  # the site's own sound, low under everything
            k = 2 if music_file else 1
            sound_in += ["-i", str(under[0])]
            mix = (mix + f"[pre];[{k}:a]aresample={RATE},aformat=channel_layouts=stereo,volume={under[1]:.2f}dB,"
                   f"apad=whole_len={round(total * RATE)},atrim=end_sample={round(total * RATE)}[site];"
                   "[pre][site]amix=inputs=2:duration=first:normalize=0")
            report["site_gain_db"] = under[1]
        if effects:  # the sound effects, well under everything
            k = 1 + bool(music_file) + bool(under)
            sound_in += [*raw, "-i", str(fx_raw)]
            down = report["sfx"]["lowered_db"]
            mix = (mix + f"[pre_fx];[{k}:a]" + (f"volume=-{down}dB" if down else "anull")
                   + "[fx];[pre_fx][fx]amix=inputs=2:duration=first:normalize=0")
        mixed = work / "mix.wav"
        extract._run([settings.FFMPEG, "-y", "-v", "error", *sound_in, "-filter_complex", mix + "[a]", "-map", "[a]",
                      "-c:a", "pcm_f32le", str(mixed)])
        measure = _measure_loudnorm(["-i", str(mixed)])
        report["mix_lufs_before"] = float(measure["input_i"])

        args = [settings.FFMPEG, "-y", "-v", "error", "-i", str(cut)]
        graph, video, n, below = [], "0:v", 0, "[0:v]"
        if footage is not None and total - footage > 0.5 / FPS:  # the close: the last frame held to the end
            graph.append(f"[0:v]tpad=stop_mode=clone:stop_duration={total - footage + 0.1:.3f}[base]")
            video = below = "[base]"
        for k, (path, at) in enumerate(inserts):  # each drawn insert from its moment, then the footage again
            n += 1
            args += ["-c:v", "libvpx-vp9", "-i", str(path)]  # libvpx keeps the alpha channel
            graph.append(f"[{n}:v]setpts=PTS-STARTPTS+{at:.3f}/TB[s{k}];"
                         f"{below}[s{k}]overlay=0:0:format=auto:eof_action=pass[b{k}]")
            below = video = f"[b{k}]"
        if overlay:
            n += 1
            args += ["-c:v", "libvpx-vp9", "-i", str(overlay)]
            graph.append(f"{below}[{n}:v]overlay=0:0:format=auto:shortest=1[v]")
            video = "[v]"
        args += ["-i", str(mixed)]
        graph.append(f"[{n + 1}:a]{_loudnorm_args(measure)},aresample={RATE}[a]")
        args += ["-filter_complex", ";".join(graph), "-map", video, "-map", "[a]",
                 "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-profile:v", "high", "-pix_fmt", "yuv420p",
                 "-r", str(FPS), "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart",
                 "-t", f"{total:.3f}", str(out)]
        extract._run(args)
    return report


VOICE_PEAK_DB = -10.0  # the loud parts of a voice levelled to VOICE_LUFS (K-weighted, 50 ms), when there's no voice
EFFECT_WINDOW = 0.05


def _window_db(x, rate: int = RATE, window: float = EFFECT_WINDOW):
    """Level (dB) of each `window` of samples, a new one every 10 ms."""
    import numpy as np
    k, hop = int(window * rate), int(0.01 * rate)
    if len(x) < k:
        return np.array([_db(x)])
    c = np.concatenate([[0.0], np.cumsum(np.square(x, dtype="float64"))])
    starts = np.arange(0, len(x) - k + 1, hop)
    power = (c[starts + k] - c[starts]) / k
    return 10 * np.log10(np.maximum(power, 1e-12))


EFFECT_RAMP = 0.03  # an effect's dip under a loud word comes in and goes out over this long


def _dip_expr(dips: list, gain_db: float, ramp: float = EFFECT_RAMP) -> str:
    """ffmpeg volume expression of t: gain_db, and sfx.DIP_DB lower over each dip (from, to: a word's loudest part,
    on the draft's clock), with short ramps."""
    import sfx
    if not dips:
        return f"{10 ** (gain_db / 20):.6f}"
    parts = [f"clip(min((t-{a - ramp:.3f})/{ramp},({b + ramp:.3f}-t)/{ramp}),0,1)" for a, b in dips]
    while len(parts) > 1:
        parts = [f"max({x},{y})" if y else x for x, y in itertools.zip_longest(parts[::2], parts[1::2])]
    return f"pow(10,({gain_db:.2f}-{sfx.DIP_DB:.2f}*{parts[0]})/20)"


def voice_peak_db(voice, words: list[dict], rate: int = RATE) -> float | None:
    """The loud parts of his voice: the 95th percentile of its 50 ms levels while he talks (K-weighted)."""
    import numpy as np
    v = _read_raw(voice) if isinstance(voice, Path) else voice
    parts = [v[int(w["start"] * rate):int(w["end"] * rate)] for w in words]
    levels = np.concatenate([_window_db(p, rate) for p in parts if len(p)]) if any(len(p) for p in parts) else []
    levels = [x for x in levels if x > -70]
    return round(float(np.percentile(levels, 95)), 1) if levels else None


def effects_stem(effects: list[dict], voice_raw: Path, words: list[dict], total: float, below: float | None,
                 out: Path) -> dict:
    """The sound effects on the draft's clock as one raw stem: each from its moment, a short fade in and out, its
    loudest 50 ms set `below` dB (settings.SFX_LEVEL_DB) under the loud parts of the levelled voice (voice_peak_db),
    plus its own duck (sfx.DUCK_DB over a word) and its dips (sfx.DIP_DB more over a word's loudest part). Measured: the guard that can't regress turns them all down
    when the loudest came out closer to the voice than asked (lowered_db, applied in the mix)."""
    import numpy as np
    below = settings.SFX_LEVEL_DB if below is None else below
    voice = voice_peak_db(voice_raw, words) if words else None
    ref = voice if voice is not None else VOICE_PEAK_DB
    peaks = {}
    for f in dict.fromkeys(str(e["file"]) for e in effects):
        tmp = out.with_name(f"fx_{len(peaks)}.f32")
        extract._run([settings.FFMPEG, "-y", "-v", "error", "-i", f, "-af", f"aresample={RATE},aformat=channel_layouts=stereo",
                      "-f", "f32le", str(tmp)])
        peaks[f] = float(_window_db(_read_raw(tmp)).max())
        tmp.unlink(missing_ok=True)
    args, graph = [settings.FFMPEG, "-y", "-v", "error"], []
    for i, e in enumerate(effects):
        sec = max(float(e["seconds"]), 0.05)
        fade = round(min(0.15, sec * 0.4), 3)
        gain = ref - below - float(e.get("duck_db") or 0) - peaks[str(e["file"])]
        ms = max(0, round(e["at"] * 1000))
        args += ["-i", str(e["file"])]
        graph.append(f"[{i}:a]aresample={RATE},aformat=channel_layouts=stereo,atrim=end={sec:.3f},afade=t=in:d=0.01,"
                     f"afade=t=out:st={sec - fade:.3f}:d={fade:.3f},adelay={ms}:all=1,asetnsamples=n=256,"
                     f"volume='{_dip_expr(e.get('dips') or [], gain)}':eval=frame[e{i}]")
    graph.append("".join(f"[e{i}]" for i in range(len(effects))) + f"amix=inputs={len(effects)}:duration=longest:"
                 f"normalize=0,apad=whole_len={round(total * RATE)},atrim=end_sample={round(total * RATE)}[fx]")
    extract._run(args + ["-filter_complex", ";".join(graph), "-map", "[fx]", "-f", "f32le", "-ar", str(RATE), "-ac", "2",
                         str(out)])
    stem = _read_raw(out)
    for e in effects:  # measured where it plays at its own level (not over a word's loudest part, where it dips)
        for a, b in e.get("dips") or []:
            stem[max(0, int((a - EFFECT_RAMP) * RATE)):int((b + EFFECT_RAMP) * RATE)] = 0.0
    loudest = max(float(_window_db(stem[int(e["at"] * RATE):int((e["at"] + e["seconds"]) * RATE) + 1]).max())
                  + float(e.get("duck_db") or 0) for e in effects)
    lowered = round(max(0.0, below - (ref - loudest)), 1) if ref - loudest < below - 1.0 else 0.0
    return {"count": len(effects), "voice_peak_db": voice, "effect_peak_db": round(loudest - lowered, 1),
            "below_db": round(ref - loudest + lowered, 1), "asked_below_db": below, "lowered_db": lowered}


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
    """Re-transcribe the draft and compare with the words the cut was meant to keep. What was heard,
    with its times ("heard"), is kept for Studio's listen-back (final_check.py)."""
    import transcribe
    wav = extract.extract_audio(final, work / "check.wav")
    said = transcribe.transcribe(wav)["words"]
    kept = [{"word": w.get("word", ""), "start": w.get("start"), "end": w.get("end")} for w in said][:3000]
    heard = [_norm(w["word"]) for w in said]
    want = [_norm(w["word"]) for w in expected]
    heard, want = [w for w in heard if w], [w for w in want if w]
    if not want:
        return {"words_expected": 0, "match": None, "missing": [], "heard": kept}
    matcher = difflib.SequenceMatcher(a=want, b=heard, autojunk=False)
    missing = [want[i] for tag, i1, i2, _, _ in matcher.get_opcodes() if tag in ("delete", "replace")
               for i in range(i1, i2)]
    return {"words_expected": len(want), "match": round(matcher.ratio(), 3), "missing": missing[:20], "heard": kept}


def _sound_ends(cuts: list[dict]) -> list[tuple[int, float]]:
    """(piece, where its sound stops in its clip, as rendered: frame-rounded) for every piece whose
    sound stops there in the draft: each join, and the draft's end. A piece that carries straight
    on into the next isn't a join."""
    frames, _, _, source = _frame_plan(cuts)
    out = []
    for k, c in enumerate(cuts):
        end = source[k] + frames[k] / FPS
        if k + 1 < len(cuts) and cuts[k + 1]["clip_id"] == c["clip_id"] and abs(end - source[k + 1]) < 1e-6:
            continue
        out.append((k, end))
    return out


def _word_at(words: list[dict], t: float) -> dict | None:
    """The word sounding at t (or the last one that started before it)."""
    return max((w for w in words if w["start"] < t), key=lambda w: w["start"], default=None)


def clipped_words(cuts: list[dict], clips: dict[int, dict]) -> list[dict]:
    """The clipped-word check: at each join (and the draft's end), is the voice still going in the
    last voice_edges.CLIP_WINDOW before the cut, in the clip's own sound? Then a word was cut short.
    Returns [{piece, end (clip seconds), at (draft seconds), word}]."""
    frames = _frame_plan(cuts)[0]
    out = []
    for k, end in _sound_ends(cuts):
        clip = clips.get(cuts[k]["clip_id"])
        v = voice_edges.voice(clip) if clip else None
        if not voice_edges.clipped(v, end):
            continue
        w = _word_at(_words(clip), end)
        out.append({"piece": k, "end": round(end, 3), "at": round(sum(frames[:k + 1]) / FPS, 2),
                    "word": w["word"].strip() if w else ""})
    return out


def unclip(cuts: list[dict], clips: dict[int, dict], found: list[dict]) -> tuple[list[dict], int]:
    """Each clipped end (clipped_words) moved on to where the voice really stops, never into the
    next word and never into another piece of the same clip. Returns the cuts and how many moved."""
    cuts, moved = [dict(c) for c in cuts], 0
    for f in found:
        c = cuts[f["piece"]]
        clip = clips[c["clip_id"]]
        v, words, end = voice_edges.voice(clip), _words(clip), f["end"]
        w = _word_at(words, end)
        nxt = next((x for x in words if w is not None and x["start"] > w["start"]), None)
        limit = min([clip.get("duration") or v.duration]
                    + [o["start"] for o in cuts if o is not c and o["clip_id"] == c["clip_id"] and o["start"] >= end])
        new = voice_edges.end_point(v, max(end, w["end"]) if w else end, end,
                                    next_start=nxt["start"] if nxt and nxt["start"] > end else None, limit=limit)
        new = min(new + 1 / FPS, limit)  # a frame's margin: the render rounds each piece to whole frames
        if new > c["end"] + 0.005:
            c["end"] = round(new, 3)
            moved += 1
    return cuts, moved


def _mmss(t: float) -> str:
    return f"{int(t // 60)}:{int(t % 60):02d}"


def check_loudness(final: Path) -> dict:
    result = extract._run([settings.FFMPEG, "-hide_banner", "-nostats", "-i", str(final),
                           "-af", "ebur128=peak=true", "-f", "null", "-"])
    text = result.stderr
    lufs = re.findall(r"I:\s+(-?[\d.]+) LUFS", text)
    peak = re.findall(r"Peak:\s+(-?[\d.]+) dBFS", text)
    return {"lufs": float(lufs[-1]) if lufs else None, "true_peak": float(peak[-1]) if peak else None}


# ── Orchestration ─────────────────────────────────────────────────────────────

def _broll_ids(edl: dict) -> list[int]:
    """The clips a voice-over lays over the voice (not in its cut list)."""
    voice = {c["clip_id"] for c in edl["cuts"]}
    return list(dict.fromkeys(b["clip_id"] for b in edl.get("broll") or [] if b.get("clip_id") not in voice))


def _clips_for(conn, ids: list[int]) -> dict[int, dict]:
    import onedrive
    onedrive.ensure_originals(conn, ids)  # originals the clean-up freed come back from OneDrive first
    rows = conn.execute(f"SELECT * FROM clips WHERE id IN ({','.join('?' * len(ids))})", ids).fetchall() if ids else []
    return {r["id"]: dict(r) for r in rows if Path(r["video_path"]).exists()}


def edit(conn, production_id: int, client=None, find_track=None) -> Path:
    """Make the next draft of a production. Returns the rendered file. Studio's read, its own
    fix rounds (self_fix.py) and the move to To review follow."""
    production = dict(conn.execute("SELECT * FROM productions WHERE id = ?", (production_id,)).fetchone())
    import transcript_edit
    if transcript_edit.pending(production):  # Review's Edit words: the same cut with the right captions, no Claude
        return transcript_edit.render(conn, production_id, find_track)
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

    import restyle
    fresh = production.get("restyle") == restyle.FRESH  # start over: planned as if it were the first draft
    edl = plan_edit(conn, production, clips, client, fresh=fresh)
    edl["cuts"] = _cut(edl, clips, client, f"production:{production_id}", [n["body"] for n in notes])
    _keep_corrections(edl, production, clips if fresh else None)
    _keep_picture(edl, production, clips, notes, fresh)
    # A note asking for an image, picture or photo of something makes that insert a picture at that moment,
    # whatever the plan did (pictures.py; a drawing still stands in if the picture can't be made).
    import pictures
    previous = json.loads(production.get("edl") or "{}")
    if notes and previous.get("cuts"):
        edl["picture_asks"] = pictures.apply_asks(edl, pictures.asks_from_notes(notes, previous))
    # A note on the sound effects ("no sound effects", "the whoosh is too loud") holds for this post from then on.
    if settings.SFX:
        import sfx
        pref, did = sfx.pref_from_notes(notes, previous.get("sfx_pref"))
        if did or previous.get("sfx_pref"):
            edl["sfx_pref"] = pref
        if did:
            edl["sfx_asks"] = did
    if fresh:
        edl["fresh"] = True
    _render(conn, production, clips, edl, plan, version, find_track, client=client)
    if notes:  # Bryan's editing notes become rules Studio keeps (learning.py)
        import learning
        learning.from_notes(conn, notes, edl.get("notes_addressed", []), "reel")
    import self_fix
    return Path(self_fix.deliver(conn, production_id, client, find_track)["video_path"])


def _cut(edl: dict, clips: dict[int, dict], client=None, ref: str = "", asked: list[str] = ()) -> list[dict]:
    """Claude's cut list made exact: snapped to the gaps between words (and the voice's real
    edges), no sentence broken (join_guard; client and ref for its one question to Claude, asked:
    the notes or fixes this edit was asked for), Studio's checks on the ends (sentence_guard),
    then jump cuts and framing. What the checks changed goes in edl["checks"]."""
    cuts = [s for s in (snap(c, clips[c["clip_id"]]) for c in edl["cuts"] if c["clip_id"] in clips) if s]
    if not cuts:
        raise RuntimeError("the edit plan kept no usable footage")
    cuts, restored = join_guard(cuts, clips, client, ref, asked)
    if not asked:  # a cut Bryan's notes or Studio's fixes asked for is theirs (join_guard asks Claude about those)
        cuts, more = whole_ends(cuts, clips)
        restored += more
    cuts, edl["checks"] = sentence_guard(cuts, clips)
    cuts = finish_cuts(cuts, clips, edl)
    edl["checks"] = _timed_notes(restored, cuts) + edl["checks"]
    return cuts


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
    return on_frames(cuts, clips)


def _frame_up(t: float) -> float:
    return math.ceil(round(t * FPS, 6)) / FPS


def _frame_down(t: float) -> float:
    return math.floor(round(t * FPS, 6)) / FPS


def on_frames(cuts: list[dict], clips: dict[int, dict]) -> list[dict]:
    """Every cut point where the sound jumps (each join, the draft's start and end) put on its
    clip's frame grid, so the render cuts the sound exactly there: render_cut makes each piece
    whole frames, which otherwise moved a cut by up to a frame (into a word, at a tight join). An
    end moves on to the next frame and a start back to the one before, unless that reaches into
    the next (or previous) word's sound; then the other way. A clip with no known sound (B-roll)
    goes to the nearest frame, so the pieces after it stay on the grid too."""
    out = [dict(c) for c in cuts]
    carry = [k > 0 and cuts[k - 1]["clip_id"] == c["clip_id"] and abs(cuts[k - 1]["end"] - c["start"]) < 0.002
             for k, c in enumerate(cuts)]
    for k, c in enumerate(out):
        clip = clips.get(c["clip_id"])
        v = voice_edges.voice(clip) if clip else None
        start, end = cuts[k]["start"], cuts[k]["end"]
        if v is None:
            if not carry[k]:
                c["start"] = round(round(start * FPS) / FPS, 4)
            if not (k + 1 < len(cuts) and carry[k + 1]):
                c["end"] = round(round(end * FPS) / FPS, 4)
            if c["end"] - c["start"] < 1 / FPS:
                c["start"], c["end"] = start, end
            continue
        words = _words(clip)
        if not carry[k]:
            prev = max((w for w in words if w["end"] <= start + 0.005), key=lambda w: w["end"], default=None)
            floor = v.quiet_from(prev["end"], max(prev["end"], start)) if prev else 0.0
            down = _frame_down(start)
            if floor is None:  # the voice runs on into this start (a tight join): the nearest frame
                c["start"] = round(round(start * FPS) / FPS, 4)
            else:
                c["start"] = round(down if down >= floor - 0.001 else _frame_up(start), 4)
        if not (k + 1 < len(cuts) and carry[k + 1]):
            nxt = min((w for w in words if w["start"] >= end - 0.005), key=lambda w: w["start"], default=None)
            cap = v.sound_from(nxt["start"], lo=end) if nxt else (clip.get("duration") or v.duration)
            up = _frame_up(end)
            if cap is None:  # the voice runs on from this end into the next word: the nearest frame
                c["end"] = round(round(end * FPS) / FPS, 4)
            else:
                c["end"] = round(up if up <= cap + 0.001 else _frame_down(end), 4)
        if c["end"] - c["start"] < 1 / FPS:  # never a piece of nothing
            c["start"], c["end"] = cuts[k]["start"], cuts[k]["end"]
    return out


# ── The close ─────────────────────────────────────────────────────────────────
# Bryan, 2026-10-21: "It also ends the videos abruptly (mid sentence/animation) without a proper
# transition out or close." Every Reel now ends on a complete sentence (sentence_guard), then a
# short beat on the last shot (END_TAIL of footage after the last word's sound: the shot's own tail
# when there is footage after it, else its last frame held), then the Urban Makers end card
# (END_CARD_SECONDS, settings.END_CARD) while the music fades out (END_FADE). No card, callout or drawn
# animation runs within END_CLEAR of the close, and no caption is on screen over the card.
END_TAIL, END_TAIL_MIN = 0.8, 0.6
END_CARD_SECONDS = 1.8
END_FADE = 1.2
END_CLEAR = 1.0
END_ASK_CHARS = 60


def close_ending(cuts: list[dict], clips: dict[int, dict]) -> tuple[list[dict], dict]:
    """A short beat after the last word: the last shot runs on to END_TAIL past where the voice
    really stops (never into the next word's sound, never into another piece of the same clip, never
    past the clip's end); silent pictures after the last speech count towards it. What footage can't
    give is held on the last frame ("hold", seconds). Returns the cuts and {tail (seconds of footage
    after the last word), hold, speech_end (the last word's sound end on the draft's clock),
    added (seconds of footage the beat added)}. Running it again on its own result changes nothing."""
    cuts = [dict(c) for c in cuts]
    spoken = [i for i, c in enumerate(cuts) if c["clip_id"] in clips and _inside(c, _words(clips[c["clip_id"]]))]
    if not cuts or not spoken:
        return cuts, {"tail": 0.0, "hold": 0.0, "speech_end": None, "added": 0.0}
    k = spoken[-1]
    c, clip = cuts[k], clips[cuts[k]["clip_id"]]
    words = _words(clip)
    last = _inside(c, words)[-1]
    nxt = next((w for w in words if w["start"] > last["start"] and w is not last), None)
    v = voice_edges.voice(clip)
    dur = clip.get("duration") or (v.duration if v else c["end"])
    sound_end = min(max(last["end"], voice_edges.end_point(v, last["end"], last["end"],
                                                           next_start=nxt["start"] if nxt else None, limit=dur)), c["end"])
    offset = sum(x["end"] - x["start"] for x in cuts[:k])
    after = sum(x["end"] - x["start"] for x in cuts[k + 1:])
    have = c["end"] - sound_end + after
    added = 0.0
    if have < END_TAIL_MIN - 0.001:
        piece = cuts[-1]
        pclip = clips.get(piece["clip_id"]) or {}
        pdur = pclip.get("duration") or piece["end"]
        cap = min([pdur] + [o["start"] for o in cuts if o is not piece and o["clip_id"] == piece["clip_id"]
                            and o["start"] >= piece["end"] - 0.001])
        if piece is c and nxt is not None:  # never into the next word's sound
            onset = v.sound_from(nxt["start"], lo=last["end"]) if v else None
            cap = min(cap, (onset if onset is not None else nxt["start"]) - 0.05)
        want = piece["end"] + (END_TAIL - have)
        new = _frame_down(min(want, cap))
        if new > piece["end"] + 0.01:
            added = round(new - piece["end"], 3)
            piece["end"] = round(new, 4)
            have += added
    hold = round(END_TAIL - have, 3) if have < END_TAIL_MIN - 0.001 else 0.0
    return cuts, {"tail": round(max(have, 0.0), 3), "hold": max(hold, 0.0),
                  "speech_end": round(offset + sound_end - c["start"], 3), "added": added}


def end_ask(plan: dict) -> str:
    """The end card's ask: the plan's own call to action, short (its first sentence, at most
    END_ASK_CHARS), never a price or a promotion; "" when there is none. Never invented."""
    text = unmark(plan.get("cta") or "")
    text = re.split(r"(?<=[.!?])\s+", text)[0].strip().rstrip(".").strip()
    if not text or len(text) > END_ASK_CHARS or motion._PRICE.search(text):
        return ""
    text = wording.tidy(text)
    return text[0].upper() + text[1:] if text else ""


def end_card(production: dict, plan: dict) -> dict | None:
    """The end card for this draft (settings.END_CARD, Urban Makers posts): {seconds, ask, handle}."""
    if not settings.END_CARD or production.get("brand") != "urban_makers":
        return None
    return {"seconds": END_CARD_SECONDS, "ask": end_ask(plan), "handle": motion.default_handle()}


def clear_of_end(graphics: list[dict], limit: float) -> tuple[list[dict], list[str]]:
    """No card, callout or drawn animation runs past `limit` (END_CLEAR before the close): one that
    would is shortened when it still has its time (a card 1 s, a drawing SCENE_MIN), else moved
    earlier when there's room, else left out. Returns the graphics and, in plain words, what changed."""
    out, notes = [], []
    for g in sorted(graphics, key=lambda g: g.get("start", 0)):
        if "start" not in g or g["start"] + g["duration"] <= limit + 0.001:
            out.append(g)
            continue
        scene = g.get("style") in ("scene", "picture")
        shortest, earliest = (SCENE_MIN, SCENE_CLEAR) if scene else (1.0, 2.5)
        kind = {"scene": "drawn animation", "picture": "picture"}.get(g.get("style"), g.get("style", "card"))
        name = f"the {kind} \"{g.get('title', '')}\""
        if limit - g["start"] >= shortest:
            out.append({**g, "duration": round(limit - g["start"], 3)})
            notes.append(f"ended {name} before the close")
            continue
        start = limit - g["duration"]
        if start >= earliest and not any(start < o["start"] + o["duration"] + 0.3 and limit > o["start"] - 0.3 for o in out):
            out.append({**g, "start": round(start, 3)})
            notes.append(f"moved {name} earlier so it finishes before the close")
            continue
        notes.append(f"left out {name}: it would have run into the close")
    return sorted(out, key=lambda g: g.get("start", 0)), notes


def music_brief(music_plan: dict, chosen: dict | None = None) -> str:
    """What the music search is asked for: exactly the track Bryan linked or the team picked, else the
    plan's mood, search words and tempo (music.find_track adds his taste)."""
    if chosen and chosen.get("url"):
        return (f"Use exactly the track Bryan linked: {chosen['url']} (Epidemic Sound track id "
                f"{chosen['track_id']}). Look it up by that id or link; don't swap in another track")
    if chosen:
        return (f"Use exactly this track the team picked: \"{chosen['title']}\" by {chosen['artist']} "
                f"(recording id {chosen['recording_id']})")
    return f"{music_plan['mood']}; search: {music_plan['search']}; about {music_plan['tempo_bpm']:.0f} BPM"


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
    clips = _clips_for(conn, ids + _broll_ids(edl))
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
                             + [s["clip_id"] for s in plan.get("segments", [])]
                             + [b["clip_id"] for b in previous.get("broll") or []]))
    clips = _clips_for(conn, ids)
    if not clips:
        raise RuntimeError("none of the draft's clips are in the library any more")
    edl = plan_edit(conn, production, clips, client, fixes=fixes, ref=self_fix.ref(production_id))
    edl["cuts"] = _cut(edl, clips, client, self_fix.ref(production_id), [f["body"] for f in fixes])
    if previous.get("look"):
        edl["look"] = previous["look"]
    if previous.get("sfx_pref"):
        edl["sfx_pref"] = previous["sfx_pref"]
    _keep_corrections(edl, production)
    _keep_picture(edl, production, clips)
    find_track, plan = _same_music(production, plan, find_track)
    return _render(conn, production, clips, edl, plan, production["version"], find_track,
                   suffix=f"_fix{round_no}", address_notes=False, client=client, ref=self_fix.ref(production_id))


def _keep_corrections(edl: dict, production: dict, clips: dict[int, dict] | None = None) -> None:
    """A new edit plan keeps the caption corrections the draft before had (they're tied to the
    clips' words, not the cut) and the words the spell-check already looked at. clips (a start
    over): only corrections whose word is still in its clip's transcript are kept."""
    previous = json.loads(production.get("edl") or "{}")
    kept = previous.get("corrections") or []
    if clips is not None:
        said = {(cid, f"{w['start']:.2f}") for cid, c in clips.items() for w in _words(c)}
        kept = [f for f in kept if isinstance(f, dict) and (_int(f.get("clip_id")), _at(f.get("at"))) in said]
    if kept and not edl.get("corrections"):
        edl["corrections"] = kept
    if previous.get("spell") and not edl.get("spell"):
        edl["spell"] = previous["spell"]


def _keep_picture(edl: dict, production: dict, clips: dict[int, dict], notes: list[dict] = (),
                  fresh: bool = False) -> None:
    """A voice-over's new plan keeps what was asked of the picture before (full screen here, the window's side),
    unless it starts over, and Bryan's notes asking for it now ("full screen here", "move me to the left") are
    made by code as well as by the plan (voiceover.asks_from_notes)."""
    if not (edl.get("voiceover") or {}).get("on"):
        return
    import voiceover
    previous = json.loads(production.get("edl") or "{}")
    if not fresh:
        voiceover.keep_asks(edl, previous)
    asks, side, did = voiceover.asks_from_notes(notes, previous, clips)
    if asks:
        edl["pip_asks"] = (edl.get("pip_asks") or []) + [a for a in asks if not any(
            voiceover._same_ask(a, o) for o in edl.get("pip_asks") or [])]
    if side:
        edl["pip_side"] = side
    if did:
        edl["pip_done"] = did


def _int(v) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _at(v) -> str:
    try:
        return f"{float(v):.2f}"
    except (TypeError, ValueError):
        return ""


def self_patch(conn, production_id: int, actions: list[dict], round_no: int, client=None,
               find_track=None) -> tuple[Path | None, list[dict]]:
    """One of Studio's own fix rounds made as patches (fix_actions.apply): the read's actions on the
    draft's own edit plan, no new plan, rendered as the same version (a file of its own) with every
    code check. Returns the render (None when no action could be made) and each action's result."""
    import fix_actions
    import self_fix
    production = dict(conn.execute("SELECT * FROM productions WHERE id = ?", (production_id,)).fetchone())
    edl = json.loads(production["edl"] or "{}")
    if not edl.get("cuts"):
        raise RuntimeError("this draft has no edit to fix")
    ids = list(dict.fromkeys(c["clip_id"] for c in edl["cuts"]))
    clips = _clips_for(conn, ids + _broll_ids(edl))
    if set(ids) - set(clips):
        raise RuntimeError("some of the draft's clips are no longer in the library")
    new, results = fix_actions.apply(edl, clips, actions)
    if not any(r.get("ok") for r in results):
        return None, results
    find_track, plan = _same_music(production, json.loads(production["plan"] or "{}"), find_track)
    final = _render(conn, production, clips, new, plan, production["version"], find_track, placed=True,
                    suffix=f"_fix{round_no}", address_notes=False, client=client, ref=self_fix.ref(production_id))
    return final, results


def _render(conn, production: dict, clips: dict, edl: dict, plan: dict, version: int, find_track=None,
            placed: bool = False, suffix: str = "", address_notes: bool = True, client=None,
            ref: str | None = None, spell: bool = True) -> Path:
    """Render an edit plan (EDL) to the next draft: cut, captions, graphics, music, QA. The
    draft is stored but stays 'producing': self_fix.deliver sends it to To review. client and ref:
    for the caption spell-check (spelling.py; ref is where its cost is recorded). spell=False: no
    spell-check (Review's Edit words: the captions are as Bryan corrected them, and nothing is spent)."""
    production_id = production["id"]
    # What Studio's own checks changed: editing problems (counted in Review's "caught" line) and
    # sound fixes (the recording's level, not an editing mistake).
    checks = [{"did": c, "counted": True} for c in edl.get("checks") or []]
    checks += [{"did": c, "counted": False} for c in edl.pop("picture_asks", None) or []]  # Bryan's asks, not slips
    checks += [{"did": c, "counted": False} for c in edl.pop("sfx_asks", None) or []]
    # Captions say what Bryan meant: the draft's corrections, and the spell-check's (words it
    # already checked in this draft aren't sent again).
    import spelling
    cuts = edl["cuts"] = attach_corrections(edl["cuts"], edl.get("corrections"))
    spelled = spelling.run(edl, cuts, clips, production, plan, client, ref or f"production:{production_id}") \
        if spell else {"fixed": [], "usd": 0.0}
    edl["spell_usd"] = spelled["usd"]  # this render's (self_fix.deliver counts the first one in its cap)
    if spelled["fixed"]:
        cuts = edl["cuts"] = attach_corrections(cuts, edl.get("corrections"))
        shown = ", ".join(f"\"{f['to']}\"" for f in spelled["fixed"][:3])
        n = len(spelled["fixed"])
        checks.append({"did": f"corrected {n} caption word{'s' if n != 1 else ''} Whisper mis-heard ({shown})",
                       "counted": True})
    RENDERS.mkdir(parents=True, exist_ok=True)
    final = RENDERS / f"production_{production_id}_v{version}{suffix}.mp4"
    # The close: a short beat on the last shot after the last word, then the end card (close_ending).
    cuts, close = close_ending(cuts, clips)
    edl["cuts"] = cuts
    card = end_card(production, plan)
    with tempfile.TemporaryDirectory(prefix="studio-edit-") as tmp:
        work = Path(tmp)
        footage = render_cut(cuts, clips, work / "cut.mp4")
        # The clipped-word check: the voice still going right before a join means a word was cut
        # short. Each is moved on to where the voice stops and the cut rendered again, once.
        clipped = clipped_words(cuts, clips)
        still = clipped
        if clipped:
            fixed_cuts, moved = unclip(cuts, clips, clipped)
            if moved:
                cuts = edl["cuts"] = attach_corrections(fixed_cuts, edl.get("corrections"))
                footage = render_cut(cuts, clips, work / "cut.mp4")
                still = clipped_words(cuts, clips)
                close = {**close_ending(cuts, clips)[1], "added": close["added"]}
        left = {f["piece"] for f in still}
        for f in clipped:
            if f["piece"] not in left:
                checks.append({"did": f"kept the end of \"{f['word']}\" at {_mmss(f['at'])} (the cut was clipping it)",
                               "counted": True})
        for f in still:
            checks.append({"did": f"the end of \"{f['word']}\" at {_mmss(f['at'])} may still sound cut short",
                           "counted": False})
        joins = {"checked": len(_sound_ends(cuts)), "clipped": len(clipped), "fixed": len(clipped) - len(still),
                 "still": [{k: f[k] for k in ("at", "word")} for f in still]}
        # The draft's clock: the footage, its last frame held when the shot had no beat after the last
        # word, then the end card.
        close_at = round(footage + close["hold"], 3)
        total = round(close_at + (card["seconds"] if card else 0.0), 3)
        edl["close"] = {**close, "at": close_at, "card": card}
        if close["added"] >= 0.1 or close["hold"]:
            checks.append({"did": "gave the ending a short beat on the last shot" + (
                f" (its last frame held {close['hold']:.1f}s)" if close["hold"] else "") + (
                " before the end card" if card else ""), "counted": False})
        clear_by = max(close_at - END_CLEAR, 0.0)  # cards and drawings finish this long before the close
        words = timeline(cuts, clips, sound=True)
        chunks = caption_chunks(words, close_at)
        overlay = None
        # A voice-over: the site footage over his voice, him in a window (voiceover.py). His cut stays the voice.
        picture, pip = work / "cut.mp4", None
        if (edl.get("voiceover") or {}).get("on"):
            import voiceover
            try:
                made, pip, notes = voiceover.render_picture(edl, cuts, clips, words, footage,
                                                            close.get("speech_end") or footage, work, plan)
            except Exception as e:  # a draft with him full screen beats no draft
                log.warning("Couldn't lay the site footage over production %s: %s: %s", production_id,
                            type(e).__name__, e)
                made, pip, notes = None, None, ["kept you full screen all through: the site footage couldn't be laid in"]
            checks += [{"did": n, "counted": False} for n in (edl.pop("pip_done", None) or []) + notes]
            picture = made or picture
        edl.pop("pip_notes", None)
        if pip:
            edl["pip"] = pip
        else:
            edl.pop("pip", None)
            if (edl.get("voiceover") or {}).get("on"):
                edl["pip_notes"] = notes or ["kept you full screen all through"]
        window = {"side": pip["side"], "spans": pip["spans"]} if pip and pip["spans"] else None
        # The look: a new plan takes today's settings; a re-render keeps the draft's own (draft_look).
        look = edl["look"] = draft_look(edl) if placed or edl.get("look") else look_now()
        import pictures
        if placed:  # a re-render: the graphics are where the draft had them
            cards = [g for g in edl.get("graphics", []) if g.get("style") not in ("scene", "picture")]
            scenes = [g for g in edl.get("graphics", []) if g.get("style") == "scene"]
            pics = [g for g in edl.get("graphics", []) if g.get("style") == "picture"]
            scene_notes, pic_notes = [], []
            if scenes and scene_cap(total) <= 0:
                scene_notes = [f"left out {len(scenes)} drawn animation{'s' if len(scenes) != 1 else ''} "
                               "(they're turned off)"]
                scenes = []
        else:
            cards = place_graphics(edl.get("graphics", []), cuts, clear_by)
            scenes, scene_notes = place_scenes(edl.get("graphics", []), cuts, clips, clear_by + 0.2, words,
                                               edl.get("hook_text", ""), cards)
            # Pictures (pictures.py) where a real-looking thing explains it better; with the drawings, about one
            # insert per pictures.INSERTS_EVERY seconds.
            pics, pic_notes = pictures.place(edl.get("graphics", []), cuts, clips, clear_by + 0.2, words,
                                             edl.get("hook_text", ""), cards, scenes,
                                             pictures.inserts_cap(clear_by + 0.2) - len(scenes))
        cards, moved = clear_of_end(cards, clear_by)
        scenes, more = clear_of_end(scenes, clear_by)
        pics, also = clear_of_end(pics, clear_by)
        checks += [{"did": n, "counted": False} for n in moved + more + also]
        if window:  # a panel over the top of the picture would cover the top of his window: full screen instead
            for g in scenes + pics:
                if g.get("look") == "panel" and any(g["start"] < b and g["start"] + g["duration"] > a
                                                    for a, b in window["spans"]):
                    g["look"], g["look_changed"] = "cutaway", "a panel would have covered your window"
        # The drawn inserts and pictures are drawn in the draft's look (Site Notes: dark panels, Archivo).
        for g in scenes + pics:
            if look["titles"] == "site":
                g["titles"] = "site"
            else:
                g.pop("titles", None)
        # A picture Studio can't show (turned off, no key, Gemini down, wrong twice) is drawn instead.
        pics, instead, more, pic_tally = pictures.make(pics, conn, production, version,
                                                       ref or f"production:{production_id}", client)
        pic_notes += more
        if look["titles"] == "site":
            instead = [{**g, "titles": "site"} for g in instead]
        scenes = sorted(scenes + instead, key=lambda g: g["start"])
        tally = {"drawn": 0, "reused": 0}
        if scenes:
            scenes, more, tally = render_scenes(scenes)
            scene_notes += more
        graphics = sorted(cards + scenes + pics, key=lambda g: g["start"])
        edl["graphics"] = graphics
        edl["inserts"] = {**tally, "placed": len(scenes), "left_out": scene_notes}
        if pics or pic_tally["instead"] or pic_notes or pic_tally["paid"]:  # a draft with no pictures stays as it was
            edl["pictures"] = {**pic_tally, "left_out": pic_notes}
        else:
            edl.pop("pictures", None)
        checks += [{"did": n, "counted": False} for n in scene_notes + pic_notes]
        if scenes and not placed:
            edl["summary"] = (edl.get("summary", "").rstrip() + " " + scene_summary(
                [g for g in scenes if not g.get("instead_of_picture")])).strip()
        if (pics or pic_tally["instead"]) and not placed:
            edl["summary"] = (edl.get("summary", "").rstrip() + " " + pictures.summary(
                pics, pic_tally["instead"])).strip()
        elif scenes and not placed:  # drawn because pictures can't be made at all: said once
            edl["summary"] = (edl.get("summary", "").rstrip() + " " + pictures.off_note(conn, production)).strip()
        if pip and not placed:
            edl["summary"] = (edl.get("summary", "").rstrip() + " " + voiceover.summary(pip)).strip()
        chunks, doubled = clear_of_cards(chunks, graphics)
        if doubled:
            checks.append({"did": f"hid {doubled} caption{'s' if doubled != 1 else ''} repeating a card's own words",
                           "counted": True})
        if look["titles"] in ("gallery", "site"):
            chunks = key_word_captions(chunks, edl.get("key_words") or [])
            # Kept for the Resolve project's captions; a full-screen picture is a photo under them too.
            edl["bright"] = sorted(bright_spans(picture) + pictures.bright(pics))
            chunks = lit_captions(chunks, edl["bright"])
            if window:
                chunks = voiceover.lit_beside(chunks, window)
        end = {"start": close_at, "duration": card["seconds"], "ask": card["ask"], "handle": card["handle"]} if card else None
        if chunks or edl["hook_text"].strip() or cards or end:
            overlay = work / "overlay.webm"
            paper = [(g["start"], g["start"] + g["duration"]) for g in scenes if g.get("look") == "cutaway"]
            render_overlay(chunks, edl["hook_text"], total, overlay, cards,
                           ACCENT.get(production["brand"], ACCENT["urban_makers"]), font=look["caption_font"],
                           style=look["titles"], hook_italic=edl.get("hook_italic"), end=end,
                           **({"paper": paper} if paper else {}), **({"pip": window} if window else {}))

        music_info, music_file = {"mood": edl["music"]["mood"], "tempo_bpm": edl["music"]["tempo_bpm"]}, None
        chosen = (plan.get("music_plan") or {}).get("chosen")
        if music.allowed_for(production["brand"]) and settings.EPIDEMIC_SOUND_API_KEY:
            brief = music_brief(edl["music"], chosen)
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

        layers = {"inserts": [(insert_file(g), g["start"]) for g in sorted(scenes + pics, key=lambda g: g["start"])]} \
            if scenes or pics else {}
        # Soft sound effects on the graphics (sfx.py): a whoosh as each comes in, ticks on a numbers panel, the end
        # card's chime; sparse, clear of his loud words, well under his voice. None (and no edl["sfx"]) when off.
        import sfx
        effects, fx_notes = sfx.plan(edl, graphics, end, cuts, words, total, production["brand"], look["titles"], picture)
        if effects:
            layers.update(effects=effects, effects_below=edl["sfx"]["level_db"])
        checks += [{"did": n, "counted": False} for n in fx_notes]
        # The shot's own sound fades out at the end of the footage, after the last word (never over it).
        speech_end = close.get("speech_end")
        fade = min(0.3, footage - speech_end - 0.05) if speech_end is not None else 0.0
        if pip and pip.get("site_gain_db") is not None and (work / "site.wav").exists():
            layers["under"] = (work / "site.wav", pip["site_gain_db"])
        sound = mix_final(picture, overlay, music_file, edl["music"]["volume_db"], total, final, words,
                          footage=footage, voice_out=(footage - fade, fade) if fade >= 0.05 else None, **layers)
        if sound.get("music_gain_db") is not None:
            music_info["gain_db"] = sound["music_gain_db"]  # the track's level in the mix (for Resolve)
        if sound.get("voice_gain_db", 0) >= 6:
            checks.append({"did": f"brought your voice up {sound['voice_gain_db']:.0f} dB to a steady level "
                                  "and cleaned up the background noise", "counted": False})
        if (sound.get("sfx") or {}).get("lowered_db"):
            checks.append({"did": f"turned the sound effects down {sound['sfx']['lowered_db']:.0f} dB more so they stay "
                                  "well under your voice", "counted": False})
        if sound.get("music_lowered_db"):
            checks.append({"did": f"turned the music down {sound['music_lowered_db']:.0f} dB more so it stays "
                                  "under your voice", "counted": False})
        loud = check_loudness(final)
        if fix_loudness(final, loud):
            loud = check_loudness(final)
            checks.append({"did": "set the loudness to Instagram's level", "counted": False})
        qa = {**loud, **check_words(final, words, work), "sound": sound, "checks": checks, "joins": joins,
              # beats.py's check on the spoken hook: does the first sentence land inside 3 seconds?
              "hook": wording.hook_timing(words)}

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
