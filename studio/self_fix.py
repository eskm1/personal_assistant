"""
Studio fixes its own list before Bryan sees a draft ("the fixes should be something the
editor catches before giving me the reviewed version"; 2026-10-20: "Please try to ensure the best
possible quality before the video comes to me").

After a draft renders (editor.py; a slideshow Reel, slideshow.py) or a carousel is laid out
(carousel.py), Studio's read (draft_review.py) runs. Each fix in it says whether the editor can make it itself (editor_can_fix)
and, if so, exactly how: a machine action (fix_actions.py: a caption word, a stutter, a pause, a
cut point, a card, the hook, the music level, a slide's words...). While there are fixes the
editor can make, up to settings.SELF_FIX_ROUNDS rounds (default 3):

  1. the actions are made directly on the draft's own edit plan ("patches": no new edit plan, so
     nothing else in the cut changes) and it's rendered again as the SAME version, to a file of its
     own, with every code check; what no action can make (or a patch didn't fix) goes to the editor
     once, all together, for a new plan (editor.self_revise / carousel.self_revise), the old way; a slideshow
     Reel's patch is the music level, its re-plan the slides' times and order (slideshow.patch / self_revise);
  2. Studio reads it again;
  3. the round is judged fix by fix: kept when the fixes it made are gone from the new read
     (matched by area, moment and words, not exact wording) and nothing that matters got worse
     (the verdict didn't drop to "Needs work", no new problem in an area it touched); partial
     progress is kept. A dropped round's version is removed and the better one stays. The rules
     behind the fixes it made are learned (learning.py).

Rounds stop when nothing editor-fixable is left that Studio hasn't tried both ways, when the
rounds run out, or when the next step would pass settings.SELF_FIX_MAX_USD for this version (the
read, the rounds and the caption spell-check together). Only then does the draft go to To review
('ready'), so Bryan is told once and sees one version, v1, with "Studio fixed N things before
showing you this", the cost line, and only what's left as fixes, each saying why it's his
(fix.left_because: new footage, his call, or what Studio tried). The rounds (what each asked, how,
its read, kept or not, why) stay in productions.self_fix for debugging. Their Claude calls are
recorded under ref() (kinds edit/carousel and review, as usual), so the draft's cost line can show
them apart. SELF_FIX_ROUNDS = 0 turns it off.
"""
import json
import logging
import re
from pathlib import Path

import catalog
import settings

log = logging.getLogger("studio.self_fix")

_REEL = ("video_path", "thumb_path", "duration", "edl", "qa", "music", "review", "review_version")
_CAROUSEL = ("thumb_path", "slides", "caption", "source_clips", "review", "review_version")


def ref(production_id: int) -> str:
    """Where the rounds' Claude calls are recorded (inside the draft's own total)."""
    return f"production:{production_id}:selffix"


def fixable(review: dict | None) -> list[dict]:
    """The read's fixes the editor can make itself."""
    return [f for f in (review or {}).get("fixes", [])
            if f.get("editor_can_fix") is True and f.get("when", "this_draft") != "next_shoot"]


def score(review: dict) -> tuple:
    """How much is wrong, by the read (smaller is better): needs work, areas marked fix, fixes
    for this draft, and fixes the editor could make."""
    fixes = review.get("fixes", [])
    return (1 if re.match(r"(?i)\s*needs work", review.get("verdict", "")) else 0,
            sum(1 for v in (review.get("scores") or {}).values() if v == "fix"),
            sum(1 for f in fixes if f.get("when", "this_draft") == "this_draft"),
            len(fixable(review)))


def _row(conn, production_id: int) -> dict:
    return dict(conn.execute("SELECT * FROM productions WHERE id = ?", (production_id,)).fetchone())


def _snapshot(row: dict) -> dict:
    return {k: row.get(k) for k in (_CAROUSEL if row.get("format") == "carousel" else _REEL)}


def _restore(conn, production_id: int, snap: dict) -> None:
    conn.execute(f"UPDATE productions SET {', '.join(f'{k} = ?' for k in snap)} WHERE id = ?",
                 (*snap.values(), production_id))
    conn.commit()


