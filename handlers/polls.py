"""📊 /poll — anonymous polls using Telegram's NATIVE poll composer.

Flow:
  1. /poll  -> bot explains; user optionally sends a photo first
  2. User composes a real poll (paperclip / attach -> Poll) and sends it to the bot
  3. Question + options are moderated as one blob (photo too, if attached)
  4. Bot posts the photo (if any) to the channel, then the poll as a reply to it

Telegram does not allow an image inside a poll message, so the photo is posted
immediately above and the poll is threaded to it — visually they read as one
unit in the channel.
"""
from __future__ import annotations

import logging

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    CommandHandler, ContextTypes, ConversationHandler, MessageHandler, filters,
)

import db as dbx
import media as mediamod
import moderation
import security
import texts as T
from handlers.comments import release_ownership, take_ownership
from config import settings
from handlers.reports import report_button
from telegram import InlineKeyboardMarkup

log = logging.getLogger(__name__)

WAITING_POLL = 1


async def poll_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    db = context.bot_data["db"]
    uhash = security.user_hash(update.effective_user.id)
    await dbx.ensure_user(db, uhash, security.alias_for(uhash))
    if await dbx.is_banned(db, uhash):
        await update.message.reply_text("🚫 You are banned from posting.")
        release_ownership(context)
        return ConversationHandler.END
    wait = security.cooldown_remaining(uhash, "poll", settings.CONFESS_COOLDOWN_SECONDS)
    if wait:
        await update.message.reply_text(f"⏳ Slow down — try again in {wait}s.")
        release_ownership(context)
        return ConversationHandler.END
    context.user_data.pop("poll_photo", None)
    take_ownership(context)
    await update.message.reply_text(T.POLL_INTRO, parse_mode=ParseMode.HTML)
    return WAITING_POLL


async def poll_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Optional image to sit above the poll."""
    media = await mediamod.extract(update.message, context.bot)
    if not media:
        return WAITING_POLL
    context.user_data["poll_photo"] = media
    await update.message.reply_text(
        "📸 Photo saved — it'll go directly above your poll.\n"
        "Now send the poll itself (attach ➜ Poll)."
    )
    return WAITING_POLL


async def poll_receive(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    poll = update.message.poll
    if poll is None:
        await update.message.reply_text(
            "That's not a poll. Use the attach button (📎) ➜ Poll to compose one, "
            "or /cancel."
        )
        return WAITING_POLL

    question = poll.question
    options = [o.text for o in poll.options]
    media = context.user_data.pop("poll_photo", None)

    db = context.bot_data["db"]
    result = await moderation.moderate(
        f"POLL QUESTION: {question}\nOPTIONS: " + " | ".join(options),
        db, kind="poll",
        image_b64=media.b64 if media else None,
        image_mime=media.mime if media else "image/jpeg",
    )
    if media and not media.b64:
        result.verdict = "REVIEW"
        result.reason = "Attached image could not be scanned"

    if result.verdict != "ALLOW":
        if result.verdict == "REJECT":
            uhash = security.user_hash(update.effective_user.id)
            strikes = await dbx.add_strike(db, uhash)
            await update.message.reply_text(
                f"❌ Poll blocked: {result.reason}\n"
                f"Strike {strikes}/{settings.MAX_STRIKES_BEFORE_BAN}."
            )
        else:
            await update.message.reply_text(
                f"🕐 That poll needs a human look and wasn't posted: {result.reason}"
            )
        release_ownership(context)
        return ConversationHandler.END

    try:
        reply_to = None
        if media and media.kind == "photo":
            shot = await context.bot.send_photo(settings.CHANNEL_ID, media.file_id)
            reply_to = shot.message_id
        elif media and media.kind == "animation":
            shot = await context.bot.send_animation(settings.CHANNEL_ID, media.file_id)
            reply_to = shot.message_id

        sent = await context.bot.send_poll(
            settings.CHANNEL_ID, question, options,
            is_anonymous=True,
            allows_multiple_answers=poll.allows_multiple_answers,
            reply_to_message_id=reply_to,
            reply_markup=InlineKeyboardMarkup([[report_button(f"poll{0}")]]),
        )
        # Reference polls by their channel message id for reporting.
        await context.bot.edit_message_reply_markup(
            settings.CHANNEL_ID, sent.message_id,
            reply_markup=InlineKeyboardMarkup([[report_button(f"poll{sent.message_id}")]]),
        )
        await update.message.reply_text("✅ Poll posted anonymously! 📊")
    except Exception:  # noqa: BLE001
        log.exception("Poll send failed")
        await update.message.reply_text("⚠️ Couldn't post the poll — tell an admin.")
    release_ownership(context)
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("poll_photo", None)
    release_ownership(context)
    await update.message.reply_text("Cancelled. 👌")
    release_ownership(context)
    return ConversationHandler.END


async def _escape(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """User typed a different command mid-conversation: end this one."""
    context.user_data.clear()
    cmd = (update.message.text or "").split()[0]
    await update.message.reply_text(f"↩️ Cancelled. Send {cmd} again to start it.")
    release_ownership(context)
    return ConversationHandler.END


def build_handlers():
    return [ConversationHandler(
        entry_points=[CommandHandler("poll", poll_start, filters.ChatType.PRIVATE)],
        states={WAITING_POLL: [
            MessageHandler(filters.POLL, poll_receive),
            MessageHandler(filters.PHOTO | filters.ANIMATION, poll_photo),
            MessageHandler(filters.TEXT & ~filters.COMMAND, poll_receive),
        ]},
        fallbacks=[
            CommandHandler("cancel", cancel),
            MessageHandler(filters.COMMAND, _escape),
        ],
        conversation_timeout=180,
    )]
