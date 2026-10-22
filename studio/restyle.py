"""
Redo the drafts waiting for review when Studio's look changes. Bryan, 2026-10-20: "as soon as the
style guide is re-released, just refresh all of the work in progress posts ... trigger a redo of all
of these carousels and whatever with the refreshed style guide."

Once per RELEASE (Studio/style_release.txt remembers the last one): every Reel and carousel in
To review is made again as its next version. A Reel gets a fresh edit (today's titles, captions and
music taste); a carousel's slides are rewritten in the current style guide and voice and laid out
again. The idea, Bryan's notes and the earlier versions stay. Review's "Edit it again" does the same
for one draft or all of them. A release that only changes how Reels look (RELEASE_REELS_ONLY, e.g. the
Site Notes graphics of 2026-10-22) leaves the carousels waiting as they are.

Start over (restyle = FRESH; Review's "Start over", or a RELEASE with RELEASE_FRESH, which also rejects
each waiting draft into Done > Archived and starts its idea over as a new production: restart): a fresh
edit, not a revision. Bryan, 2026-10-21: "take out the 3 'to review' videos ... redo them entirely because i'm unsure
if repeated cutting is the issue". A Reel is planned from the original plan and footage as if it were
the first draft: no previous cut list, graphics or key words, no Studio's read of the old cut (editor.
_fresh_block); the version number carries on, his open notes still go in as notes and the earlier
versions stay. A carousel's slides are rewritten from the plan's own slides.
"""
import logging

import catalog
import settings

log = logging.getLogger("studio.restyle")

# Bump when the look changes enough that drafts waiting for review should be made again.
# RELEASE_FRESH: this release starts them over (a fresh edit from the footage) instead of revising them.
RELEASE = "2026-10-22 Site Notes graphics"
RELEASE_FRESH = False
# What the release's Telegram says (a revision release: the waiting drafts are made again in the new look).
RELEASE_NEWS = "Studio's new graphics look (Site Notes) is in"
# RELEASE_REELS_ONLY: the release changes how Reels look (titles, captions, graphics, the end card), not carousels'
# slides, so a carousel waiting for review is left as it is (a slideshow Reel is a Reel: made again).
RELEASE_REELS_ONLY = True
# The productions.restyle values: made again in today's look (a revision), or started over.
AGAIN, FRESH = 1, 2
# A fresh release rejects each waiting draft (it stays in Done > Archived with this note) and starts
# its idea over as a new edit (Bryan, 2026-10-21: "automatically reject all the 3 'to be reviewed'
# videos and redo them based on the revised style, rules, etc."). The idea is never dropped.
REJECT_NOTE = "Rejected for a fresh edit after the endings fix"

# What a restyled carousel's rewrite works from (in place of Bryan's notes; not learned as a rule).
CAROUSEL_NOTE = {
    "id": 0, "slide": None,
    "body": "Studio's look and style guide were updated: rewrite these slides and the caption in the current "
            "style guide, playbook and voice. Keep the idea, the facts, the order of the story and the "
            "pictures; change wording only where the style guide asks for it.",
}


def _mark_file():
    return settings.ROOT / "style_release.txt"


def waiting(conn) -> list[int]:
    return [r["id"] for r in conn.execute(
        "SELECT id FROM productions WHERE status = 'ready' AND removed_at IS NULL AND archived_at IS NULL ORDER BY id")]


def mark(conn, ids: list[int], fresh: bool = False) -> int:
    """Queue these drafts (waiting for review) to be made again in today's look; fresh: started over
    (a fresh edit from the footage, not a revision of the last cut)."""
    if not ids:
        return 0
    marks = ",".join("?" * len(ids))
    n = conn.execute(f"""UPDATE productions SET restyle = ?, status = 'revising', updated_at = ?
                         WHERE id IN ({marks}) AND status = 'ready'""",
                     (FRESH if fresh else AGAIN, catalog.now(), *ids)).rowcount
    conn.commit()
    return n


