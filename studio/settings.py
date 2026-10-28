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
# Studio's read of a draft (draft_review.py): the step that judges quality and lists the fixes. Bryan,
# 2026-10-21: "stick to high then we re-evaluate" (low/medium/high/xhigh/max; higher = more thinking, more cost).
READ_EFFORT = _env("STUDIO_READ_EFFORT", "high")
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

# ── Help, feedback and usage (help_desk.py, activity.py, usage_report.py) ─────
# Ask Studio: questions a person may ask Claude per day (Singapore day), and the model (the main one
# unless a cheaper one is set).
HELP_DAILY = max(int(_env("STUDIO_HELP_DAILY", "30")), 0)
HELP_MODEL = _env("STUDIO_HELP_MODEL", CLAUDE_MODEL)
# Usage capture in Review and the planner (page views, clicks, dead clicks, cancelled dialogs, errors; never
# content). Off = nothing is recorded and the pages stop sending. Kept this many days.
USAGE = _flag("STUDIO_USAGE", True)
USAGE_DAYS = max(int(_env("STUDIO_USAGE_DAYS", "180")), 7)
# The weekly report's key (GET /api/report/weekly, "Authorization: Bearer <it>"), made on first start.
REPORT_TOKEN_FILE = ROOT / "report_token"

# Review plays a light 720p copy of each draft and clip (previews.py), made as drafts go to review
# and, for older ones, in the background. Off = no new light copies; Review plays what's there.
LIGHT_COPIES = _flag("STUDIO_LIGHT_COPIES", True)
# The home-screen app opens from its last copy of the page (web/sw.js); off removes it from phones.
SERVICE_WORKER = _flag("STUDIO_SERVICE_WORKER", True)

