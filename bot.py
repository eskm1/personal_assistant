import asyncio
import base64
import logging
import os
import tempfile
import traceback
from collections import defaultdict

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

from config import TELEGRAM_BOT_TOKEN, ALLOWED_USER_IDS, MAX_HISTORY_PAIRS
from router import chat
from voice import transcribe_voice
from tools.umcpm import list_umcpm_projects
from tools.inbox import append_to_inbox
from tools.pending import current_conversation

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
# The bot token appears in every Telegram API URL; httpx logs full URLs at INFO,
# which would write the token into journald on every poll. Keep these at WARNING.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram.ext").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# Conversation history, keyed by (chat_id, user_id) so DM and group threads
# stay fully isolated. Cleared on /start or /clear.
histories: dict[tuple[int, int], list[dict]] = defaultdict(list)

# Telegram hard-caps a message at 4096 chars; leave headroom.
MAX_MESSAGE_CHARS = 3900


def conv_key(update: Update) -> tuple[int, int]:
    return (update.effective_chat.id, update.effective_user.id)


# ── Auth guard ────────────────────────────────────────────────────────────────

def is_owner(user_id: int) -> bool:
    """Full access — Bryan's personal tools (email, calendar, tasks, etc.)"""
    return user_id in ALLOWED_USER_IDS


# ── Helpers ───────────────────────────────────────────────────────────────────

def _is_plain_user_message(msg: dict) -> bool:
    """A safe history boundary: a user turn that is plain text or an image+text
    message — anything except tool_result blocks (those must stay glued to the
    assistant tool_use turn before them)."""
    if msg.get("role") != "user":
        return False
    content = msg.get("content")
    if isinstance(content, str):
        return True
    return isinstance(content, list) and not any(
        isinstance(block, dict) and block.get("type") == "tool_result"
        for block in content
    )


def _parse_note_command(raw: str) -> str | None:
    """If raw starts with /note (or /note@bot), return the note text after it
    (possibly empty). Return None when it isn't a /note command at all.
    Splits only the first line's command so multi-line notes keep their newlines."""
    raw = (raw or "").lstrip()
    if not raw.startswith("/note"):
        return None
    command, _, rest = raw.partition(" ")
    if command != "/note" and not command.startswith("/note@"):
        return None
    return rest.strip()


def trim_history(history: list[dict]) -> None:
    """Trim to the last N messages, then drop leading messages until the first is a
    plain user text message — so history never begins mid tool_use/tool_result pair."""
    max_messages = MAX_HISTORY_PAIRS * 2
    if len(history) > max_messages:
        del history[:-max_messages]
    while history and not _is_plain_user_message(history[0]):
        history.pop(0)


async def send_chunked(message, text: str) -> None:
    """Send text as one or more Telegram messages, splitting on natural boundaries
    so nothing exceeds Telegram's 4096-char limit."""
    text = text or "(no response)"
    while text:
        if len(text) <= MAX_MESSAGE_CHARS:
            chunk, text = text, ""
        else:
            window = text[:MAX_MESSAGE_CHARS]
            cut = window.rfind("\n\n")
            if cut < MAX_MESSAGE_CHARS // 2:
                cut = window.rfind("\n")
            if cut < MAX_MESSAGE_CHARS // 2:
                cut = MAX_MESSAGE_CHARS
            chunk, text = text[:cut], text[cut:].lstrip("\n")
        await message.reply_text(chunk, disable_web_page_preview=True)