def restart(conn, ids: list[int], note: str = REJECT_NOTE) -> list[tuple[int, int]]:
    """Reject these drafts (waiting for review) and start each one's idea over: the draft is marked
    rejected and archived (Done > Archived, with `note` among its notes, its files and history kept),
    and a new production of the same idea takes its place, queued as a fresh edit (FRESH) from the
    plan and footage. The version number carries on, Bryan's open notes move to it (they go in as
    notes), Studio's read of the old cut doesn't. The idea points at the new one. Each draft is done
    in one transaction, and only a draft still waiting for review is touched, so running it again
    never rejects or copies twice. Returns [(old id, new id)]."""
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(productions)")]
    fresh_start = {"status": "revising", "restyle": FRESH, "video_path": None, "thumb_path": None,
                   "duration": None, "qa": None, "review": None, "review_version": None, "self_fix": None, "error": None,
                   "archived_at": None, "removed_at": None, "removed_bytes": None}
    done = []
    for pid in ids:
        row = conn.execute("SELECT * FROM productions WHERE id = ? AND status = 'ready' AND removed_at IS NULL",
                           (pid,)).fetchone()
        if row is None:
            continue
        stamp = catalog.now()
        values = {c: row[c] for c in cols if c != "id"}
        values.update({k: v for k, v in fresh_start.items() if k in values}, updated_at=stamp)
        with conn:
            new = conn.execute(f"INSERT INTO productions ({', '.join(values)}) VALUES ({', '.join('?' * len(values))})",
                               list(values.values())).lastrowid
            conn.execute("""UPDATE feedback SET target_id = ? WHERE target = 'production' AND target_id = ?
                            AND status = 'open'""", (new, pid))
            conn.execute("""INSERT INTO feedback (target, target_id, version, body, status, created_at)
                            VALUES ('production', ?, ?, ?, 'addressed', ?)""", (pid, row["version"], note, stamp))
            conn.execute("""UPDATE productions SET status = 'rejected', archived_at = ?, restyle = NULL, updated_at = ?
                            WHERE id = ?""", (stamp, stamp, pid))
            conn.execute("UPDATE ideas SET production_id = ? WHERE production_id = ?", (new, pid))
        log.info("Draft %s (v%s) rejected for a fresh edit; its idea starts over as production %s", pid,
                 row["version"], new)
        done.append((pid, new))
    return done


def run_once(conn) -> int:
    """On the first start after a new RELEASE: queue every draft waiting for review (every Reel, with
    RELEASE_REELS_ONLY). Returns how many."""
    path = _mark_file()
    try:
        if path.read_text(encoding="utf-8").strip() == RELEASE:
            return 0
    except OSError:
        pass
    ids = waiting(conn)
    if RELEASE_REELS_ONLY:
        ids = [i for i in ids if conn.execute("SELECT format FROM productions WHERE id = ?", (i,)).fetchone()[0] != "carousel"]
    n = len(restart(conn, ids)) if RELEASE_FRESH else mark(conn, ids)
    path.write_text(RELEASE + "\n", encoding="utf-8")
    if n:
        import notify
        log.info("New release (%s): %s %s drafts", RELEASE, "starting over" if RELEASE_FRESH else "making again", n)
        drafts = f"the {n} draft{'s' if n != 1 else ''} waiting for review"
        notify.send((f"Studio's editing changed: gentler cuts, a proper ending, and it now listens to and watches each draft "
                     f"before you see it. It rejected {drafts} (they're "
                     "kept in Done > Archived) and started them over from your footage, one at a time; the new edits "
                     "come back to To review."
                     if RELEASE_FRESH else
                     f"{RELEASE_NEWS}. It's making {drafts} again in it, one at a time; they come back "
                     "to To review.") + f"\n{notify.dashboard_link('review')}")
    return n
