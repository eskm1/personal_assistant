"""
Second-brain vault browsing and inbox filing (PARA sorting).

Implements the vault's own documented "file my inbox" workflow (see the vault's
CLAUDE.md): Claude reads the inbox and vault structure, proposes a destination
and filename per entry, and only moves things after Bryan confirms. All writes
go through the pending-confirmation gate, batched so one confirm applies a whole
weekly-review filing plan.

Reuses the second-brain GitHub token/repo the inbox capture already uses. Reads
are immediate; writes are staged. The inbox file is only rewritten AFTER every
target note has been written, so a mid-batch failure can duplicate an entry
(inbox + target) but never lose one.
"""
import base64
import json
import re
from urllib.parse import quote

import requests

from config import (
    SECOND_BRAIN_GITHUB_TOKEN,
    SECOND_BRAIN_REPO,
    SECOND_BRAIN_BRANCH,
    SECOND_BRAIN_INBOX_PATH,
)
from tools import pending

_API = "https://api.github.com"

# Writes are confined to the PARA tree.
_ALLOWED_FOLDERS = ("00 Inbox/", "10 Projects/", "20 Areas/", "30 Resources/", "40 Archive/")
_MAX_MOVES = 30


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {SECOND_BRAIN_GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _contents_url(path: str) -> str:
    return f"{_API}/repos/{SECOND_BRAIN_REPO}/contents/{quote(path, safe='/')}"


def _token_missing() -> str | None:
    if not SECOND_BRAIN_GITHUB_TOKEN:
        return "⚠️ Vault access is not set up — SECOND_BRAIN_GITHUB_TOKEN is missing on the server."
    return None


def _get_file(path: str) -> tuple[str | None, str | None]:
    """Return (sha, decoded content) or (None, None) when the file doesn't exist."""
    r = requests.get(_contents_url(path), headers=_headers(),
                     params={"ref": SECOND_BRAIN_BRANCH}, timeout=15)
    if r.status_code == 404:
        return None, None
    r.raise_for_status()
    payload = r.json()
    return payload["sha"], base64.b64decode(payload["content"]).decode("utf-8")


def _put_file(path: str, content: str, message: str, sha: str | None = None) -> None:
    body = {
        "message": message,
        "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
        "branch": SECOND_BRAIN_BRANCH,
    }
    if sha:
        body["sha"] = sha
    r = requests.put(_contents_url(path), headers=_headers(), data=json.dumps(body), timeout=15)
    r.raise_for_status()


def _bad_write_path(path: str) -> str | None:
    if ".." in path or path.startswith("/"):
        return f"invalid path '{path}'"
    if not path.endswith(".md"):
        return f"'{path}' must be a .md file"
    if not path.startswith(_ALLOWED_FOLDERS):
        return f"'{path}' is outside the PARA folders ({', '.join(f.rstrip('/') for f in _ALLOWED_FOLDERS)})"
    return None


def _tidy_inbox(text: str) -> str:
    """After removing filed entries: drop day headings left with no content and
    collapse the blank lines the removals leave behind."""
    lines = text.split("\n")
    result: list[str] = []
    i = 0
    while i < len(lines):
        if lines[i].startswith("## "):
            j = i + 1
            while j < len(lines) and not lines[j].startswith("## "):
                j += 1
            if not any(l.strip() for l in lines[i + 1 : j]):
                i = j
                continue
        result.append(lines[i])
        i += 1
    tidied = re.sub(r"\n{3,}", "\n\n", "\n".join(result))
    return tidied.rstrip("\n") + "\n"


# ── Read tools ────────────────────────────────────────────────────────────────

def list_vault() -> str:
    """List every markdown note in the vault, grouped by folder."""
    missing = _token_missing()
    if missing:
        return missing
    try:
        r = requests.get(
            f"{_API}/repos/{SECOND_BRAIN_REPO}/git/trees/{SECOND_BRAIN_BRANCH}",
            headers=_headers(), params={"recursive": "1"}, timeout=15,
        )
        r.raise_for_status()
        paths = sorted(
            t["path"] for t in r.json().get("tree", [])
            if t["type"] == "blob" and t["path"].endswith(".md")
        )
        if not paths:
            return "The vault has no markdown notes."
        lines, current = [], None
        for p in paths:
            folder = p.rsplit("/", 1)[0] if "/" in p else "(root)"
            if folder != current:
                current = folder
                lines.append(f"\n{folder}/")
            lines.append(f"  {p.rsplit('/', 1)[-1]}")
        return "Vault notes:" + "\n".join(lines)
    except Exception as e:
        return f"Vault list error: {e}"


def read_vault_note(path: str) -> str:
    """Read one vault note (or the inbox file) by its repo path."""
    missing = _token_missing()
    if missing:
        return missing
    try:
        _sha, content = _get_file(path)
        if content is None:
            return f"No note at '{path}'. Use list_vault to see paths."
        return f"Path: {path}\n\n{content}"
    except Exception as e:
        return f"Vault read error: {e}"


# ── Filing executors (run only after confirmation) ────────────────────────────

def _do_file_entries(moves: list[dict]) -> str:
    try:
        inbox_sha, inbox = _get_file(SECOND_BRAIN_INBOX_PATH)
        if inbox is None:
            return f"❌ Inbox file '{SECOND_BRAIN_INBOX_PATH}' not found."

        not_found = [m["inbox_entry"][:60] for m in moves if m["inbox_entry"] not in inbox]
        if not_found:
            return (
                "❌ Nothing was changed — these entries are no longer in the inbox "
                "(already filed, or the text doesn't match exactly): "
                + "; ".join(f"'{t}…'" for t in not_found)
            )

        # Write every target first; only then rewrite the inbox.
        done = []
        for m in moves:
            target = (m.get("target_path") or "").strip()
            if not target:
                done.append(f"deleted: {m['inbox_entry'][:50]}")
                continue
            sha, existing = _get_file(target)
            note_md = m.get("content", "").strip()
            if existing is not None:
                new_content = existing.rstrip("\n") + "\n\n" + note_md + "\n"
                _put_file(target, new_content, f"vault: file inbox entry into {target}", sha)
                done.append(f"appended to {target}")
            else:
                _put_file(target, note_md + "\n", f"vault: new note {target} (filed from inbox)")
                done.append(f"created {target}")

        for m in moves:
            inbox = inbox.replace(m["inbox_entry"], "", 1)
        _put_file(
            SECOND_BRAIN_INBOX_PATH, _tidy_inbox(inbox),
            f"vault: file {len(moves)} inbox entries (weekly review via Telegram)", inbox_sha,
        )
        return "✅ Inbox filed:\n" + "\n".join(f"• {d}" for d in done) + "\nPull the vault to see it in Obsidian."
    except requests.HTTPError as e:
        status = e.response.status_code if e.response is not None else None
        if status in (401, 403):
            return "❌ Filing failed: the vault GitHub token is missing, expired, or lacks write access."
        return f"❌ Filing failed partway ({e}). Check the inbox — already-filed entries may still be listed there."
    except Exception as e:
        return f"❌ Filing failed partway ({e}). Check the inbox — already-filed entries may still be listed there."


def _do_move_note(from_path: str, to_path: str) -> str:
    try:
        sha, content = _get_file(from_path)
        if content is None:
            return f"❌ No note at '{from_path}'."
        dest_sha, dest = _get_file(to_path)
        if dest is not None:
            return f"❌ '{to_path}' already exists — pick another name or append with file_inbox_entries."
        _put_file(to_path, content, f"vault: move {from_path} -> {to_path}")
        r = requests.delete(
            _contents_url(from_path), headers=_headers(),
            data=json.dumps({
                "message": f"vault: move {from_path} -> {to_path}",
                "sha": sha,
                "branch": SECOND_BRAIN_BRANCH,
            }),
            timeout=15,
        )
        r.raise_for_status()
        return f"✅ Moved {from_path} -> {to_path}. Pull the vault to see it in Obsidian."
    except Exception as e:
        return f"❌ Move failed ({e}) — check whether the note now exists in both places."


# ── Write tools (stage via the confirmation gate) ─────────────────────────────

def file_inbox_entries(moves: list[dict]) -> str:
    """Stage a batch inbox-filing plan for confirmation (does not write yet)."""
    missing = _token_missing()
    if missing:
        return missing
    if not moves:
        return "No moves given."
    if len(moves) > _MAX_MOVES:
        return f"Too many moves ({len(moves)}); stage at most {_MAX_MOVES} per batch."

    problems = []
    for i, m in enumerate(moves, 1):
        if not (m.get("inbox_entry") or "").strip():
            problems.append(f"move {i}: inbox_entry is empty")
            continue
        target = (m.get("target_path") or "").strip()
        if target:
            bad = _bad_write_path(target)
            if bad:
                problems.append(f"move {i}: {bad}")
            if not (m.get("content") or "").strip():
                problems.append(f"move {i}: content is empty (required when target_path is set)")
    if problems:
        return "Not staged — fix these first: " + "; ".join(problems)

    lines = []
    for m in moves:
        snippet = " ".join(m["inbox_entry"].split())[:60]
        target = (m.get("target_path") or "").strip()
        lines.append(f"• '{snippet}' → {target if target else 'DELETE (drop from inbox)'}")
    summary = f"File {len(moves)} inbox entries:\n" + "\n".join(lines)
    return pending.stage(summary, lambda: _do_file_entries(moves))


def move_vault_note(from_path: str, to_path: str) -> str:
    """Stage moving/renaming a whole note for confirmation (does not write yet)."""
    missing = _token_missing()
    if missing:
        return missing
    from_path, to_path = from_path.strip(), to_path.strip()
    bad = _bad_write_path(to_path)
    if bad:
        return f"Not staged — {bad}."
    if from_path == to_path:
        return "Not staged — source and destination are the same."
    summary = f"Move vault note {from_path} → {to_path}"
    return pending.stage(summary, lambda: _do_move_note(from_path, to_path))


# ── Tool definitions (Anthropic schema) ──────────────────────────────────────

TOOL_DEFS = [
    {
        "name": "list_vault",
        "description": (
            "List every note in Bryan's second-brain vault grouped by PARA folder "
            "(00 Inbox, 10 Projects, 20 Areas, 30 Resources, 40 Archive). "
            "Use this first when sorting the inbox or answering 'what do I know about X'."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "read_vault_note",
        "description": (
            "Read one vault note by its repo path (e.g. '00 Inbox/telegram.md', "
            "'20 Areas/health.md'). Read the inbox and any candidate target notes "
            "before proposing a filing plan."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Note path from list_vault"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "file_inbox_entries",
        "description": (
            "Stage a batch filing plan that moves entries OUT of the inbox file into PARA notes "
            "(one confirmation applies the whole batch). Does NOT write immediately. "
            "Each move: inbox_entry = the entry's EXACT text as it appears in the inbox file "
            "(the '- HH:MM ...' line, including any following '![](attachments/...)' line); "
            "target_path = existing note to append to, or a new kebab-case .md path to create "
            "(omit it to just delete a junk entry); content = the cleaned-up markdown to write "
            "(rewrite the raw capture: drop the timestamp, one sentence per line, plain dash not em dash, "
            "add [[wikilinks]] to related notes, re-embed photos as ![[filename.jpg]] so they resolve "
            "from any folder). Only call after Bryan has approved the plan in chat."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "moves": {
                    "type": "array",
                    "description": "One item per inbox entry being filed or deleted.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "inbox_entry": {"type": "string", "description": "Exact text of the entry in the inbox file, verbatim."},
                            "target_path": {"type": "string", "description": "Destination note path in a PARA folder; omit to delete the entry."},
                            "content": {"type": "string", "description": "Cleaned-up markdown to append/create at target_path. Required when target_path is set."},
                        },
                        "required": ["inbox_entry"],
                    },
                },
            },
            "required": ["moves"],
        },
    },
    {
        "name": "move_vault_note",
        "description": (
            "Stage moving or renaming a WHOLE note to another PARA folder, e.g. a finished project "
            "to '40 Archive/' or a standalone note out of '00 Inbox/'. Does NOT write immediately — "
            "one confirmation per move. Destination must not already exist."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "from_path": {"type": "string", "description": "Current note path"},
                "to_path": {"type": "string", "description": "New path, kebab-case .md inside a PARA folder"},
            },
            "required": ["from_path", "to_path"],
        },
    },
]

DISPATCH = {
    "list_vault": list_vault,
    "read_vault_note": read_vault_note,
    "file_inbox_entries": file_inbox_entries,
    "move_vault_note": move_vault_note,
}
