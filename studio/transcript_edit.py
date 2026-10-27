"""
Edit words: Bryan (or the team) corrects the transcript of a Reel waiting for review, and Studio puts
the right words in its captions (Bryan, 2026-10-27: "Sometimes there are errors in the transcript. Can I
correct typos and resubmit for the captions to be updated? ... I didn't mean to say SIDs, I meant to say
'as IDs'").

Review's Transcript fold has Edit words: the transcript becomes a text box, he corrects words in place
(a word replaced, one word into two, two into one, a word Whisper heard that wasn't said) and presses Fix
captions. Here:

1. The text he sent is compared with the draft's words (diff): word by word for English, character by
   character for Chinese, lined up without case or punctuation so a word whose case or punctuation
   changed is still that word. Spaces and new lines never count. Each changed stretch maps back onto the
   words it replaced and their times (one word into two: the two share its time; two into one: the
   second folds into the first). A change too big to be a correction (a phrase added or taken out, a new
   sentence, most of the words different) is refused with why: that's an edit, it needs a note.
2. They become caption corrections (edl["corrections"], by clip and the word's time in it: the same
   path Studio's read and the spell-check use, editor.attach_corrections), kept with the draft until its
   new version renders (edl["word_fixes"], which is also what tells the editor to do this and nothing
   else: editor.edit), and the draft goes to Being edited.
3. The editor renders it again as its next version with the same cut (editor._render, placed): no new
   plan, no Claude (no spell-check, no read: the read of the version before is kept, it saw the same
   cut, minus the caption fixes these words made), no new pictures. US$0. Back to To review.

"Remember this word" adds the corrected terms to the trade glossary (glossary.add), so Whisper hears them
next time and the spell-check trusts them. Corrections teach no editing rule (learning.py has no caption
path; a "wrong -> right" line in the glossary is how Bryan teaches a mis-hearing).
"""
import difflib
import json
import logging
import re
from pathlib import Path

import catalog

log = logging.getLogger("studio.transcript_edit")

MAX_OLD_WORDS = 4       # words one change may replace
MAX_NEW_WORDS = 6       # words one change may say
MAX_MORE = 2            # words one change may add or take out (one into two is one more)
CJK_WORD = 3            # Chinese is compared by character: about three to a word
MAX_SHARE = 0.4         # of the draft's words that may change at once
MAX_TEXT = 20_000

_CJK = "㐀-䶿一-鿿豈-﫿　-〿＀-￯"
_UNIT = re.compile(f"[{_CJK}]|[^\\s{_CJK}]+")
_IS_CJK = re.compile(f"[{_CJK}]")
_SENTENCE = re.compile(r"[.!?。！？]['\"”’)]*$")

# Small words never added to the glossary by themselves ("SIDs" -> "as IDs" remembers "IDs").
_COMMON = set("""a an the and or but so if as at by for from in into of on onto to up with without is are was were
be been being am do does did done have has had i you he she it we they me him her us them my your his its our their
this that these those there here then than not no yes very just also too all any some more most much many one
two three can could will would shall should may might must what which who whom whose when where why how""".split())


class Refused(Exception):
    """A change Studio won't make as a caption correction; its message says why, in plain words."""


class Changed(Refused):
    """The draft moved on (a new version, or out of To review) while the words were being edited."""


# ── The words, as text and as units ───────────────────────────────────────────

def units(text: str) -> list[str]:
    """Text as the pieces compared: each Chinese character on its own, anything else by spaces."""
    return _UNIT.findall(str(text or ""))


def _join(parts: list[str]) -> str:
    """Units back into text: no space between Chinese characters, one between anything else."""
    out = ""
    for p in parts:
        if out and not (_IS_CJK.match(out[-1]) and _IS_CJK.match(p[0])):
            out += " "
        out += p
    return out


def editable(words: list[dict]) -> str:
    """The text Review's box starts with: the draft's words as its captions say them."""
    return _join([u for w in words for u in units(w["word"])])


def _key(unit: str) -> str:
    return re.sub(r"[\W_]+", "", unit.lower()) or unit


# ── The diff ──────────────────────────────────────────────────────────────────

