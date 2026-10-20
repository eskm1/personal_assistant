"""
Redo the drafts waiting for review when Studio's look changes. Bryan, 2026-10-20: "as soon as the
style guide is re-released, just refresh all of the work in progress posts ... trigger a redo of all
of these carousels and whatever with the refreshed style guide."

Once per RELEASE (Studio/style_release.txt remembers the last one): every Reel and carousel in
To review is made again as its next version. A Reel gets a fresh edit (today's titles, captions and
music taste); a carousel's slides are rewritten in the current style guide and voice and laid out
again. The idea, Bryan's notes and the earlier versions stay. Review's "Edit it again" does the same
for one draft or all of them.
"""
import logging

import catalog
import settings

log = logging.getLogger("studio.restyle")

# Bump when the look changes enough that drafts waiting for review should be made again.
RELEASE = "2026-10-20 gallery titles, italic key words, music taste"

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


def mark(conn, ids: list[int]) -> int:
    """Queue these drafts (waiting for review) to be made again in today's look."""
    if not ids:
        return 0
    marks = ",".join("?" * len(ids))
    n = conn.execute(f"""UPDATE productions SET restyle = 1, status = 'revising', updated_at = ?
                         WHERE id IN ({marks}) AND status = 'ready'""", (catalog.now(), *ids)).rowcount
    conn.commit()
    return n


def run_once(conn) -> int:
    """On the first start after a new RELEASE: queue every draft waiting for review. Returns how many."""
    path = _mark_file()
    try:
        if path.read_text(encoding="utf-8").strip() == RELEASE:
            return 0
    except OSError:
        pass
    n = mark(conn, waiting(conn))
    path.write_text(RELEASE + "\n", encoding="utf-8")
    if n:
        import notify
        log.info("New look (%s): making %s drafts again", RELEASE, n)
        notify.send(f"Studio's new look is in. It's making the {n} draft{'s' if n != 1 else ''} waiting for "
                    f"review again in it, one at a time; they come back to To review.\n{notify.dashboard_link('review')}")
    return n