def _files(row: dict) -> list[Path]:
    """The files a version's render made (to remove a dropped one)."""
    if row.get("format") == "carousel":
        try:
            folder = Path(json.loads(row.get("slides") or "{}").get("files", [""])[0]).parent
        except (ValueError, IndexError, TypeError):
            return []
        return [folder] if folder.name.startswith("carousel_") else []
    import previews
    files = [Path(p) for p in (row.get("video_path"), row.get("thumb_path")) if p]
    return files + ([previews.light_for(row["video_path"])] if row.get("video_path") else [])


def _remove(paths: list[Path], keep: list[Path]) -> None:
    import shutil
    keep = {p.resolve() for p in keep}
    for p in paths:
        try:
            if p.resolve() in keep or not p.exists():
                continue
            shutil.rmtree(p) if p.is_dir() else p.unlink()
        except OSError as e:
            log.warning("Couldn't remove %s: %s", p, e)


def _notes(fixes: list[dict]) -> list[dict]:
    """The read's fixes as notes for the editor (numbered from 1)."""
    out = []
    for i, f in enumerate(fixes, 1):
        out.append({"id": i, "at_seconds": f.get("start"), "slide": f.get("slide"),
                    "body": f"{f['what']} ({f['why']})" if f.get("why") else f["what"]})
    return out


# Bryan, 2026-10-20: "Every time we do another pass of revisions, just automatically add all of the
# notes that you had identified ... they're hygiene, so I don't need to review them one by one."
READ_NOTE_ID = 900000  # ids for the read's fixes when they ride along with Bryan's notes (never a feedback id)


def read_notes(row, open_notes: list | None = None) -> list[dict]:
    """The latest read's fixes for this draft (not "film next time"), as notes for a revision, minus any
    Bryan already added as a note himself (Review's "Add as note"). Empty when the read is of an
    older version."""
    row = dict(row)
    if not row.get("review") or row.get("review_version") != row.get("version"):
        return []
    try:
        fixes = (json.loads(row["review"]) or {}).get("fixes") or []
    except ValueError:
        return []
    taken = {str(n["body"]).strip() for n in (open_notes or [])}
    todo = [f for f in fixes if isinstance(f, dict) and f.get("what")
            and f.get("when", "this_draft") != "next_shoot" and f["what"].strip() not in taken]
    return [{**n, "id": READ_NOTE_ID + n["id"]} for n in _notes(todo)]


def _checks(row: dict) -> list[dict]:
    if row.get("format") == "carousel":
        return []
    return [c for c in json.loads(row.get("qa") or "{}").get("checks") or [] if isinstance(c, dict)]


# ── Telling one read's fix from another's ─────────────────────────────────────
# A fresh read words things its own way, so a fix is matched by what it's about (its area, its moment,
# its action and the words in it), never by its exact wording.
_STOP = {"the", "and", "for", "with", "that", "this", "from", "into", "onto", "over", "under", "your", "you",
         "its", "his", "her", "are", "was", "but", "not", "when", "where", "what", "make", "move", "change"}
NEAR_SECONDS = 2.5