# ── The editor's cutting and framing ──────────────────────────────────────────
# Jump cuts: inside every stretch the editor keeps, a pause between sentences or clauses (after a
# full stop or comma, or any pause over a second) longer than EDIT_MAX_PAUSE seconds is cut down to
# a natural breath, and lone "um"s go. A short pause inside a sentence is never cut (Bryan,
# 2026-10-21: "it just cuts out words mid-speech"; it was 0.35 s, every pause, before).
EDIT_JUMP_CUTS = _flag("STUDIO_EDIT_JUMP_CUTS", True)
EDIT_MAX_PAUSE = float(_env("STUDIO_EDIT_MAX_PAUSE", "0.7"))
# The close of every Reel: after the last word, a short beat on the last shot, then the Urban
# Makers end card (the mark, the Instagram handle, the post's ask) while the music fades out.
# Off = the draft ends after that beat, with no card.
END_CARD = _flag("STUDIO_END_CARD", True)
# Voice-overs (voiceover.py; Bryan, 2026-10-21: film B-roll on site, talk in the studio after): when a post's
# footage is a talking head filmed in the studio plus site B-roll, the site footage fills the screen over his voice
# with him in a small window in a bottom corner, full screen for his first and last sentence. Off = such posts are
# edited as before (the B-roll cut in between his sentences).
EDIT_VOICEOVER = _flag("STUDIO_EDIT_VOICEOVER", True)
# Slideshow Reels (slideshow.py: a carousel's slides as a Reel with music): each slide very slowly grows to
# 103% while it's up (the whole slide, never cropped). Off = the slides stay still.
SLIDESHOW_PUSH_IN = _flag("STUDIO_SLIDESHOW_PUSH_IN", True)
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
# Picture inserts (pictures.py; Bryan, 2026-10-22: "Gemini OK"): where a real-looking thing (a glass panel, a
# fluted panel, a terrazzo finish, a finished look) explains it better than a drawing, the editor may lay an AI
# picture (Gemini, concepts.py, marked "Concept") over the footage instead. At most REEL_PICTURES_MAX new ones a
# draft (one picture each, a second only when the check finds it wrong). Needs GEMINI_API_KEY; off = drawings.
REEL_PICTURES = _flag("STUDIO_REEL_PICTURES", True)
REEL_PICTURES_MAX = min(max(int(_env("STUDIO_REEL_PICTURES_MAX", "4") or 4), 0), 8)
# Words behind Bryan (behind.py; Bryan, 2026-10-28: "Have words pop up behind my head together in the background"):
# big words in the background that he stands in front of, for the hook or a key line, at most EDIT_BEHIND_MAX a Reel
# (2 at most). Needs the person finder (ai-edge-litert, its model fetched once into Studio/Models). Off = words on top.
EDIT_BEHIND = _flag("STUDIO_EDIT_BEHIND", True)
EDIT_BEHIND_MAX = min(max(int(_env("STUDIO_EDIT_BEHIND_MAX", "2") or 2), 0), 2)
# Studio fixes its own list (self_fix.py): after a draft renders and Studio's read runs, the
# fixes the editor can make itself are made, up to this many rounds, before Bryan sees the draft.
# Most are made directly (a caption word, a stutter, a pause, a cut point, a card, the hook, the
# music level: no new edit plan, so a round is one render and one read); only what nothing smaller
# can fix goes back to the editor for a new plan. 0 = off.
SELF_FIX_ROUNDS = min(max(int(_env("STUDIO_SELF_FIX_ROUNDS", "3")), 0), 5)
# What Studio may spend on its read, its own fix rounds and the caption spell-check, per draft
# version (US$). When the next step would pass it, Studio stops and says so on the draft.
SELF_FIX_MAX_USD = max(float(_env("STUDIO_SELF_FIX_MAX_USD", "1.50")), 0.0)
# Studio listens back to and watches through every finished Reel before Bryan sees it
# (final_check.py: words cut short or said twice, broken sentences, an ending mid-sentence; titles
# over the face, flashes, words gone before they can be read, the same animation twice, anything
# still moving at the close). Free (no Claude); what it finds goes into Studio's own fix rounds.
FINAL_CHECK = _flag("STUDIO_FINAL_CHECK", True)
# The caption spell-check before each render (spelling.py): one short text-only Claude call that
# catches words Whisper mis-heard (MCSC for MCST), against the trade glossary (Studio/glossary.txt)
# and the brief. Off = only the glossary's own "wrong -> right" lines are applied. The model is the
# main one unless a cheaper one is set (claude-haiku-5-5 costs about a tenth).
SPELL_CHECK = _flag("STUDIO_SPELL_CHECK", True)
SPELL_MODEL = _env("STUDIO_SPELL_MODEL", CLAUDE_MODEL)
# Editing rules Studio learns from the problems it fixed and from Bryan's editing notes,
# kept in their own section of the playbook (learning.py); at most this many per kind of post.
LEARN_RULES = _flag("STUDIO_LEARN_RULES", True)
LEARNED_RULES_MAX = min(max(int(_env("STUDIO_LEARNED_RULES_MAX", "20")), 3), 60)
# Captions font (and the small text under titles), from the fonts bundled in studio/render
# (editor.CAPTION_FONTS): inter, figtree, nunito, dm_sans or outfit.
CAPTION_FONT = _env("STUDIO_CAPTION_FONT", "inter").lower().replace("-", "_").replace(" ", "_")
# The look of the hook, callouts, cards, captions and end card (editor.TITLE_STYLES): site (Site Notes,
# Bryan's pick of 2026-10-22: one bold font, words on dark labels, the key word in brass, graphics over
# the footage), gallery (the look before: a serif title with one italic word, captions glow, a key word
# in serif italic) or classic (the first look: boxed titles with the amber bar, captions on a box).
TITLE_STYLE = _env("STUDIO_TITLE_STYLE", "site").lower().strip()
# The brand kit (brand_kit.py, Review > More > Brand kit: fonts, colours, logo, per kind of post) starts from
# TITLE_STYLE's look until it's saved. Bryan changes it; on = the team may change it too (everyone can see it).
BRAND_KIT_TEAM = _flag("STUDIO_BRAND_KIT_TEAM", False)
# How far the music sits under the voice while Bryan talks (dB); between phrases it rises a
# little, and with no talking it plays at a comfortable bed level.
MUSIC_GAP_DB = min(max(float(_env("STUDIO_MUSIC_GAP_DB", "20")), 12.0), 30.0)
# Soft sound effects on the graphics (sfx.py; Bryan, 2026-10-26: "calm, never jarring"): a whoosh as a label comes
# in, ticks on a numbers panel, a chime on the end card. Epidemic Sound's when its server offers them, else
# Openverse's CC0 / public domain ones, fetched once and kept. SFX_LEVEL_DB: how far their loudest moment sits under
# the loud parts of the voice; SFX_MAX: at most this many a draft (and about one per 6 seconds). Off = voice and
# music only, as before.
SFX = _flag("STUDIO_SFX", True)
SFX_LEVEL_DB = min(max(float(_env("STUDIO_SFX_LEVEL_DB", "24")), 12.0), 40.0)
SFX_MAX = min(max(int(_env("STUDIO_SFX_MAX", "5") or 5), 0), 12)
OPENVERSE_URL = _env("STUDIO_OPENVERSE_URL", "https://api.openverse.org/v1/audio/")

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
# Urban Makers' wiki (wiki.py): the quote engine's trade articles, read-only, as a source of topics.
# The address (the Supabase REST base), a read-only role's token and the anon key; never logged.
# Any of them blank = topics without the wiki.
WIKI_URL = _env("STUDIO_WIKI_URL").rstrip("/")
WIKI_JWT = _env("STUDIO_WIKI_JWT")
WIKI_ANON_KEY = _env("STUDIO_WIKI_ANON_KEY")

