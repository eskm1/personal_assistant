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
# Reference reels (README section 14): videos dropped in <inbox>\References\Ours or
# \Inspiration become references in the online planner and are moved to REFERENCES.
REFERENCES = ROOT / "References"
REFERENCE_INBOX = INBOX / "References"

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

# ── Online planning (Supabase; README section 12) ────────────────────────────
# The team plans on the online planner; the worker syncs with it whenever it runs
# (cloud_sync.py). Blank = planning stays on this dashboard. The service key is a
# secret: it only ever goes to STUDIO_SUPABASE_URL.
SUPABASE_URL = _env("STUDIO_SUPABASE_URL").rstrip("/")
SUPABASE_SERVICE_KEY = _env("STUDIO_SUPABASE_SERVICE_KEY")
PLANNER_URL = _env("STUDIO_PLANNER_URL").rstrip("/")
CLOUD_SYNC_SECONDS = int(_env("STUDIO_CLOUD_SYNC_SECONDS", "30"))

# Notes marked "remember for future edits" are appended here; the editor reads it.
PLAYBOOK = ROOT / "playbook.md"

# ── Accounts ──────────────────────────────────────────────────────────────────
# Which Instagram accounts Studio makes content for. For now everything is Urban
# Makers, even clips that sound personal. "urban_makers,personal" brings the
# personal account back (clips are then tagged per account again).
KNOWN_BRANDS = ("urban_makers", "personal")
BRANDS = tuple(b for b in (x.strip() for x in _env("STUDIO_BRANDS", "urban_makers").split(","))
               if b in KNOWN_BRANDS) or ("urban_makers",)
SINGLE_BRAND = BRANDS[0] if len(BRANDS) == 1 else None


def brands_text() -> str:
    """brands.md, as Claude reads it when tagging, planning and suggesting posts.
    With one account, prefaced by the rule that everything is for that account."""
    path = Path(BRANDS_FILE)
    text = path.read_text(encoding="utf-8").strip() if path.exists() else ""
    if SINGLE_BRAND == "urban_makers":
        text = ("For now, every post is for the Urban Makers account, including clips and ideas that "
                "sound personal (Bryan's day, his opinions, life running the business): file them under "
                "urban_makers and shape them for the Urban Makers audience. The personal account below "
                "is on hold.\n\n" + text)
    return text

# ── OneDrive (server) ─────────────────────────────────────────────────────────
# On the server, footage still arrives through OneDrive: onedrive.py pulls it with
# rclone (remote set up by server_move.py) into INBOX, and moves each picked-up
# original into the archive folder so OneDrive keeps a backup. Approved reels are
# copied to the "ready" folder for posting from the phone. Folder names are paths
# inside OneDrive. Blank inbox = OneDrive intake off (the laptop's synced folder).
ONEDRIVE_REMOTE = _env("STUDIO_ONEDRIVE_REMOTE", "onedrive")
ONEDRIVE_INBOX = _env("STUDIO_ONEDRIVE_INBOX").strip("/\\")
ONEDRIVE_ARCHIVE = _env("STUDIO_ONEDRIVE_ARCHIVE", "Studio Archive").strip("/\\")
ONEDRIVE_READY = _env("STUDIO_ONEDRIVE_READY", "Studio Ready to post").strip("/\\")
RCLONE = _env("STUDIO_RCLONE", "rclone")


def ensure_folders() -> None:
    for folder in (ROOT, INBOX, LIBRARY, REVIEW, APPROVED, LOGS):
        folder.mkdir(parents=True, exist_ok=True)