def diff(words: list[dict], edited: str) -> list[dict]:
    """What changed between the draft's words and the edited text, as changes on whole words:
    [{"first", "last" (word indices), "to" (their new text), "from", "put" (only what was typed in)}].
    Raises Refused for a change that isn't a correction. Empty when nothing changed."""
    old, owner = [], []
    for k, w in enumerate(words):
        for u in units(w["word"]):
            old.append(u)
            owner.append(k)
    new = units(edited)
    if not new and old:
        raise Refused("The transcript is empty. Cancel to leave it as it was.")
    m = difflib.SequenceMatcher(None, [_key(u) for u in old], [_key(u) for u in new], autojunk=False)
    edits = []  # (i1, i2, new units) in unit positions
    for tag, i1, i2, j1, j2 in m.get_opcodes():
        if tag == "equal":  # the same words; one whose case or punctuation changed is a change of that word
            edits += [(i1 + k, i1 + k + 1, [new[j1 + k]]) for k in range(i2 - i1) if old[i1 + k] != new[j1 + k]]
        else:
            edits.append((i1, i2, new[j1:j2]))
    if not edits:
        return []
    # Each edit on the words it touches (an insert: the word it comes before, or the last word).
    groups = []  # [first word, last word, [edits]]
    for i1, i2, put in edits:
        if i2 > i1:
            a, b = owner[i1], owner[i2 - 1]
        else:
            a = b = owner[i1] if i1 < len(owner) else owner[-1]
        if groups and a <= groups[-1][1]:
            groups[-1][1] = max(groups[-1][1], b)
            groups[-1][2].append((i1, i2, put))
        else:
            groups.append([a, b, [(i1, i2, put)]])
    out = []
    for a, b, todo in groups:
        lo = owner.index(a)
        hi = len(owner) - 1 - owner[::-1].index(b)
        parts, typed, pos = [], [], lo
        for i1, i2, put in todo:
            parts += old[pos:i1] + put
            typed += put
            pos = max(pos, i2)
        parts += old[pos:hi + 1]
        was = " ".join(w["word"].strip() for w in words[a:b + 1])
        to = _join(parts)
        if to == was:
            continue
        out.append({"first": a, "last": b, "from": was, "to": to, "put": _join(typed)})
    _check(words, out)
    return out


def _quote(text: str) -> str:
    text = str(text or "").strip()
    return f"\u201c{text if len(text) <= 30 else text[:28].rstrip() + '\u2026'}\u201d"


def _check(words: list[dict], changes: list[dict]) -> None:
    """A correction, not an edit: small changes, no new sentence, not most of the words."""
    changed = sum(c["last"] - c["first"] + 1 for c in changes)
    if words and changed > max(4, MAX_SHARE * len(words)):
        raise Refused("That changes most of the words. Edit words is for correcting a few: add a note for the editor instead.")
    note = "Add a note for the editor instead."
    for c in changes:
        n_old, n_new = len(units(c["from"])), len(units(c["to"]))  # words (Chinese: characters)
        x = CJK_WORD if _IS_CJK.search(c["from"] + c["to"]) else 1
        quoted = _quote(c["put"] or c["from"])
        said_ends = sum(1 for w in words[c["first"]:c["last"] + 1] if _SENTENCE.search(w["word"].strip()))
        if n_new > n_old and sum(1 for u in units(c["to"]) if _SENTENCE.search(u)) > said_ends:  # a full stop alone is fine
            raise Refused(f"{quoted}: a new sentence is an edit, not a correction. {note}")
        if abs(n_new - n_old) > MAX_MORE * x:
            raise Refused(f"{quoted}: adding or taking out a phrase is an edit, not a correction. {note}")
        if n_old > MAX_OLD_WORDS * x or n_new > MAX_NEW_WORDS * x:
            raise Refused(f"{quoted}: that's rewriting, not correcting words. {note}")
        if len({w["clip_id"] for w in words[c["first"]:c["last"] + 1]}) > 1:
            raise Refused(f"{_quote(c['from'])} runs across two shots. Change each side on its own.")


def corrections(words: list[dict], changes: list[dict], who: str = "") -> list[dict]:
    """The caption corrections that make the draft's words read as changed ([{clip_id, at, from, to, by}],
    editor.attach_corrections). As many words as before: word for word. Otherwise the first word says
    the new text over all their time, and the rest fold into it ("")."""
    out = []

    def fix(w: dict, to: str) -> None:
        out.append({"clip_id": w["clip_id"], "at": round(w["src"], 2), "from": w["word"], "to": to, "by": "review",
                    **({"who": who} if who else {})})
    for c in changes:
        span = words[c["first"]:c["last"] + 1]
        new = c["to"].split()
        cjk = bool(_IS_CJK.search(c["to"]))
        if len(new) == len(span) and len(span) > 1 and not cjk:
            for w, t in zip(span, new):
                if t != w["word"].strip():
                    fix(w, t)
            continue
        fix(span[0], c["to"])
        for w in span[1:]:
            fix(w, "")
    return out


