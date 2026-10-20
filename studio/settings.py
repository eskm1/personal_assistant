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


def _flag(name: str, default: bool) -> bool:
    """An on/off setting: 0, off, false or no turn it off; blank = the default."""
    return _env(name, "1" if default else "0").lower() not in ("0", "off", "false", "no")


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
# The languages clips are spoken (and subtitled) in. Auto-detection picks the likeliest
# of these, so Singlish isn't heard as Malay. Chinese always comes out in Simplified
# characters.
WHISPER_LANGUAGES = tuple(x.strip() for x in _env("WHISPER_LANGUAGES", "en,zh").split(",") if x.strip())

# ── Claude ────────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = _env("ANTHROPIC_API_KEY")
CLAUDE_MODEL = _env("STUDIO_CLAUDE_MODEL", "claude-opus-5-5")
CLAUDE_EFFORT = _env("STUDIO_CLAUDE_EFFORT", "medium")
BRANDS_FILE = _path("STUDIO_BRANDS_FILE", HERE / "brands.md")

# ── AI concept images (carousels; concepts.py) ────────────────────────────────
# Google Gemini's image model draws concept pictures for carousel slides. The key is
# Studio's own (Google AI Studio, billing on), not the quote tool's; it only ever goes
# to GEMINI_URL, in a header, and is never logged. Blank = no concept pictures.
GEMINI_API_KEY = _env("GEMINI_API_KEY")
# Nano Banana 2.1 (Google's recommended image model since 2026-10-06; the older
# gemini-3.1-flash-image is deprecated and its -preview was shut down in June 2026).
IMAGE_MODEL = _env("STUDIO_IMAGE_MODEL", "gemini-nano-banana-2.1")
# What one picture costs (US$, Google's list price for a 1K image), for the cost lines.
IMAGE_COST_USD = float(_env("STUDIO_IMAGE_COST_USD", "0.034"))
GEMINI_URL = _env("STUDIO_GEMINI_URL", "https://generativelanguage.googleapis.com/v1beta").rstrip("/")

# Review plays a light 720p copy of each draft and clip (previews.py), made as drafts go to review
# and, for older ones, in the background. Off = no new light copies; Review plays what's there.
LIGHT_COPIES = _flag("STUDIO_LIGHT_COPIES", True)
# The home-screen app opens from its last copy of the page (web/sw.js); off removes it from phones.
SERVICE_WORKER = _flag("STUDIO_SERVICE_WORKER", True)

# ── The editor's cutting and framing ──────────────────────────────────────────
# Jump cuts: inside every stretch the editor keeps, silences, breaths and lone "um"s
# longer than EDIT_MAX_PAUSE seconds are cut down to a short breath.
EDIT_JUMP_CUTS = _flag("STUDIO_EDIT_JUMP_CUTS", True)
EDIT_MAX_PAUSE = float(_env("STUDIO_EDIT_MAX_PAUSE", "0.35"))
# Reframing: horizontal footage (Z fc, GoPro) is cropped around the person's face
# instead of down the middle. Off = the centre crop.
EDIT_REFRAME = _flag("STUDIO_EDIT_REFRAME", True)
# Punch-ins: on talking shots, every 4-7 seconds the picture cuts between normal and a
# closer framing (EDIT_PUNCH_ZOOM, 1.12 = 112%), so a long shot never sits still.
EDIT_PUNCH_INS = _flag("STUDIO_EDIT_PUNCH_INS", True)
EDIT_PUNCH_ZOOM = float(_env("STUDIO_EDIT_PUNCH_ZOOM", "1.12"))
# Drawn inserts: the editor may lay a short animation from the motion library (motion.py) over
# the footage where Bryan explains something the footage doesn't show, or to cover a missing
# shot, while his voice and the captions carry on. At most EDIT_SCENES_MAX per draft (and about
# one per 15 seconds). Off (or a max of 0) = no inserts.
EDIT_SCENES = _flag("STUDIO_EDIT_SCENES", True)
EDIT_SCENES_MAX = min(max(int(_env("STUDIO_EDIT_SCENES_MAX", "3") or 3), 0), 6)
# Studio fixes its own list (self_fix.py): after a draft renders and Studio's read runs, the
# fixes the editor can make itself go back to the editor, up to this many rounds, before Bryan
# sees the draft. Each round is one edit plan and one read (a few tens of cents). 0 = off.
SELF_FIX_ROUNDS = min(max(int(_env("STUDIO_SELF_FIX_ROUNDS", "2")), 0), 4)
# Editing rules Studio learns from the problems it fixed and from Bryan's editing notes,
# kept in their own section of the playbook (learning.py); at most this many per kind of post.
LEARN_RULES = _flag("STUDIO_LEARN_RULES", True)
LEARNED_RULES_MAX = min(max(int(_env("STUDIO_LEARNED_RULES_MAX", "20")), 3), 60)
# Captions, hook and graphics font, from the fonts bundled in studio/render (editor.CAPTION_FONTS):
# figtree, nunito, dm_sans or outfit.
CAPTION_FONT = _env("STUDIO_CAPTION_FONT", "figtree").lower().replace("-", "_").replace(" ", "_")
# How far the music sits under the voice while Bryan talks (dB); between phrases it rises a
# little, and with no talking it plays at a comfortable bed level.
MUSIC_GAP_DB = min(max(float(_env("STUDIO_MUSIC_GAP_DB", "20")), 12.0), 30.0)

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
# The planner's public (anon / publishable) key, for serving the planner at /plan/ on
# this machine. Blank = read it once from the Netlify planner's public config.js.
SUPABASE_ANON_KEY = _env("STUDIO_SUPABASE_ANON_KEY")
# Other web addresses (besides STUDIO_PLANNER_URL) whose planner may upload footage
# straight to a shot (uploads.py), comma-separated, e.g. a Netlify preview.
UPLOAD_ORIGINS = {o.strip().rstrip("/") for o in _env("STUDIO_UPLOAD_ORIGINS").split(",") if o.strip()}
# Footage uploaded from the planner waits here until it's complete.
UPLOADS = ROOT / "Uploads"
# Ideas from new footage (footage_ideas.py): once new clips stop arriving for this many
# minutes (a shoot day's uploads are done), Claude suggests posts from them and adds them
# to the posts already planned. 0 turns it off (the planner's button still works).
IDEAS_AFTER_MINUTES = int(_env("STUDIO_IDEAS_AFTER_MINUTES", "20"))
# Suggested topics (topics.py): once a week, on this day at this hour Singapore time, Claude
# suggests a few posts for the month's gaps (TOPICS_COUNT, default 4). "off" = no weekly run
# (the planner's "Suggest topics" button still works). TOPICS_BEHIND: also when a kind is
# short past mid-month and no suggestion covers it (at most once a day).
TOPICS_DAY = _env("STUDIO_TOPICS_DAY", "monday").lower()
TOPICS_HOUR = min(max(int(_env("STUDIO_TOPICS_HOUR", "8")), 0), 23)
TOPICS_COUNT = min(max(int(_env("STUDIO_TOPICS_COUNT", "4")), 0), 8)
TOPICS_BEHIND = _flag("STUDIO_TOPICS_BEHIND", True)

