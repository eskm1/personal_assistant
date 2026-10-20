"""
Studio fixes its own list before Bryan sees a draft ("the fixes should be something the
editor catches before giving me the reviewed version").

After a draft renders (editor.py) or a carousel is laid out (carousel.py), Studio's read
(draft_review.py) runs. Each fix in it says whether the editor can make it itself
(editor_can_fix: trims, the opening, the hook text, captions, a graphic's placement or
wording, the music level, slide wording...) or it's for Bryan (new footage, spending,
judgement calls). While there are fixes the editor can make, up to settings.SELF_FIX_ROUNDS
rounds:

  1. the editor plans the draft again with those fixes as its notes (the same machinery as
     Send back, editor.self_revise / carousel.self_revise) and renders it as the SAME version,
     to a file of its own; the music stays;
  2. Studio reads it again;
  3. a round that makes the read better (score()) is kept, and the rules behind the fixes it
     made are learned (learning.py); a round that doesn't, or fails, is dropped and the
     better version stays. Rounds stop when nothing editor-fixable is left.

Only then does the draft go to To review ('ready'), so Bryan is never told early and sees
one version, v1, with "Studio fixed N things before showing you this" folded at the top and
only what's left as fixes. The rounds (what each asked for, its read, kept or not) stay in
productions.self_fix for debugging; dropped renders' files are removed. Their Claude calls
are recorded under ref() (kinds edit/carousel and review, as usual), so the draft's cost
line can show them apart. SELF_FIX_ROUNDS = 0 turns it off.
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


def deliver(conn, production_id: int, client=None, find_track=None, rounds: int | None = None,
            count: bool = True) -> dict:
    """Studio's read, its own fix rounds, then To review. Returns the production row as it's
    delivered. A failed round never fails the draft: the better version is kept. count: log
    the draft for Review's trend (not for a re-render of the same edit)."""
    import draft_review
    import learning
    import usage
    rounds = settings.SELF_FIX_ROUNDS if rounds is None else rounds
    spent_before = usage.total_for(conn, ref(production_id))
    row = _row(conn, production_id)
    scope = "carousel" if row.get("format") == "carousel" else "reel"
    review = draft_review.safe_run(conn, production_id, client)
    row = _row(conn, production_id)  # with its read, so going back to it restores the read too
    first = fixable(review)
    checks = _checks(row)
    caught = len(first) + sum(1 for c in checks if c.get("counted"))
    history = {"version": row["version"], "at": catalog.now(), "rounds": [], "fixed": [],
               "first_checks": [c["did"] for c in checks], "first_read": review and {
                   "verdict": review.get("verdict"), "fixes": len(review.get("fixes", [])), "fixable": len(first)}}
    best, best_row = review, row
    todo, done = first, 0
    while review is not None and todo and done < rounds:
        done += 1
        before = score(best)
        notes = _notes(todo)
        entry = {"round": done, "asked": [f["what"] for f in todo]}
        try:
            if scope == "carousel":
                import carousel
                carousel.self_revise(conn, production_id, notes, done, client)
            else:
                import editor
                editor.self_revise(conn, production_id, notes, done, client, find_track)
        except Exception as e:
            log.warning("Studio's fix round %d failed for production %s: %s: %s", done, production_id,
                        type(e).__name__, e)
            entry.update(kept=False, error=f"{type(e).__name__}: {e}"[:300])
            history["rounds"].append(entry)
            _restore(conn, production_id, _snapshot(best_row))
            break
        new_row = _row(conn, production_id)
        new = draft_review.safe_run(conn, production_id, client, ref=ref(production_id))
        entry.update(file=new_row.get("video_path") or "", summary=(json.loads(new_row.get("edl") or "{}").get("summary")
                                                                    if scope == "reel" else ""),
                     verdict=(new or {}).get("verdict"), score=list(score(new)) if new else None,
                     before=list(before))
        if new is None or score(new) >= before:
            # Not better (or no read to tell): the version before stays, and the rounds stop.
            entry["kept"] = False
            history["rounds"].append(entry)
            _remove(_files(new_row), _files(best_row))
            _restore(conn, production_id, _snapshot(best_row))
            log.info("Production %s: Studio's fix round %d didn't improve the read (%s -> %s); kept the one before",
                     production_id, done, before, new and score(new))
            break
        # Kept: what it fixed (the editor says how), and the rules behind it.
        addressed = _addressed(new_row, scope)
        fixed = [{"what": f["what"], "how": addressed.get(i, ""), "area": f.get("area")}
                 for i, f in enumerate(todo, 1) if i in addressed or not addressed]
        entry.update(kept=True, fixed=len(fixed))
        history["rounds"].append(entry)
        history["fixed"] += fixed
        learned = learning.learn([{"rule": f.get("rule"), "same_rule": f.get("same_rule")}
                                  for i, f in enumerate(todo, 1) if (i in addressed or not addressed) and f.get("rule")],
                                 scope, "studio")
        entry["rules"] = [f"R{r['id']}" for r in learned]
        _remove(_files(best_row), _files(new_row))
        best, best_row = new, new_row
        todo = fixable(new)
    usd = usage.total_for(conn, ref(production_id)) - spent_before  # this version's rounds
    # What Studio's code checks did in the version Bryan sees (the "caught" count is the first pass's).
    final_checks = _checks(best_row)
    history["checks"] = [c["did"] for c in final_checks]
    history.update(caught=caught, fixed_count=len(history["fixed"]) + sum(1 for c in final_checks if c.get("counted")),
                   usd=round(usd, 4), left=len((best or {}).get("fixes", [])))
    if scope == "reel" and best_row.get("video_path"):
        import previews
        previews.for_draft(best_row["video_path"])  # the light copy Review plays (the full file if it fails)
    conn.execute("UPDATE productions SET status = 'ready', self_fix = ?, updated_at = ? WHERE id = ?",
                 (json.dumps(history, ensure_ascii=False), catalog.now(), production_id))
    conn.commit()
    if best is not None and count:
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
    describes the current version)."""
    try:
        data = json.loads(row["self_fix"] or "null")
    except (ValueError, KeyError, IndexError):
        return None
    if not isinstance(data, dict) or data.get("version") != row["version"]:
        return None
    fixed = [f["what"] for f in data.get("fixed", [])] + [c[0].upper() + c[1:] for c in data.get("checks", []) if c]
    return {"fixed": fixed, "caught": data.get("caught", 0), "rounds": len(data.get("rounds", [])),
            "usd": data.get("usd", 0)}
