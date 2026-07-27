import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")

# Comma-separated Telegram user IDs that are allowed to use the bot
ALLOWED_USER_IDS: set[int] = set(
    int(uid.strip())
    for uid in os.getenv("ALLOWED_USER_IDS", "").split(",")
    if uid.strip().isdigit()
)

# Urban Makers (umcpm) Supabase integration
UMCPM_SUPABASE_URL = os.getenv("UMCPM_SUPABASE_URL", "https://rlcigpzbjuigpjnewimm.supabase.co")
UMCPM_SERVICE_KEY = os.getenv("UMCPM_SERVICE_KEY", "")
UMCPM_BASE_URL = os.getenv("UMCPM_BASE_URL", "https://umcpm.netlify.app")

MS_CLIENT_ID = os.getenv("MS_CLIENT_ID", "")
MS_TENANT_ID = os.getenv("MS_TENANT_ID", "common")

# IANA timezone name used for Outlook calendar events, e.g. "America/New_York"
USER_TIMEZONE = os.getenv("USER_TIMEZONE", "UTC")

# Set HEADLESS=1 on the server so interactive OAuth flows fail fast with
# instructions instead of hanging on a machine with no browser/console.
HEADLESS = os.getenv("HEADLESS", "") == "1"

# ── Second brain vault (personal knowledge inbox) ─────────────────────────────
# The bot writes captures to the vault's GitHub repo over HTTP, since it runs on a
# server with no access to the laptop where the vault lives.
# Token: a fine-grained GitHub PAT with Contents read+write on the second-brain repo only.
SECOND_BRAIN_GITHUB_TOKEN = os.getenv("SECOND_BRAIN_GITHUB_TOKEN", "")
SECOND_BRAIN_REPO = os.getenv("SECOND_BRAIN_REPO", "eskm1/second-brain")
SECOND_BRAIN_BRANCH = os.getenv("SECOND_BRAIN_BRANCH", "main")
SECOND_BRAIN_INBOX_PATH = os.getenv("SECOND_BRAIN_INBOX_PATH", "00 Inbox/telegram.md")

# Vault folder holding the daily journal (one YYYY-MM-DD.md note per day).
# A journal is an ongoing personal responsibility, so it lives under 20 Areas.
JOURNAL_PATH = os.getenv("JOURNAL_PATH", "20 Areas/Journal")

# ── Personal blog (bryanjlum.com) ─────────────────────────────────────────────
# Static Astro site on Cloudflare Pages: publishing a post = committing a markdown
# file to the site repo, which triggers a deploy. Token: a fine-grained GitHub PAT
# with Contents read+write on the blog repo only.
BLOG_GITHUB_TOKEN = os.getenv("BLOG_GITHUB_TOKEN", "")
BLOG_REPO = os.getenv("BLOG_REPO", "eskm1/bryanjlum.com")
BLOG_BRANCH = os.getenv("BLOG_BRANCH", "main")
BLOG_POSTS_PATH = os.getenv("BLOG_POSTS_PATH", "src/content/blog")
BLOG_BASE_URL = os.getenv("BLOG_BASE_URL", "https://bryanjlum.com")

# ── Ava's proactive push ──────────────────────────────────────────────────────
# SGT hour (0-23) when Ava DMs the morning brief (Bob's world: due today,
# overdue, completed, new tasks, report headlines) to every allowed user.
# Set AVA_PUSH_HOUR=-1 to disable. Quiet days send nothing.
AVA_PUSH_HOUR = int(os.getenv("AVA_PUSH_HOUR", "7"))

# SGT hour (0-23) when Ava DMs the nightly journal reminder (the three prompts,
# usually answered with a voice note). Set AVA_JOURNAL_HOUR=-1 to disable.
AVA_JOURNAL_HOUR = int(os.getenv("AVA_JOURNAL_HOUR", "21"))

# How many message pairs (user + assistant) to keep per user
MAX_HISTORY_PAIRS = 20