async def reply_from_claude(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_content: str | list,
    owner: bool = True,
) -> None:
    """user_content is either plain text or a list of content blocks (e.g. image + text)."""
    key = conv_key(update)
    history = histories[key]

    history.append({"role": "user", "content": user_content})
    trim_history(history)
    turn_start = len(history) - 1  # index of the user message we just added

    await context.bot.send_chat_action(update.effective_chat.id, "typing")

    # Tell staged/destructive tools which conversation they belong to (for the
    # confirmation gate). Set before to_thread so the copied context carries it.
    current_conversation.set(f"{key[0]}:{key[1]}")

    try:
        # chat() is blocking (network + tool loop); run it off the event loop so
        # other messages keep flowing. chat() appends the assistant turn in-place.
        response = await asyncio.to_thread(chat, history, is_owner=owner)
    except Exception:
        # Roll the whole failed turn back out of history (user msg + any partial
        # assistant/tool appends) so the next call isn't left with an orphan pair.
        del history[turn_start:]
        raise  # surfaced by the global error handler

    await send_chunked(update.message, response)


# ── Command handlers ──────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_owner(update.effective_user.id):
        return
    histories[conv_key(update)].clear()
    await update.message.reply_text(
        "Hi! I'm Blumbot, Bryan's personal assistant.\n\n"
        "You can talk to me normally or send a voice note. I can help with calendar, "
        "email, tasks, directions, and general questions — and I can pass project "
        "work to Bob, the Urban Makers WhatsApp agent.\n\n"
        "Use /clear to reset the conversation."
    )


async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_owner(update.effective_user.id):
        return
    histories[conv_key(update)].clear()
    await update.message.reply_text("Conversation cleared.")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_owner(update.effective_user.id):
        return
    await update.message.reply_text(
        "Commands:\n"
        "/start — reset and introduce myself\n"
        "/clear — clear conversation history\n"
        "/projects — list Urban Makers projects\n"
        "/note — capture a personal note to my second-brain inbox\n"
        "/help  — show this message\n\n"
        "You can also send voice notes and I'll transcribe them automatically, "
        "and just say \"note that down\" to capture something to your vault.\n"
        "Photos work too: send one and I can look at it, or caption it /note "
        "(plus any text) to save it straight into your vault inbox."
    )