# Who may sign in to the dashboard: emails at these domains (comma-separated; "*" = anyone).
# Owner accounts are always allowed, whatever their address.
ALLOWED_EMAIL_DOMAINS = tuple(d.strip().lower().lstrip("@") for d in
                              _env("STUDIO_ALLOWED_EMAIL_DOMAINS", "theurbanmakers.com").split(",") if d.strip())

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

# ── Reference library (ref_library.py, taste.py) ──────────────────────────────
# The most Studio may spend in a calendar month on analysing bulk-imported references and
# writing taste reports (US$; about 0.05 a post). Nothing is spent until the owner presses
# "Analyse" in the planner; past this, analysing pauses until next month.
REFERENCE_BUDGET_USD = max(float(_env("STUDIO_REFERENCE_BUDGET_USD", "60")), 0.0)

# ── Posting to Instagram (instagram.py, posting.py, post_results.py) ──────────
# Studio posts only what Bryan approved and scheduled, through "Instagram API with Instagram
# Login" (graph.instagram.com, no Facebook Page). The access token, app ID and app secret are
# pasted once by the owner under More > Instagram and kept only on this machine,
# in INSTAGRAM_FILE (readable by Studio only); never online, never logged.
INSTAGRAM_FILE = ROOT / "instagram.json"
INSTAGRAM_GRAPH_URL = _env("STUDIO_INSTAGRAM_GRAPH_URL", "https://graph.instagram.com").rstrip("/")
INSTAGRAM_API_VERSION = _env("STUDIO_INSTAGRAM_API_VERSION", "v25.0")
# Off = nothing is published (scheduled posts wait; "Post now" refuses). A kill switch.
POSTING = _flag("STUDIO_POSTING", True)
# Where Instagram fetches the video and slides from while a post goes out (a short-lived link
# to this machine, /public/media/...). Blank = STUDIO_DASHBOARD_URL. Must be https on the server.
PUBLIC_URL = (_env("STUDIO_PUBLIC_URL") or DASHBOARD_URL).rstrip("/")
# Posts a week the schedule suggests slots for; 0 = the content plan's (Plan > Content plan, default 3).
POSTS_PER_WEEK = min(max(int(_env("STUDIO_POSTS_PER_WEEK", "0")), 0), 21)
# The time of day (Singapore) a suggested slot starts at.
POST_TIME = _env("STUDIO_POST_TIME", "19:30")
# Most slides Instagram takes in one carousel.
INSTAGRAM_CAROUSEL_MAX = min(max(int(_env("STUDIO_INSTAGRAM_CAROUSEL_MAX", "10")), 2), 20)
# The handle on the animated explainers' end card (motion.py), small under the mark. The connected
# Instagram account's own name wins when there is one; "off" = no handle.
INSTAGRAM_HANDLE = _env("STUDIO_INSTAGRAM_HANDLE", "urbanmakers_sg").strip().lstrip("@")
# The files of a post on its way to Instagram (the checked video, the slides as JPEGs).
POSTING_DIR = ROOT / "Posting"
# Results: every post of the last 90 days is read once a day at this hour (Singapore), and each
# new post again about an hour after it went out.
RESULTS_HOUR = min(max(int(_env("STUDIO_RESULTS_HOUR", "9")), 0), 23)
