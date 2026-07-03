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

# How many message pairs (user + assistant) to keep per user
MAX_HISTORY_PAIRS = 20