async def cmd_projects(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_owner(update.effective_user.id):
        return
    await context.bot.send_chat_action(update.effective_chat.id, "typing")
    # Optional search term after the command, e.g. "/projects tan kitchen"
    query = " ".join(context.args) if context.args else ""
    result = await asyncio.to_thread(list_umcpm_projects, query)
    await send_chunked(update.message, result)


async def cmd_note(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_owner(update.effective_user.id):
        return
    # Take the raw text after the command so multi-line notes keep their newlines
    # (context.args would collapse them). Handles "/note ..." and "/note@bot ...".
    text = _parse_note_command(update.message.text) or ""
    if not text:
        await update.message.reply_text(
            "Send the note after the command, e.g.\n"
            "/note idea: telegram capture straight into my vault inbox"
        )
        return
    await context.bot.send_chat_action(update.effective_chat.id, "typing")
    result = await asyncio.to_thread(append_to_inbox, text)
    await update.message.reply_text(result)


# ── Private chat handlers ─────────────────────────────────────────────────────

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if not is_owner(user_id):
        logger.warning("Blocked unauthorized DM from user %s", user_id)
        return
    await reply_from_claude(update, context, update.message.text, owner=True)


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if not is_owner(user_id):
        logger.warning("Blocked unauthorized voice from user %s", user_id)
        return

    await context.bot.send_chat_action(update.effective_chat.id, "typing")

    voice_file = await update.message.voice.get_file()
    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        await voice_file.download_to_drive(tmp_path)
        transcript = await asyncio.to_thread(transcribe_voice, tmp_path)
    except Exception as e:
        os.unlink(tmp_path)
        err = str(e)
        if "insufficient_quota" in err or "429" in err:
            await update.message.reply_text(
                "⚠️ Voice transcription is unavailable — OpenAI account is out of credits.\n"
                "Top up at platform.openai.com/account/billing, then try again."
            )
        else:
            await update.message.reply_text(f"⚠️ Voice transcription failed: {err}")
        return
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)

    await update.message.reply_text(f'Heard: "{transcript}"')
    await reply_from_claude(update, context, transcript, owner=True)


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Photos in private chat. Telegram delivers them as a separate message type
    (photo + caption, not text), so neither the text handler nor CommandHandler
    ever sees them — including a '/note' typed as the caption.

    Two paths:
    - caption starts with /note  -> save photo + caption into the vault inbox
    - anything else              -> pass the image to Claude so the bot can see it
    """
    user_id = update.effective_user.id
    if not is_owner(user_id):
        logger.warning("Blocked unauthorized photo from user %s", user_id)
        return

    await context.bot.send_chat_action(update.effective_chat.id, "typing")

    # Largest rendition Telegram offers for a compressed photo (~1280px JPEG),
    # comfortably within the vision API's size limits.
    tg_file = await update.message.photo[-1].get_file()
    data = bytes(await tg_file.download_as_bytearray())

    caption = update.message.caption or ""
    note_text = _parse_note_command(caption)
    if note_text is not None:
        result = await asyncio.to_thread(append_to_inbox, note_text, data)
        await update.message.reply_text(result)
        return

    content = [
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/jpeg",
                "data": base64.b64encode(data).decode("ascii"),
            },
        },
        {"type": "text", "text": caption.strip() or "Bryan sent this photo with no caption."},
    ]
    await reply_from_claude(update, context, content, owner=True)


# ── Group chat handler ────────────────────────────────────────────────────────

async def handle_group_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    if not message or not message.text:
        return

    bot_username = context.bot.username

    # Only respond when @mentioned or when replying to the bot
    is_mention = any(
        e.type == "mention"
        and message.text[e.offset : e.offset + e.length].lstrip("@") == bot_username
        for e in (message.entities or [])
    )
    is_reply_to_bot = (
        message.reply_to_message is not None
        and message.reply_to_message.from_user is not None
        and message.reply_to_message.from_user.id == context.bot.id
    )

    if not is_mention and not is_reply_to_bot:
        return

    # Strip the @mention from the text before sending to Claude
    text = message.text
    if is_mention:
        text = text.replace(f"@{bot_username}", "").strip()

    if not text:
        await message.reply_text("Yes? How can I help?")
        return

    user_id = update.effective_user.id
    owner = is_owner(user_id)

    if not owner:
        logger.info("Group message from non-owner user %s — general chat only", user_id)

    await reply_from_claude(update, context, text, owner=owner)


# ── Global error handler ──────────────────────────────────────────────────────

async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Unhandled exception:\n%s",
                 "".join(traceback.format_exception(context.error)))
    if isinstance(update, Update) and update.effective_message:
        err = str(context.error) or context.error.__class__.__name__
        try:
            await update.effective_message.reply_text(f"⚠️ Something went wrong: {err[:300]}")
        except Exception:
            pass  # never let the error handler itself raise


# ── Entry point ───────────────────────────────────────────────────────────────

async def main() -> None:
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set in .env")
    if not ALLOWED_USER_IDS:
        raise RuntimeError("ALLOWED_USER_IDS is empty — set at least one Telegram user ID in .env")

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("clear", cmd_clear))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("projects", cmd_projects))
    app.add_handler(CommandHandler("note", cmd_note))

    # Private chats — full access
    private = filters.ChatType.PRIVATE
    app.add_handler(MessageHandler(private & filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(private & filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(private & filters.PHOTO, handle_photo))

    # Group / supergroup chats — @mention or reply only, tools restricted to owner
    group = filters.ChatType.GROUP | filters.ChatType.SUPERGROUP
    app.add_handler(MessageHandler(group & filters.TEXT & ~filters.COMMAND, handle_group_message))

    app.add_error_handler(on_error)

    logger.info("Bot starting, allowed users: %s", ALLOWED_USER_IDS)

    async with app:
        await app.start()
        await app.updater.start_polling()
        logger.info("Bot is running. Press Ctrl+C to stop.")
        try:
            await asyncio.Event().wait()
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            await app.updater.stop()
            await app.stop()


if __name__ == "__main__":
    asyncio.run(main())
