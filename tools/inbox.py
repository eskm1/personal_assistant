"""
Second-brain vault inbox capture.

The bot runs on a remote server and has no access to Bryan's laptop, where the
plain-markdown second-brain vault lives. So captures are written to the vault's
GitHub repo over HTTP (the GitHub Contents API), the same "write to a cloud
backend, not a local file" shape the wiki tool uses for Supabase. Bryan sees the
captures in Obsidian after his next `git pull`.

Everything lands in `00 Inbox/telegram.md`, raw and unpolished, grouped under a
`## YYYY-MM-DD` heading with an HH:MM (SGT) timestamp. Filing happens at the
weekly review. Capture is non-destructive, so unlike the wiki edits it writes
immediately with no confirmation gate - friction is the enemy of capture.
"""
import base64
import json
from datetime import datetime
from urllib.parse import quote
from zoneinfo import ZoneInfo

import requests

from config import (
    SECOND_BRAIN_GITHUB_TOKEN,
    SECOND_BRAIN_REPO,
    SECOND_BRAIN_BRANCH,
    SECOND_BRAIN_INBOX_PATH,
)

_API = "https://api.github.com"
_SGT = ZoneInfo("Asia/Singapore")

# Written only if the inbox file does not exist yet in the repo. Kept in sync with
# the seeded 00 Inbox/telegram.md in the vault.
_SEED = (
    "# Telegram captures\n\n"
    "Raw notes captured from Telegram (via `/note` or by asking the bot to note something).\n"
    "Unpolished by design. File these into PARA at the weekly review; this file should trend toward empty.\n"
)


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {SECOND_BRAIN_GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _contents_url() -> str:
    # Encode the path but keep the folder separators (the space in "00 Inbox" must be escaped).
    return f"{_API}/repos/{SECOND_BRAIN_REPO}/contents/{quote(SECOND_BRAIN_INBOX_PATH, safe='/')}"


def _append_once(text: str) -> str:
    """Fetch the inbox file, append one capture, and write it back. One attempt.

    Returns the HH:MM SGT stamp on success. Raises requests.HTTPError on a stale
    sha (409) so the caller can retry with a fresh fetch.
    """
    url = _contents_url()

    r = requests.get(url, headers=_headers(), params={"ref": SECOND_BRAIN_BRANCH}, timeout=15)
    if r.status_code == 200:
        payload = r.json()
        sha = payload["sha"]
        content = base64.b64decode(payload["content"]).decode("utf-8")
    elif r.status_code == 404:
        sha = None
        content = _SEED
    else:
        r.raise_for_status()
        return ""  # unreachable, keeps type checkers happy

    now = datetime.now(_SGT)
    day = now.strftime("%Y-%m-%d")
    stamp = now.strftime("%H:%M")

    body = content.rstrip("\n")
    if f"## {day}" not in body:
        body += f"\n\n## {day}"
    entry = text.strip().replace("\r\n", "\n")
    body += f"\n- {stamp} {entry}"
    new_content = body + "\n"

    put_body = {
        "message": f"inbox: capture via Telegram ({day} {stamp} SGT)",
        "content": base64.b64encode(new_content.encode("utf-8")).decode("ascii"),
        "branch": SECOND_BRAIN_BRANCH,
    }
    if sha:
        put_body["sha"] = sha

    r2 = requests.put(url, headers=_headers(), data=json.dumps(put_body), timeout=15)
    r2.raise_for_status()
    return stamp


def append_to_inbox(text: str) -> str:
    """Capture raw text into the vault inbox. Returns a user-facing status string."""
    if not text or not text.strip():
        return "Nothing to capture - the note was empty."
    if not SECOND_BRAIN_GITHUB_TOKEN:
        return "⚠️ Capture is not set up yet - SECOND_BRAIN_GITHUB_TOKEN is missing on the server."

    try:
        # One retry: a 409 means another capture wrote between our GET and PUT, so
        # the sha we sent is stale. Re-fetch and try again.
        for attempt in range(2):
            try:
                stamp = _append_once(text)
                return f"✅ Captured to inbox at {stamp} SGT. File it at your weekly review."
            except requests.HTTPError as e:
                status = e.response.status_code if e.response is not None else None
                if status == 409 and attempt == 0:
                    continue
                if status in (401, 403):
                    return "⚠️ Capture failed: the GitHub token is missing, expired, or lacks write access to the vault repo."
                raise
    except Exception as e:
        return f"⚠️ Capture failed: {e}"


# ── Claude tool (natural-language / voice capture) ────────────────────────────
# Immediate, no confirmation gate: capture is non-destructive and must stay
# friction-free. Business/operational knowledge belongs in the wiki, not here.

def capture_note(text: str) -> str:
    return append_to_inbox(text)


TOOL_DEFS = [
    {
        "name": "capture_note",
        "description": (
            "Capture a quick PERSONAL note into Bryan's second-brain vault inbox. "
            "Use for personal captures: ideas, reminders to himself, things to mull over, "
            "journal snippets, code/dev lessons, personal finance/health/admin. "
            "Saves immediately, no confirmation needed. "
            "Do NOT use this for Urban Makers operational or business knowledge (SOPs, pricing, "
            "suppliers, client processes) - that belongs in the wiki via create_wiki_article/append_to_wiki_article. "
            "Rule of thumb: if a new hire would need it, it's wiki; if Bryan would take it with him after selling the "
            "company, it's a personal note for here."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "The note to capture, verbatim or lightly cleaned up. Keep Bryan's own wording.",
                },
            },
            "required": ["text"],
        },
    },
]

DISPATCH = {
    "capture_note": capture_note,
}