def _content(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9']+", str(text or "").lower()) if len(w) > 2 and w not in _STOP}


def _key(text) -> str:
    return re.sub(r"[\W_]+", "", str(text or "").lower())


def same_fix(a: dict, b: dict) -> bool:
    """Do two fixes (from two reads) name the same problem?"""
    if a.get("check") or b.get("check"):  # Studio's listen-back and watch-through: its kind, word and moment
        if a.get("check") != b.get("check") or _key(a.get("subject")) != _key(b.get("subject")):
            return False
        return a.get("start") is None or b.get("start") is None or abs(a["start"] - b["start"]) <= NEAR_SECONDS
    aa, ab = a.get("action") or {}, b.get("action") or {}
    kind = aa.get("type")
    if kind and kind == ab.get("type") and kind not in ("none", "replan"):
        if kind in ("caption_word", "drop_words", "restore_words") and _key(aa.get("from")) and _key(ab.get("from")):
            if _key(aa["from"]) != _key(ab["from"]):
                return False
            if a.get("start") is None or b.get("start") is None or abs(a["start"] - b["start"]) <= NEAR_SECONDS:
                return True
        if kind == "graphic" and aa.get("item") is not None and ab.get("item") is not None:
            if aa["item"] != ab["item"]:
                return False
        if kind in ("hook_text", "music_level"):
            return True
        if kind == "sound_effects":
            return aa.get("item") == ab.get("item")
        if kind == "slide_text" and aa.get("item") == ab.get("item") and aa.get("field") == ab.get("field"):
            return True
    if a.get("slide") and b.get("slide") and a["slide"] != b["slide"]:
        return False
    if a.get("start") is not None and b.get("start") is not None:
        a_end = a["end"] if a.get("end") is not None else a["start"]
        b_end = b["end"] if b.get("end") is not None else b["start"]
        if not (a["start"] - NEAR_SECONDS <= b_end and b["start"] - NEAR_SECONDS <= a_end):
            return False
    wa, wb = _content(a.get("what")), _content(b.get("what"))
    sim = len(wa & wb) / len(wa | wb) if wa and wb else 0.0
    return (a.get("area") == b.get("area") and sim >= 0.2) or sim >= 0.45


def _needs_work(review: dict) -> bool:
    return bool(re.match(r"(?i)\s*needs work", (review or {}).get("verdict", "")))


def _worse(old: dict, new: dict, touched: set) -> str:
    """What the round broke, in plain words ("" when nothing): the read now says it needs work, or
    an area the round touched went from good to fix with a problem that wasn't there before."""
    if _needs_work(new) and not _needs_work(old):
        return f"the read now says \"{new.get('verdict', '')}\""
    for area in touched:
        if (old.get("scores") or {}).get(area) == "good" and (new.get("scores") or {}).get(area) == "fix":
            fresh = [f for f in new.get("fixes", []) if f.get("area") == area
                     and not any(same_fix(f, o) for o in old.get("fixes", []))]
            if fresh:
                return f"a new problem: {fresh[0]['what']}"
    return ""


# ── What Studio does with each fix, and why one is left for Bryan ─────────────

def _tries(fix: dict, tries: list[dict]) -> list[dict]:
    return [t for t in tries if same_fix(t["fix"], fix)]


def _method(fix: dict, tries: list[dict]) -> str | None:
    """How Studio makes a fix next: "patch" (its action, directly), "replan" (the editor plans the
    draft again with it), or None when both have been tried."""
    import fix_actions
    tried = {t["how"] for t in _tries(fix, tries)}
    if (fix.get("action") or {}).get("type") in fix_actions.PATCHES and "patch" not in tried:
        return "patch"
    return "replan" if "replan" not in tried else None


def _try(fix: dict, how: str, ok: bool, outcome: str = "") -> dict:
    return {"fix": {k: fix.get(k) for k in ("what", "area", "start", "end", "slide", "action", "check", "subject")},
            "how": how, "ok": ok, "outcome": outcome}


_BRYANS = {"footage": "Needs new footage or a real photo.", "taste": "Your call: it's a matter of taste.",
           "message": "Your call: it changes what the post says.", "spending": "Your call: it costs money.",
           "": "Your call."}


def left_because(fix: dict, tries: list[dict], stopped: str | None, rounds: int, cap: float) -> dict:
    """Why a fix is left for Bryan, in plain words: {kind: footage | yours | tried | stopped, text}."""
    if fix.get("when") == "next_shoot":
        text = "Needs new footage: film it next time."
        if fix.get("drawn"):
            text += " Drawn for now in this draft."
        return {"kind": "footage", "text": text}
    if not fix.get("editor_can_fix"):
        if fix.get("left_note"):  # Studio's own check found it but no change of its can make it
            return {"kind": "look", "text": fix["left_note"]}
        reason = fix.get("for_bryan") or ""
        return {"kind": "footage" if reason == "footage" else "yours", "text": _BRYANS.get(reason, _BRYANS[""])}
    mine = _tries(fix, tries)
    if mine:
        said = list(dict.fromkeys(t["outcome"] for t in mine if t.get("outcome")))[-2:]
        return {"kind": "tried", "text": "Studio tried and couldn't: " + ("; ".join(said) or "it was still there") + "."}
    if stopped == "photos":
        return {"kind": "yours", "text": "A photos-only carousel: Studio leaves its photos, cover line and caption as "
                                         "written, so this one is yours."}
    if rounds <= 0:
        return {"kind": "stopped", "text": "Studio's own fixes are turned off." if settings.SELF_FIX_ROUNDS <= 0 else
                "This version was made again from the same edit, so Studio only read it."}
    if stopped == "cap":
        return {"kind": "stopped", "text": f"Studio stopped before this one: it reached its US${cap:.2f} limit for this draft."}
    if stopped == "read":
        return {"kind": "stopped", "text": "Studio couldn't read the draft again to check a fix, so it stopped."}
    return {"kind": "stopped", "text": f"Studio found it after its last fix round (it does {rounds} rounds a draft)."}


def _edit_usd(conn, production_id: int) -> float:
    """What one edit plan costs (the dearest "edit" call on this production), to judge a re-plan."""
    import usage
    conn.executescript(usage._SCHEMA)
    row = conn.execute("SELECT MAX(cost_usd) FROM usage WHERE kind = 'edit' AND (ref = ? OR ref LIKE ?)",
                       (f"production:{production_id}", f"production:{production_id}:%")).fetchone()
    return float(row[0]) if row and row[0] else 0.40


def deliver(conn, production_id: int, client=None, find_track=None, rounds: int | None = None,
            count: bool = True) -> dict:
    """Studio's read, its own fix rounds, then To review. Returns the production row as it's
    delivered. A failed round never fails the draft: the better version is kept. count: log
    the draft for Review's trend (not for a re-render of the same edit)."""
    import draft_review
    import learning
    import usage
    rounds = settings.SELF_FIX_ROUNDS if rounds is None else rounds
    cap = settings.SELF_FIX_MAX_USD
    row = _row(conn, production_id)
    scope = "carousel" if row.get("format") == "carousel" else "reel"
    slides = row.get("format") == "slideshow"  # a Reel of a carousel's slides: its own patches and re-plan
    whole = f"production:{production_id}"
    base = usage.total_for(conn, whole)
    first_spell = float(json.loads(row.get("edl") or "{}").get("spell_usd") or 0) if scope == "reel" else 0.0

    def spent() -> float:  # this version's read, rounds and spell-checks (the render's own spell-check too)
        return round(usage.total_for(conn, whole) - base + first_spell, 4)
    review = draft_review.safe_run(conn, production_id, client)
    read_usd = max(spent() - first_spell, 0.02)
    row = _row(conn, production_id)  # with its read, so going back to it restores the read too
    first = fixable(review)
    checks = _checks(row)
    caught = len(first) + sum(1 for c in checks if c.get("counted"))
    history = {"version": row["version"], "at": catalog.now(), "rounds": [], "fixed": [], "cap": cap,
               "first_checks": [c["did"] for c in checks], "first_read": review and {
                   "verdict": review.get("verdict"), "fixes": len(review.get("fixes", [])), "fixable": len(first)}}
    best, best_row = review, row
    tries: list[dict] = []
    done, stopped = 0, None
    if scope == "carousel":
        import carousel
        if carousel.layout_of(row) == carousel.PHOTOS:  # as written: Studio reads it, Bryan decides (the CTO, 2026-10-28)
            rounds, stopped = 0, "photos"
    replan_usd = 0.05 if slides else _edit_usd(conn, production_id) if scope == "reel" else 0.15
    while best is not None:
        todo = [(n, f) for n, f in enumerate(best.get("fixes", []), 1) if f in fixable(best)]
        plan = [(n, f, _method(f, tries)) for n, f in todo]
        plan = [(n, f, how) for n, f, how in plan if how]
        if not plan:
            break  # nothing left Studio can still try
        if done >= rounds:
            stopped = stopped or "rounds"
            break
        # Patches first (cheap, exact); what nothing smaller can fix, all together in one re-plan.
        how = "patch" if any(h == "patch" for _, _, h in plan) else "replan"
        batch = [(n, f) for n, f, h in plan if h == how]
        estimate = read_usd + (replan_usd if how == "replan" else 0.02)
        if cap and spent() + estimate > cap:
            stopped = "cap"
            log.info("Production %s: Studio's fixes stop at US$%.2f of its US$%.2f limit", production_id, spent(), cap)
            break
        done += 1
        before = score(best)
        entry = {"round": done, "how": how, "asked": [f["what"] for _, f in batch]}
        results = None
        try:
            if how == "patch":
                actions = [{"n": n, "action": f["action"]} for n, f in batch]
                if scope == "carousel":
                    import carousel
                    made, results = carousel.patch(conn, production_id, actions, done)
                elif slides:
                    import slideshow
                    made, results = slideshow.patch(conn, production_id, actions, done, client, find_track)
                else:
                    import editor
                    made, results = editor.self_patch(conn, production_id, actions, done, client, find_track)
            else:
                notes = _notes([f for _, f in batch])
                if scope == "carousel":
                    import carousel
                    made = carousel.self_revise(conn, production_id, notes, done, client)
                elif slides:
                    import slideshow
                    made = slideshow.self_revise(conn, production_id, notes, done, client, find_track)
                else:
                    import editor
                    made = editor.self_revise(conn, production_id, notes, done, client, find_track)
        except Exception as e:
            log.warning("Studio's fix round %d failed for production %s: %s: %s", done, production_id,
                        type(e).__name__, e)
            entry.update(kept=False, error=f"{type(e).__name__}: {e}"[:300])
            history["rounds"].append(entry)
            tries += [_try(f, how, False, "the quick fix failed" if how == "patch" else "the re-edit failed")
                      for _, f in batch]
            _restore(conn, production_id, _snapshot(best_row))
            continue
        applied = batch
        if results is not None:
            entry["results"] = results
            ok = {r["n"]: r for r in results if r.get("ok")}
            for n, f in batch:
                if n not in ok:
                    why = next((r.get("why") for r in results if r["n"] == n), "") or "it couldn't be made"
                    tries.append(_try(f, "patch", False, f"a quick fix couldn't ({why})"))
            applied = [(n, f) for n, f in batch if n in ok]
            if made is None:  # nothing could be made: no render, no read, and the round isn't counted
                done -= 1
                entry.update(kept=False, skipped=True)
                history["rounds"].append(entry)
                continue
        new_row = _row(conn, production_id)
        new = draft_review.safe_run(conn, production_id, client, ref=ref(production_id))
        entry.update(file=new_row.get("video_path") or "", summary=(json.loads(new_row.get("edl") or "{}").get("summary")
                                                                    if scope == "reel" else ""),
                     verdict=(new or {}).get("verdict"), score=list(score(new)) if new else None,
                     before=list(before), usd=spent())
        if new is None:  # no read to judge it by: the version before stays, and the rounds stop
            entry["kept"] = False
            history["rounds"].append(entry)
            _remove(_files(new_row), _files(best_row))
            _restore(conn, production_id, _snapshot(best_row))
            stopped = "read"
            break
        # Judged fix by fix: kept when what it set out to fix is gone and it broke nothing that matters.
        gone = [(n, f) for n, f in applied if not any(same_fix(f, g) for g in new.get("fixes", []))]
        broke = _worse(best, new, {f.get("area") for _, f in applied})
        label = "a quick fix" if how == "patch" else "a re-edit"
        if broke or not gone:
            entry.update(kept=False, why=broke or "the read still saw every fix it made")
            history["rounds"].append(entry)
            tries += [_try(f, how, False, f"{label} made something else worse ({broke})" if broke
                           else f"{label} didn't change what Studio's read saw") for _, f in applied]
            _remove(_files(new_row), _files(best_row))
            _restore(conn, production_id, _snapshot(best_row))
            log.info("Production %s: Studio's fix round %d dropped (%s)", production_id, done, entry["why"])
            continue
        # Kept: what it fixed (how, in plain words), and the rules behind it.
        did = {r["n"]: r.get("did", "") for r in results or [] if r.get("ok")}
        addressed = _addressed(new_row, scope) if how == "replan" else {}
        gone_n = {n for n, _ in gone}
        fixed = [{"what": f["what"], "how": did.get(n) or addressed.get(i, ""), "area": f.get("area"), "by": how}
                 for i, (n, f) in enumerate(batch, 1) if n in gone_n]
        tries += [_try(f, how, n in gone_n, "" if n in gone_n else f"{label} didn't change what Studio's read saw")
                  for n, f in applied]
        entry.update(kept=True, fixed=len(fixed))
        history["rounds"].append(entry)
        history["fixed"] += fixed
        # A slideshow's rules (slide times, its music) aren't the Reels' editing rules: not learned.
        learned = [] if slides else learning.learn(
            [{"rule": f.get("rule"), "same_rule": f.get("same_rule")} for _, f in gone if f.get("rule")], scope, "studio")
        entry["rules"] = [f"R{r['id']}" for r in learned]
        _remove(_files(best_row), _files(new_row))
        best, best_row = new, new_row
    usd = spent()
    # What Studio's code checks did in the version Bryan sees (the "caught" count is the first pass's).
    final_checks = _checks(best_row)
    history["checks"] = [c["did"] for c in final_checks]
    history.update(caught=caught, fixed_count=len(history["fixed"]) + sum(1 for c in final_checks if c.get("counted")),
                   usd=round(usd, 4), left=len((best or {}).get("fixes", [])), stopped=stopped,
                   tries=tries)
    if best is not None:  # every fix left for Bryan says why it's his
        for f in best.get("fixes", []):
            f["left_because"] = left_because(f, tries, stopped, rounds, cap)
        conn.execute("UPDATE productions SET review = ? WHERE id = ? AND review_version = version",
                     (json.dumps(best, ensure_ascii=False), production_id))
    if scope == "reel" and best_row.get("video_path"):
        import previews
        previews.for_draft(best_row["video_path"])  # the light copy Review plays (the full file if it fails)
    conn.execute("UPDATE productions SET status = 'ready', self_fix = ?, updated_at = ? WHERE id = ?",
                 (json.dumps(history, ensure_ascii=False), catalog.now(), production_id))
    conn.commit()
    if best is not None and count and not slides:
        learning.log_draft(conn, production_id, row["version"], scope, caught, history["fixed_count"],
                           history["left"], done, usd)
    if done:
        log.info("Production %s v%s: Studio fixed %d thing(s) itself in %d round(s) (US$%.2f)", production_id,
                 row["version"], len(history["fixed"]), done, usd)
    return _row(conn, production_id)


def _addressed(row: dict, scope: str) -> dict[int, str]:
    """{note number: how} from the round's plan."""
    if scope == "carousel":
        items = json.loads(row.get("slides") or "{}").get("notes_addressed", [])
    else:
        items = json.loads(row.get("edl") or "{}").get("notes_addressed", [])
    return {a["note_id"]: a.get("how", "") for a in items if isinstance(a, dict) and isinstance(a.get("note_id"), int)}


def summary(row) -> dict | None:
    """What Review shows: what Studio fixed before Bryan saw this version (only when it
    describes the current version), and its cost line."""
    try:
        data = json.loads(row["self_fix"] or "null")
    except (ValueError, KeyError, IndexError):
        return None
    if not isinstance(data, dict) or data.get("version") != row["version"]:
        return None
    fixed = [f["what"] for f in data.get("fixed", [])] + [c[0].upper() + c[1:] for c in data.get("checks", []) if c]
    usd, cap = float(data.get("usd") or 0), data.get("cap")
    line = f"Studio's read and its own fixes cost US${usd:.2f} for this draft"
    line += f" (its limit is US${cap:.2f})." if cap else "."
    if data.get("stopped") == "cap":
        line += " It stopped fixing at that limit."
    try:  # the draft's AI pictures (pictures.py): new ones are Gemini's, reused ones cost nothing
        pics = json.loads(row["edl"] or "{}").get("pictures") or {}
    except (ValueError, KeyError, IndexError, TypeError):
        pics = {}
    if pics.get("placed") or pics.get("drawn"):
        drawn, shown = int(pics.get("drawn") or 0), int(pics.get("placed") or 0)
        line += (f" Pictures: {shown} shown, {drawn} drawn by Gemini (US${float(pics.get('usd') or 0):.2f})."
                 if drawn else f" Pictures: {shown} shown, all already drawn (no new cost).")
    return {"fixed": fixed, "caught": data.get("caught", 0),
            "rounds": sum(1 for r in data.get("rounds", []) if not r.get("skipped")),
            "usd": usd, "cap": cap, "stopped": data.get("stopped"), "cost_line": line}
