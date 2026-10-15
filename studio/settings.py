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


def _env(name: str, default: str = "") -> str:
    """The variable's value, or the default when it's unset OR left blank.
    .env.example lists every setting with an empty value, so a blank line must
    mean "use the default" (a plain os.getenv would return the empty string)."""
    return os.getenv(name, "").strip() or default


def _path(name: str, default: Path) -> Path:
    value = _env(name)
    return Path(value).expanduser() if value else default


# ── Folders ───────────────────────────────────────────────────────────────────
# ROOT holds everything the worker owns. INBOX is where the phone's uploads land —
# usually a OneDrive (or Google Drive) folder synced to this laptop, so it can live
# outside ROOT. Processed clips are MOVED out of the inbox into the library, which
# also frees the cloud space they used.
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
POLL_SECONDS = int(_env("STUDIO_POLL_SECONDS", "30"))
STABLE_SECONDS = int(_env("STUDIO_STABLE_SECONDS", "20"))

# ── ffmpeg ────────────────────────────────────────────────────────────────────
FFMPEG = _env("FFMPEG_PATH", "ffmpeg")
FFPROBE = _env("FFPROBE_PATH", "ffprobe")

# Frames Claude looks at: one every few seconds, capped so long clips stay cheap.
FRAME_EVERY_SECONDS = float(_env("STUDIO_FRAME_EVERY_SECONDS", "3"))
MAX_FRAMES = int(_env("STUDIO_MAX_FRAMES", "20"))
FRAME_WIDTH = int(_env("STUDIO_FRAME_WIDTH", "512"))

# ── Whisper (local, faster-whisper) ───────────────────────────────────────────
# "auto" uses an NVIDIA GPU (eGPU included) when attached and falls back to the CPU.
# faster-whisper has no Intel Arc or AMD support, so those machines use the CPU.
WHISPER_DEVICE = _env("WHISPER_DEVICE", "auto").lower()
# Blank = pick by device: large-v3-turbo on GPU, small on CPU.
WHISPER_MODEL = _env("WHISPER_MODEL")
# Blank = auto-detect per clip (handles English, Mandarin, Singlish mixes).
WHISPER_LANGUAGE = _env("WHISPER_LANGUAGE") or None

# ── Claude ────────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = _env("ANTHROPIC_API_KEY")
CLAUDE_MODEL = _env("STUDIO_CLAUDE_MODEL", "claude-opus-5-5")
CLAUDE_EFFORT = _env("STUDIO_CLAUDE_EFFORT", "medium")
BRANDS_FILE = _path("STUDIO_BRANDS_FILE", HERE / "brands.md")

# ── Epidemic Sound (music for the editor) ─────────────────────────────────────
# Personal MCP key from epidemicsound.com/account/api-keys.
EPIDEMIC_SOUND_API_KEY = _env("EPIDEMIC_SOUND_API_KEY")
EPIDEMIC_SOUND_MCP_URL = _env("EPIDEMIC_SOUND_MCP_URL", "https://www.epidemicsound.com/a/mcp-service/mcp")
# Which accounts may use Epidemic tracks (comma-separated: personal, urban_makers).
# The plan licenses one channel per platform, so list only the account registered
# with Epidemic Sound; posts for other accounts are made without Epidemic music.
MUSIC_ACCOUNTS = {a.strip() for a in _env("STUDIO_MUSIC_ACCOUNTS", "urban_makers").split(",") if a.strip()}

# ── Telegram (send-only, through Ava's bot) ───────────────────────────────────
# Sending with the same token the server uses is safe: only getUpdates polling
# conflicts between processes, and this worker never polls.
TELEGRAM_BOT_TOKEN = _env("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = _env("TELEGRAM_CHAT_ID")


# ── Dashboard (review site) ───────────────────────────────────────────────────
# 127.0.0.1 = this laptop only. To review from your phone or another computer over
# Tailscale, set the host to 0.0.0.0 AND a password; the dashboard refuses to
# listen beyond this machine without one.
DASHBOARD_HOST = _env("STUDIO_DASHBOARD_HOST", "127.0.0.1")
DASHBOARD_PORT = int(_env("STUDIO_DASHBOARD_PORT", "8765"))
# Optional: the address you open it at (e.g. your Tailscale name); added to
# Telegram pings so a notification links straight to the review.
DASHBOARD_URL = _env("STUDIO_DASHBOARD_URL").rstrip("/")

# How often the worker checks GitHub for a new version and updates itself while
# idle (updater.py). 0 turns automatic updates off ("Update now" still works).
AUTO_UPDATE_MINUTES = int(_env("STUDIO_AUTO_UPDATE_MINUTES", "10"))

# Notes marked "remember for future edits" are appended here; the editor reads it.
PLAYBOOK = ROOT / "playbook.md"


def ensure_folders() -> None:
    for folder in (ROOT, INBOX, LIBRARY, REVIEW, APPROVED, LOGS):
        folder.mkdir(parents=True, exist_ok=True)
