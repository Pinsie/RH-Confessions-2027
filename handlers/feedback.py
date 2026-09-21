"""📨 JCRC Direct Line — anonymous feedback with two-way reply.

Resident: /feedback in PM -> sends message -> moderated -> forwarded to the
JCRC group tagged #FB<id>. Sender's chat id is stored Fernet-encrypted only.

JCRC side (in the JCRC group): /fbreply <id> <message> -> routed back to the
sender anonymously. Neither side learns the other's identity; JCRC replies are
attributed simply as "JCRC". Feedback mappings are purged after
ANON_CHAT_TTL_DAYS alongside the other relays.

If JCRC_GROUP_ID isn't configured, feedback lands in the admin group instead.
"""
from __future__ import annotations

import html
import logging

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    CommandHandler, ContextTypes, ConversationHandler, MessageHandler, filters,
)

import db as dbx
import moderation
import security
import texts as T
from handlers.comments import release_ownership, take_ownership
from config import settings

log = logging.getLogger(__name__)

WAITING_FB = 1


def _jcrc_chat() -> int:
    return settings.JCRC_GROUP_ID or settings.ADMIN_GROUP_ID


async def feedback_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    db = context.bot_data["db"]
    uhash = security.user_hash(update.effective_user.id)
    await dbx.ensure_user(db, uhash, security.alias_for(uhash))
    if await dbx.is_banned(db, uhash):
        await update.message.reply_text("🚫 You are banned.")
        release_ownership(context)
        return ConversationHandler.END
    wait = security.cooldown_remaining(uhash, "feedback", settings.COMMENT_COOLDOWN_SECONDS)
    if wait:
        await update.message.reply_text(f"⏳ Slow down — try again in {wait}s.")
        release_ownership(context)
        return ConversationHandler.END
    take_ownership(context)
    await update.message.reply_text(T.FEEDBACK_INTRO, parse_mode=ParseMode.HTML)
    return WAITING_FB


async def feedback_receive(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip()
    if not text:
        await update.message.reply_text("Please send text, or /cancel.")
        return WAITING_FB

    db = context.bot_data["db"]
    result = await moderation.moderate(text, db, kind="feedback")
    if result.verdict == "REJECT":
        uhash = security.user_hash(update.effective_user.id)
        strikes = await dbx.add_strike(db, uhash)
        await update.message.reply_text(
            f"❌ Not sent: {result.reason}\nStrike {strikes}/{settings.MAX_STRIKES_BEFORE_BAN}."
        )
        release_ownership(context)
        return ConversationHandler.END
    # REVIEW feedback still goes through — it's a private line to JCRC, not a
    # public post — but flagged so JCRC sees the AI's concern.
    flag = f"\n\n⚠️ AI note: {html.escape(result.reason)}" if result.verdict == "REVIEW" else ""

    enc = security.encrypt_chat_id(update.effective_chat.id)
    fid = await dbx.create_feedback(db, enc, text)

    try:
        await context.bot.send_message(
            _jcrc_chat(),
            f"📨 <b>Anonymous feedback #FB{fid}</b>\n---\n{html.escape(text)}\n---{flag}\n\n"
            f"Reply with: <code>/fbreply {fid} your message</code>",
            parse_mode=ParseMode.HTML,
        )
        await update.message.reply_text(
            "✅ Sent to the JCRC anonymously! If they reply, it'll arrive here. 📨"
        )
    except Exception:  # noqa: BLE001
        log.exception("Feedback forward failed")
        await update.message.reply_text("⚠️ Couldn't deliver — tell an admin.")
    release_ownership(context)
    return ConversationHandler.END


async def fbreply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """JCRC group: /fbreply <id> <message>."""
    if update.effective_chat.id not in (settings.JCRC_GROUP_ID, settings.ADMIN_GROUP_ID):
        return
    args = context.args or []
    if len(args) < 2 or not args[0].lstrip("#FB").isdigit():
        await update.message.reply_text("Usage: /fbreply <id> <message>")
        return
    db = context.bot_data["db"]
    fb = await dbx.get_feedback(db, int(args[0].lstrip("#FB")))
    if not fb:
        await update.message.reply_text("Feedback not found (or expired).")
        return
    dest = security.decrypt_chat_id(fb["enc_chat"])
    if not dest:
        await update.message.reply_text("Couldn't decrypt routing — expired key?")
        return
    text = " ".join(args[1:])
    try:
        await context.bot.send_message(
            dest,
            f"📨 <b>JCRC replied to your feedback #FB{fb['id']}</b>:\n\n{html.escape(text)}",
            parse_mode=ParseMode.HTML,
        )
        await update.message.reply_text("✅ Reply delivered.")
    except Exception:  # noqa: BLE001
        await update.message.reply_text("Couldn't deliver (they may have blocked the bot).")


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    release_ownership(context)
    await update.message.reply_text("Cancelled. 👌")
    release_ownership(context)
    return ConversationHandler.END


async def _escape(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """User typed a different command mid-conversation: end this one and let the
    new command's handler pick it up on their next send."""
    context.user_data.clear()
    cmd = (update.message.text or "").split()[0]
    await update.message.reply_text(f"↩️ Cancelled. Send {cmd} again to start it.")
    release_ownership(context)
    return ConversationHandler.END


def build_handlers():
    conv = ConversationHandler(
        entry_points=[CommandHandler("feedback", feedback_start, filters.ChatType.PRIVATE)],
        states={WAITING_FB: [MessageHandler(filters.TEXT & ~filters.COMMAND, feedback_receive)]},
        fallbacks=[
            CommandHandler("cancel", cancel),
            # A new command always escapes a stale conversation.
            MessageHandler(filters.COMMAND, _escape),
        ],
        conversation_timeout=180,
    )
    return [conv, CommandHandler("fbreply", fbreply, filters.ChatType.GROUPS)]
