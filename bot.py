import asyncio
import logging
import os
import tempfile
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

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Per-user conversation history stored as flat message list.
# Cleared on /start or /clear.
histories: dict[int, list[dict]] = defaultdict(list)


# ── Auth guard ────────────────────────────────────────────────────────────────

def allowed(user_id: int) -> bool:
    return user_id in ALLOWED_USER_IDS


# ── Helpers ───────────────────────────────────────────────────────────────────

def trim_history(history: list[dict]) -> None:
    max_messages = MAX_HISTORY_PAIRS * 2
    if len(history) > max_messages:
        history[:] = history[-max_messages:]


async def reply_from_claude(update: Update, context: ContextTypes.DEFAULT_TYPE, user_text: str) -> None:
    user_id = update.effective_user.id
    history = histories[user_id]

    history.append({"role": "user", "content": user_text})
    trim_history(history)

    await context.bot.send_chat_action(update.effective_chat.id, "typing")

    response = chat(history)
    history.append({"role": "assistant", "content": response})

    await update.message.reply_text(response)


# ── Command handlers ──────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not allowed(update.effective_user.id):
        return
    histories[update.effective_user.id].clear()
    await update.message.reply_text(
        "Hi! I'm your personal assistant.\n\n"
        "You can talk to me normally or send a voice note. I can help with your calendar, "
        "email, tasks, Telegram messages, directions, and general questions.\n\n"
        "Use /clear to reset the conversation."
    )


async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not allowed(update.effective_user.id):
        return
    histories[update.effective_user.id].clear()
    await update.message.reply_text("Conversation cleared.")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not allowed(update.effective_user.id):
        return
    await update.message.reply_text(
        "Commands:\n"
        "/start — reset and introduce myself\n"
        "/clear — clear conversation history\n"
        "/help  — show this message\n\n"
        "You can also send voice notes and I'll transcribe them automatically."
    )


# ── Message handlers ──────────────────────────────────────────────────────────

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if not allowed(user_id):
        logger.warning("Blocked unauthorized user %s", user_id)
        return
    await reply_from_claude(update, context, update.message.text)


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if not allowed(user_id):
        logger.warning("Blocked unauthorized user %s", user_id)
        return

    await context.bot.send_chat_action(update.effective_chat.id, "typing")

    voice_file = await update.message.voice.get_file()
    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        await voice_file.download_to_drive(tmp_path)
        transcript = transcribe_voice(tmp_path)
    except Exception as e:
        os.unlink(tmp_path)
        err = str(e)
        if "insufficient_quota" in err or "429" in err:
            await update.message.reply_text(
                "⚠️ Voice transcription is unavailable — your OpenAI account is out of credits.\n"
                "Top up at platform.openai.com/account/billing, then try again."
            )
        else:
            await update.message.reply_text(f"⚠️ Voice transcription failed: {err}")
        return
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)

    # Echo the transcript so the user can confirm what was heard
    await update.message.reply_text(f'_Heard:_ "{transcript}"', parse_mode="Markdown")

    await reply_from_claude(update, context, transcript)


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
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))

    logger.info("Bot starting, allowed users: %s", ALLOWED_USER_IDS)

    async with app:
        await app.start()
        await app.updater.start_polling()
        logger.info("Bot is running. Press Ctrl+C to stop.")
        try:
            await asyncio.Event().wait()  # block until interrupted
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            await app.updater.stop()
            await app.stop()


if __name__ == "__main__":
    asyncio.run(main())
