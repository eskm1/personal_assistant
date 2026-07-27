"""
Daily journal — voice-first journaling into the second-brain vault.

Every evening (AVA_JOURNAL_HOUR, bot.py) Ava DMs Bryan a fixed reminder with the
three journal prompts; he answers with a voice note (transcribed before it
reaches the model), and the model saves the entry with save_journal_entry.
Entries land in the vault's GitHub repo as one note per day at
'<JOURNAL_PATH>/YYYY-MM-DD.md' — the same write-to-a-cloud-backend shape as the
inbox capture. And like capture_note it writes immediately, with no
confirmation gate: friction kills a journaling habit, and the vault is private.

The three prompts live here (PROMPTS) so the pushed reminder, the system
prompt, and the note headings can never drift apart.
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
    JOURNAL_PATH,
)

_API = "https://api.github.com"
_SGT = ZoneInfo("Asia/Singapore")

# The three daily prompts, in the order they are asked (second person, for the
# reminder DM) and their first-person twins (headings inside Bryan's own note).
PROMPTS = (
    "How are you feeling today?",
    "Anything important that happened?",
    "What are you looking forward to tomorrow?",
)
_HEADINGS = (
    "How am I feeling today?",
    "Anything important that happened?",
    "What am I looking forward to tomorrow?",
)


def reminder_text() -> str:
    """The exact nightly reminder Ava pushes. Fixed text, not model-generated,
    so the ritual is dependable and the three prompts never mutate."""
    return (
        "🌙 Journal time. When you're ready, send me a voice note about your day:\n"
        f"1. {PROMPTS[0]}\n"
        f"2. {PROMPTS[1]}\n"
        f"3. {PROMPTS[2]}\n\n"
        "Text works too. I'll tidy it into tonight's note in your vault — "
        "and if tonight's not the night, just ignore me."
    )


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {SECOND_BRAIN_GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _note_path(day: str) -> str:
    return f"{JOURNAL_PATH.strip().strip('/')}/{day}.md"


def _contents_url(path: str) -> str:
    return f"{_API}/repos/{SECOND_BRAIN_REPO}/contents/{quote(path, safe='/')}"


def _render_sections(feeling: str, happened: str, looking_forward: str) -> str:
    """Only the prompts Bryan actually answered become sections."""
    parts = []
    for heading, text in zip(_HEADINGS, (feeling, happened, looking_forward)):
        text = (text or "").strip().replace("\r\n", "\n")
        if text:
            parts.append(f"## {heading}\n\n{text}")
    return "\n\n".join(parts)


def _save_once(sections: str) -> tuple[str, str]:
    """Create or append today's journal note. One attempt; raises HTTPError on a
    stale sha (409) so the caller can retry with a fresh fetch.
    Returns (path, HH:MM stamp)."""
    now = datetime.now(_SGT)
    day = now.strftime("%Y-%m-%d")
    stamp = now.strftime("%H:%M")
    path = _note_path(day)
    url = _contents_url(path)

    r = requests.get(url, headers=_headers(), params={"ref": SECOND_BRAIN_BRANCH}, timeout=15)
    if r.status_code == 200:
        payload = r.json()
        sha = payload["sha"]
        existing = base64.b64decode(payload["content"]).decode("utf-8")
        content = f"{existing.rstrip()}\n\n---\n\nAdded at {stamp}:\n\n{sections}\n"
    elif r.status_code == 404:
        sha = None
        title = now.strftime("%A, %d %B %Y")
        content = f"# Journal - {title}\n\n{sections}\n"
    else:
        r.raise_for_status()
        return path, stamp  # unreachable, keeps type checkers happy

    put_body = {
        "message": f"journal: {day} entry via Telegram ({stamp} SGT)",
        "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
        "branch": SECOND_BRAIN_BRANCH,
    }
    if sha:
        put_body["sha"] = sha
    r2 = requests.put(url, headers=_headers(), data=json.dumps(put_body), timeout=15)
    r2.raise_for_status()
    return path, stamp


def save_journal_entry(feeling: str = "", happened: str = "", looking_forward: str = "") -> str:
    """Save today's journal entry to the vault. Immediate, no confirmation gate."""
    if not SECOND_BRAIN_GITHUB_TOKEN:
        return "⚠️ Journaling is not set up yet - SECOND_BRAIN_GITHUB_TOKEN is missing on the server."
    sections = _render_sections(feeling, happened, looking_forward)
    if not sections:
        return "Nothing to save - all three answers were empty."

    try:
        # One retry: a 409 means something else wrote the note between our GET
        # and PUT, so the sha we sent is stale. Re-fetch and try again.
        for attempt in range(2):
            try:
                path, stamp = _save_once(sections)
                return f"✅ Journal saved to {path} ({stamp} SGT)."
            except requests.HTTPError as e:
                status = e.response.status_code if e.response is not None else None
                if status == 409 and attempt == 0:
                    continue
                if status in (401, 403):
                    return "⚠️ Journal save failed: the vault GitHub token is missing, expired, or lacks write access."
                raise
    except Exception as e:
        return f"⚠️ Journal save failed: {e}"


# ── Tool definitions (Anthropic schema) ──────────────────────────────────────

TOOL_DEFS = [
    {
        "name": "save_journal_entry",
        "description": (
            "Save today's journal entry into Bryan's second-brain vault (one note per day, "
            f"'{JOURNAL_PATH.strip().strip('/')}/YYYY-MM-DD.md'). Use when Bryan answers the nightly journal "
            "reminder - usually one voice note covering some or all of the three prompts - or whenever he "
            "journals spontaneously. Split his answer across the three fields; keep his own first-person words, "
            "only lightly cleaned (drop filler, fix obvious mis-transcriptions), one sentence per line, "
            "plain dash never em dash. Leave any prompt he didn't address as an empty string - never invent "
            "or pad answers. Saves immediately, no confirmation needed. If today's note already exists, "
            "the new answers are appended with a timestamp."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "feeling": {
                    "type": "string",
                    "description": f"His answer to '{PROMPTS[0]}' - empty string if not addressed.",
                },
                "happened": {
                    "type": "string",
                    "description": f"His answer to '{PROMPTS[1]}' - empty string if not addressed.",
                },
                "looking_forward": {
                    "type": "string",
                    "description": f"His answer to '{PROMPTS[2]}' - empty string if not addressed.",
                },
            },
        },
    },
]

DISPATCH = {
    "save_journal_entry": save_journal_entry,
}