def terms(changes: list[dict]) -> list[str]:
    """What "Remember this word" adds to the glossary: from each change, the words it put in (not the
    ones around them it kept), without punctuation or small common words, at most three words, at least
    two letters; a number on its own never."""
    out = []
    for c in changes:
        if _IS_CJK.search(c["to"]):  # Chinese: the word it changed, as it now reads (2 to 6 characters)
            text = "".join(ch for ch in c["to"] if _IS_CJK.match(ch) and re.match(r"\w", ch))
            if 2 <= len(text) <= 6 and text not in out:
                out.append(text)
            continue
        put = [p for p in (re.sub(r"^[^\w]+|[^\w']+$", "", u) for u in c["put"].split()) if p]
        while put and put[0].lower() in _COMMON:
            put.pop(0)
        while put and put[-1].lower() in _COMMON:
            put.pop()
        if not put or len(put) > 3:
            continue
        text = " ".join(put)
        if len(re.sub(r"[^A-Za-z]", "", text)) < 2 or text.lower() in (t.lower() for t in out):
            continue
        if _key(text) in {_key(u) for u in units(c["from"])} | {_key(c["from"])} and text == text.lower():
            continue  # only its spaces or punctuation changed ("its" -> "it's", "every thing"): not a term
        out.append(text)
    return out


# ── Asking for it, from Review ────────────────────────────────────────────────

def why_not(row) -> str | None:
    """Why this draft can't have its words fixed now (None: it can)."""
    row = dict(row)
    if row.get("format") == "carousel":
        return "A carousel has no transcript. Add a note to change a slide's words."
    if row.get("format") == "slideshow":
        return "A slideshow Reel has no spoken words to correct."
    if row.get("status") != "ready":
        if row.get("status") in ("planned", "revising", "producing"):
            return "The editor is working on it. Edit words when it's back in To review."
        return "Edit words works on a draft waiting for review."
    if not row.get("edl") or not row.get("video_path"):
        return "This draft has no captions to correct."
    return None


def _words(conn, row: dict) -> list[dict]:
    import draft_review
    return draft_review.draft_words(conn, row)


def submit(conn, production_id: int, edited: str, version: int | None, remember: bool = False,
           who: str = "") -> dict:
    """Bryan's edited transcript: checked, turned into caption corrections, kept with the draft and queued
    for a render with the same cut. Returns {"changes": [{from, to}], "remembered": [terms]}. Raises
    Refused (a plain reason) when it can't."""
    row = conn.execute("SELECT * FROM productions WHERE id = ?", (production_id,)).fetchone()
    if row is None:
        raise Refused("That draft is gone.")
    row = dict(row)
    why = why_not(row)
    if why:
        raise Refused(why)
    if version is not None and int(version) != int(row["version"]):
        raise Changed(f"This draft changed since you opened it (it's v{row['version']} now). Cancel and edit again.")
    if not isinstance(edited, str) or len(edited) > MAX_TEXT:
        raise Refused("Send the transcript as text.")
    words = _words(conn, row)
    if not words:
        raise Refused("This draft has no captions to correct.")
    changes = diff(words, edited)
    if not changes:
        raise Refused("Nothing changed. Correct a word, then press Fix captions.")
    shown = [{"from": c["from"], "to": c["to"], "at": round(words[c["first"]]["start"], 2)} for c in changes]
    edl = json.loads(row["edl"])
    edl["word_fixes"] = {"version": row["version"], "corrections": corrections(words, changes, who),
                         "changes": shown, "by": who, "at": catalog.now()}
    with conn:
        n = conn.execute("""UPDATE productions SET edl = ?, status = 'revising', updated_at = ?
                            WHERE id = ? AND status = 'ready' AND version = ?""",
                         (json.dumps(edl), catalog.now(), production_id, row["version"])).rowcount
    if not n:
        raise Changed("This draft changed while you were editing. Cancel and edit again.")
    remembered = []
    if remember:
        import glossary
        remembered = glossary.add(terms(changes), who or "Review")
    log.info("Production %s v%s: %d caption word(s) corrected by %s (%s)%s", production_id, row["version"], len(changes),
             who or "someone", "; ".join(f"{c['from']} -> {c['to']}" for c in shown),
             f", remembered {remembered}" if remembered else "")
    return {"changes": shown, "remembered": remembered}


# ── The render: same cut, the right words ─────────────────────────────────────

def pending(production: dict) -> bool:
    """Is this production queued for its words fixed (and only that)? editor.edit asks."""
    try:
        fixes = json.loads(production.get("edl") or "{}").get("word_fixes") or {}
    except ValueError:
        return False
    return bool(fixes.get("corrections")) and fixes.get("version") == production.get("version")


