"""
Settings for the studio worker — the always-on home PC that turns phone footage
into tagged, searchable clips (phase 1 of the Content Studio plan).

Kept separate from the bot's config.py on purpose: the bot runs on the Linux
server, this runs on the Windows laptop at home, and neither should need the
other's secrets or dependencies. Values come from studio/.env.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
load_dotenv(HERE / ".env")


def _path(name: str, default: Path) -> Path:
    value = os.getenv(name, "").strip()
    return Path(value).expanduser() if value else default


# ── Folders ───────────────────────────────────────────────────────────────────
# ROOT holds everything the worker owns. INBOX is where the phone's uploads land —
# usually a Google Drive folder synced by Drive for desktop, so it can live
# outside ROOT. Processed clips are MOVED out of the inbox into the library, which
# also frees the Drive space they used.
ROOT = _path("STUDIO_ROOT", Path.home() / "Studio")
INBOX = _path("STUDIO_INBOX", ROOT / "Inbox")
LIBRARY = ROOT / "Library"
REVIEW = ROOT / "Review"
APPROVED = ROOT / "Approved"
LOGS = ROOT / "logs"
CATALOG_DB = ROOT / "catalog.db"

VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".webm"}

# A file is only picked up once it has stopped changing for this long, so a
# half-synced upload is never processed.
POLL_SECONDS = int(os.getenv("STUDIO_POLL_SECONDS", "30"))
STABLE_SECONDS = int(os.getenv("STUDIO_STABLE_SECONDS", "20"))

# ── ffmpeg ────────────────────────────────────────────────────────────────────
FFMPEG = os.getenv("FFMPEG_PATH", "ffmpeg")
FFPROBE = os.getenv("FFPROBE_PATH", "ffprobe")

# Frames Claude looks at: one every few seconds, capped so long clips stay cheap.
FRAME_EVERY_SECONDS = float(os.getenv("STUDIO_FRAME_EVERY_SECONDS", "3"))
MAX_FRAMES = int(os.getenv("STUDIO_MAX_FRAMES", "20"))
FRAME_WIDTH = int(os.getenv("STUDIO_FRAME_WIDTH", "512"))

# ── Whisper (local, faster-whisper) ───────────────────────────────────────────
# "auto" uses the eGPU when an NVIDIA card is attached and falls back to the CPU.
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "auto").lower()
# Blank = pick by device: large-v3-turbo on GPU, small on CPU.
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "").strip()
# Blank = auto-detect per clip (handles English, Mandarin, Singlish mixes).
WHISPER_LANGUAGE = os.getenv("WHISPER_LANGUAGE", "").strip() or None

# ── Claude ────────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.getenv("STUDIO_CLAUDE_MODEL", "claude-opus-5-5")
CLAUDE_EFFORT = os.getenv("STUDIO_CLAUDE_EFFORT", "medium")
BRANDS_FILE = _path("STUDIO_BRANDS_FILE", HERE / "brands.md")

# ── Telegram (send-only, through Ava's bot) ───────────────────────────────────
# Sending with the same token the server uses is safe: only getUpdates polling
# conflicts between processes, and this worker never polls.
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")


# ── Dashboard (review site) ───────────────────────────────────────────────────
# 127.0.0.1 = this laptop only. To review from your phone or another computer over
# Tailscale, set the host to 0.0.0.0 AND a password; the dashboard refuses to
# listen beyond this machine without one.
DASHBOARD_HOST = os.getenv("STUDIO_DASHBOARD_HOST", "127.0.0.1")
DASHBOARD_PORT = int(os.getenv("STUDIO_DASHBOARD_PORT", "8765"))
DASHBOARD_PASSWORD = os.getenv("STUDIO_DASHBOARD_PASSWORD", "")
# Optional: the address you open it at (e.g. your Tailscale name); added to
# Telegram pings so a notification links straight to the review.
DASHBOARD_URL = os.getenv("STUDIO_DASHBOARD_URL", "").rstrip("/")

# Notes marked "remember for future edits" are appended here; the editor reads it.
PLAYBOOK = ROOT / "playbook.md"


def ensure_folders() -> None:
    for folder in (ROOT, INBOX, LIBRARY, REVIEW, APPROVED, LOGS):
        folder.mkdir(parents=True, exist_ok=True)
