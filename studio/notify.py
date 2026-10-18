"""
Send-only Telegram messages to Bryan through Ava's bot. Activity is batched into
one digest per burst of uploads or edits ("20 new videos processed, see …")
rather than a message per video. Send failures are logged, never raised: a
missed ping must not fail work that was done fine.
"""
import logging
import threading
import time
from collections import Counter

import requests

from settings import DASHBOARD_PORT, DASHBOARD_URL, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

log = logging.getLogger("studio.notify")

def configured() -> bool:
    return bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)


def send(text: str) -> bool:
    if not configured():
        log.info("Telegram not configured; would have sent: %s", text)
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "disable_web_page_preview": True},
            timeout=20,
        )
        r.raise_for_status()
        return True
    except requests.RequestException as e:
        log.warning("Telegram send failed: %s", e)
        return False


# ── Digest ─────────────────────────────────────────────────────────────────────
# One message per batch instead of one per video. Events pile up while uploads
# and edits are still in progress and go out as a single summary once things
# settle, with a link to the dashboard for the details.

QUIET_SECONDS = 60          # wait this long after the last event...
MAX_HOLD_SECONDS = 30 * 60  # ...but never sit on news longer than this


def dashboard_link(tab: str) -> str:
    base = DASHBOARD_URL or f"http://127.0.0.1:{DASHBOARD_PORT}"
    return f"{base}/#/tab/{tab}"


def planner_link() -> str:
    """Where the team plans: the planner when planning is online (at /plan/ on the
    Studio machine, else the Netlify address), otherwise the dashboard's Plan tab."""
    import cloud_sync
    import settings
    if cloud_sync.enabled():
        if DASHBOARD_URL:
            return f"{DASHBOARD_URL}/plan/"
        if settings.PLANNER_URL:
            return settings.PLANNER_URL + "/"
    return dashboard_link("plan")


class Digest:
    def __init__(self):
        self._lock = threading.Lock()
        self._counts: Counter = Counter()
        self._notes: dict[str, str] = {}
        self._first = self._last = 0.0

    def add(self, kind: str, n: int = 1) -> None:
        """kind: processed | retake | matched | duplicate | unreadable | failed | draft | edit_failed
        | ideas_new | ideas_attached (ideas from new footage)"""
        if n <= 0:
            return
        with self._lock:
            now = time.time()
            if not self._counts:
                self._first = now
            self._counts[kind] += n
            self._last = now

    def note(self, key: str, text: str) -> None:
        """A word for the next message (ideas_when: "today's footage")."""
        with self._lock:
            self._notes[key] = text

    def message(self, counts: Counter, notes: dict | None = None) -> str:
        def n(k, one, many):
            c = counts[k]
            return f"{c} {one if c == 1 else many}"
        lines = []
        if counts["processed"]:
            extras = [x for x in (
                counts["retake"] and n("retake", "flagged for a retake", "flagged for a retake"),
                counts["duplicate"] and n("duplicate", "duplicate skipped", "duplicates skipped"),
                counts["unreadable"] and n("unreadable", "file couldn't be read", "files couldn't be read"),
                counts["failed"] and n("failed", "will be retried", "will be retried"),
            ) if x]
            lines.append(f"{n('processed', 'new video', 'new videos')} processed"
                         + (f" ({', '.join(extras)})" if extras else "") + ".")
            lines.append(f"See categories, quality and transcripts: {dashboard_link('library')}")
            if counts["matched"]:
                lines.append(f"{n('matched', 'planned shot', 'planned shots')} got footage: {dashboard_link('plan')}")
        elif counts["duplicate"] or counts["unreadable"] or counts["failed"]:
            lines.append("Uploads need a look: " + ", ".join(x for x in (
                counts["duplicate"] and n("duplicate", "duplicate skipped", "duplicates skipped"),
                counts["unreadable"] and n("unreadable", "file couldn't be read", "files couldn't be read"),
                counts["failed"] and n("failed", "failed and will be retried", "failed and will be retried"),
            ) if x) + f". {dashboard_link('ingesting')}")
        if counts["ideas_new"] or counts["ideas_attached"]:
            parts = [x for x in (
                counts["ideas_new"] and n("ideas_new", "new post idea", "new post ideas"),
                counts["ideas_attached"] and n("ideas_attached", "clip added to existing ideas",
                                               "clips added to existing ideas")) if x]
            lines.append(" and ".join(parts).capitalize() + f" from {(notes or {}).get('ideas_when') or 'the new footage'}"
                         f": {planner_link()}")
        if counts["draft"] or counts["edit_failed"]:
            drafts = [x for x in (counts["draft"] and n("draft", "draft ready to review", "drafts ready to review"),
                                  counts["edit_failed"] and n("edit_failed", "edit failed", "edits failed")) if x]
            lines.append(", ".join(drafts).capitalize() + f": {dashboard_link('review' if counts['draft'] else 'producing')}")
        return "Studio: " + "\n".join(lines)

    def flush(self, busy: bool = False, force: bool = False) -> bool:
        """Send the summary if there's news and things have settled (or force)."""
        with self._lock:
            if not self._counts:
                return False
            now = time.time()
            if not force:
                if busy and now - self._first < MAX_HOLD_SECONDS:
                    return False
                if now - self._last < QUIET_SECONDS:
                    return False
            counts, self._counts = self._counts, Counter()
            notes, self._notes = self._notes, {}
        if not +counts:
            return False
        return send(self.message(counts, notes))


digest = Digest()