def _carry_read(old: dict, version: int, changes: list[dict], words_before: list[dict]) -> dict:
    """The read of the version before, for the new one: the same cut and picture, so it still holds,
    minus the caption fixes these corrections made (the read's "caption_word" on a word that changed)."""
    changed = []
    for c in changes:
        for w in words_before:
            if abs(w["start"] - float(c.get("at") or 0)) <= 2.5 and _key(w["word"]) in {_key(u) for u in units(c["from"])}:
                changed.append(w)

    def made(f: dict) -> bool:
        a = f.get("action") or {}
        if a.get("type") != "caption_word":
            return False
        said = {_key(u) for u in units(a.get("from") or "")}
        at = f.get("start") if f.get("start") is not None else a.get("start")
        return any(_key(w["word"]) in said and (at is None or abs(float(at) - w["start"]) <= 2.5) for w in changed)
    fixes = [f for f in old.get("fixes") or [] if not made(f)]
    return {**old, "fixes": fixes, "carried": f"Read on v{version - 1}. Only the captions changed since."}


def render(conn, production_id: int, find_track=None) -> Path:
    """The editor's job for a draft whose words were corrected (edl["word_fixes"], pending()): its corrections
    added to the draft's, rendered again as the next version from the same edit plan (no new plan, no
    spell-check, no read: no Claude at all), the read before carried over, back to To review."""
    import editor
    import previews
    production = dict(conn.execute("SELECT * FROM productions WHERE id = ?", (production_id,)).fetchone())
    edl = json.loads(production["edl"] or "{}")
    asked = edl.pop("word_fixes", None) or {}
    if not edl.get("cuts"):
        raise RuntimeError("this production has no finished draft to correct")
    ids = list(dict.fromkeys(c["clip_id"] for c in edl["cuts"]))
    clips = editor._clips_for(conn, ids + editor._broll_ids(edl))
    if set(ids) - set(clips):
        raise RuntimeError("some of the draft's clips are no longer in the library")
    words_before = editor.timeline(edl["cuts"], clips)
    import spelling
    spelling.merge(edl, asked.get("corrections") or [])  # his words win over the read's and the spell-check's
    # A caption key word (Site Notes' brass) follows its word, as fix_actions' caption_word does.
    for c in asked.get("changes") or []:
        old = {editor._core(u).lower() for u in units(c["from"])}
        if edl.get("key_words") and c["to"].split() and old & {k.lower() for k in edl["key_words"]}:
            to = editor._core(c["to"].split()[-1])
            edl["key_words"] = [to if k.lower() in old else k for k in edl["key_words"]]
    find_track, plan = editor._same_music(production, json.loads(production["plan"] or "{}"), find_track)
    catalog.set_production_status(conn, production_id, "producing")
    version = production["version"] + 1
    edl["words_fixed"] = {"version": version, "changes": asked.get("changes") or [], "by": asked.get("by") or ""}
    final = editor._render(conn, production, clips, edl, plan, version, find_track, placed=True,
                           address_notes=False, spell=False)
    row = dict(conn.execute("SELECT * FROM productions WHERE id = ?", (production_id,)).fetchone())
    review, review_version = production.get("review"), production.get("review_version")
    if review and review_version == production["version"]:
        try:
            carried = _carry_read(json.loads(review), version, asked.get("changes") or [], words_before)
            conn.execute("UPDATE productions SET review = ?, review_version = ? WHERE id = ?",
                         (json.dumps(carried, ensure_ascii=False), version, production_id))
        except ValueError:
            pass
    # What Studio fixed before (and the cost line) still describes this cut; its checks are this render's.
    try:
        history = json.loads(production.get("self_fix") or "null")
    except ValueError:
        history = None
    qa = json.loads(row.get("qa") or "{}")
    checks = [c["did"] for c in qa.get("checks") or [] if isinstance(c, dict)]
    if isinstance(history, dict) and history.get("version") == production["version"] and not history.get("pending"):
        history = {**history, "version": version, "checks": checks, "words": asked.get("changes") or []}
    else:
        history = {"version": version, "at": catalog.now(), "rounds": [], "fixed": [], "checks": checks, "caught": 0,
                   "usd": 0.0, "left": 0, "stopped": None, "tries": [], "words": asked.get("changes") or []}
    if row.get("video_path"):
        previews.for_draft(row["video_path"])
    conn.execute("UPDATE productions SET status = 'ready', self_fix = ?, updated_at = ? WHERE id = ?",
                 (json.dumps(history, ensure_ascii=False), catalog.now(), production_id))
    conn.commit()
    log.info("Production %s v%d: captions corrected (%s), same cut, no Claude", production_id, version,
             "; ".join(f"{c['from']} -> {c['to']}" for c in asked.get("changes") or []) or "none pending")
    return final