# Who may sign in to the dashboard: emails at these domains (comma-separated; "*" = anyone).
# Owner accounts are always allowed, whatever their address.
ALLOWED_EMAIL_DOMAINS = tuple(d.strip().lower().lstrip("@") for d in
                              _env("STUDIO_ALLOWED_EMAIL_DOMAINS", "theurbanmakers.com").split(",") if d.strip())

# Notes marked "remember for future edits" are appended here; the editor reads it.
PLAYBOOK = ROOT / "playbook.md"
# Bryan's voice file (voice_file.py): how he sounds, drafted from his own talking-head videos and
# edited by him in Review (Playbook > Your voice). Every prompt that writes words viewers read gets it.
VOICE_FILE = ROOT / "voice.md"
# The de-AI filter on everything Studio writes that viewers read (wording.py): invisible characters
# and typography are always fixed; on (the default), text that still reads machine-written gets one
# short Claude rewrite (a cent or two) and the better version is kept. 0 = only the safe fixes.
HUMANIZE = _flag("STUDIO_HUMANIZE", True)
# Words and phrases the filter never flags for Bryan (trade terms he really says, like "hack"),
# one per line; made with a few on first use.
HUMANIZE_ALLOW = ROOT / "humanize_allow.txt"

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
# How many library posts an hour Studio may download from Instagram at most (spaced at random): a slow, steady
# rate, so Instagram keeps trusting the server and its sign-in (references added by hand always go first).
REFERENCE_LIBRARY_PER_HOUR = min(max(float(_env("STUDIO_REFERENCE_LIBRARY_PER_HOUR", "4")), 0.1), 60.0)
# YouTube references (ref_youtube.py): YouTube blocks downloads from servers, so Gemini watches the
# video from its link instead (GEMINI_API_KEY, the concept pictures' key). The model must take video
# input; the name is not checked against Google's model list from here (a wrong one fails each YouTube
# reference with a plain reason). What one costs (US$, an estimate: Google bills by tokens) counts
# toward the budget above. Only the first REFERENCE_VIDEO_MAX_SECONDS of a long video are watched.
REFERENCE_VIDEO_MODEL = _env("STUDIO_REFERENCE_VIDEO_MODEL", "gemini-3.8-flash")
REFERENCE_VIDEO_COST_USD = max(float(_env("STUDIO_REFERENCE_VIDEO_COST_USD", "0.05")), 0.0)
REFERENCE_VIDEO_MAX_SECONDS = min(max(int(_env("STUDIO_REFERENCE_VIDEO_MAX_SECONDS", "180")), 30), 1800)

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
# The planner's profile grid draws the bio header from the connected account (read with the results);
# until Instagram is connected, from these.
INSTAGRAM_NAME = _env("STUDIO_INSTAGRAM_NAME", "Urban Makers").strip()[:60]
INSTAGRAM_BIO = _env("STUDIO_INSTAGRAM_BIO", "").replace("\\n", "\n").strip()[:300]
# Small stills of the account's posts (and its picture), read with the results: the planner's grid shows
# them (thumbs bucket results/<media id>.jpg), because Instagram's own links to them run out.
IG_THUMBS = ROOT / "IGThumbs"
# The files of a post on its way to Instagram (the checked video, the slides as JPEGs).
POSTING_DIR = ROOT / "Posting"
# Results: every post of the last 90 days is read once a day at this hour (Singapore), and each
# new post again about an hour after it went out.
RESULTS_HOUR = min(max(int(_env("STUDIO_RESULTS_HOUR", "9")), 0), 23)
# What kinds of posts work (insights.py): each post scored against the account's typical post, the features
# Studio knows compared, and a memo once a week on this day at this hour (Singapore; before the suggested
# topics), one Claude call (INSIGHTS_MODEL, usage kind "insights"). STUDIO_INSIGHTS=0 turns it all off.
INSIGHTS = _flag("STUDIO_INSIGHTS", True)
INSIGHTS_DAY = _env("STUDIO_INSIGHTS_DAY", "monday").lower()
INSIGHTS_HOUR = min(max(int(_env("STUDIO_INSIGHTS_HOUR", "7")), 0), 23)
INSIGHTS_MODEL = _env("STUDIO_INSIGHTS_MODEL", CLAUDE_MODEL)
