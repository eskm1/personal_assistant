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

# How many message pairs (user + assistant) to keep per user
MAX_HISTORY_PAIRS = 20
