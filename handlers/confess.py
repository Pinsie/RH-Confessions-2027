"""/confess flow: collect -> moderate -> post (or queue for review)."""
from __future__ import annotations

import html
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    CallbackQueryHandler, CommandHandler, ContextTypes, ConversationHandler,
    MessageHandler, filters,
)

import db as dbx
import media as mediamod
import moderation
import security
import texts as T
from handlers.comments import release_ownership, take_ownership
from config import settings

log = logging.getLogger(__name__)

WAITING_TEXT = 1

SUPPORT_NOTE = T.SUPPORT_NOTE


def confession_keyboard(code: str) -> InlineKeyboardMarkup:
    base = f"https://t.me/{settings.BOT_USERNAME}?start="
    rows = [
        [
            InlineKeyboardButton("📨 Post confession", url=f"{base}confess"),
            InlineKeyboardButton("💬 Comment", url=f"{base}comment_{code}"),
        ]
    ]
    if settings.ANON_CHAT_ENABLED:
        rows.append([InlineKeyboardButton("🕵️ Chat with confessor", url=f"{base}chat_{code}")])
    from handlers.reports import report_button
    rows.append([report_button(code)])
    return InlineKeyboardMarkup(rows)


def render_confession(code: str, category: str, title: str, body: str) -> str:
    cat = html.escape(category or "Other")
    ttl = html.escape(title or "ANONYMOUS CONFESSION")
    return T.CONFESSION_TEMPLATE.format(cat=cat, title=ttl, code=code, body=html.escape(body))


async def confess_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    uhash = security.user_hash(update.effective_user.id)
    db = context.bot_data["db"]
    await dbx.ensure_user(db, uhash, security.alias_for(uhash))

    if await dbx.is_banned(db, uhash):
        await update.message.reply_text("🚫 You are banned from posting.")
        release_ownership(context)
        return ConversationHandler.END

    wait = security.cooldown_remaining(uhash, "confess", settings.CONFESS_COOLDOWN_SECONDS)
    if wait:
        await update.message.reply_text(f"⏳ Slow down — try again in {wait}s.")
        release_ownership(context)
        return ConversationHandler.END

    # Take ownership: stop the free-text router (comments / anon-chat relay)
    # from also consuming the user's next message.
    take_ownership(context)
    await update.message.reply_text(T.CONFESS_INTRO, parse_mode=ParseMode.HTML)
    return WAITING_TEXT


async def confess_receive(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    media = await mediamod.extract(update.message, context.bot)
    if media:
        text = (update.message.caption or "").strip()
    else:
        text = (update.message.text or "").strip()
        if not text:
            await update.message.reply_text("Please send text, a photo, or a GIF — or /cancel.")
            return WAITING_TEXT
    if len(text) > settings.MAX_CONFESSION_LEN:
        await update.message.reply_text(
            f"Too long ({len(text)} chars, max {settings.MAX_CONFESSION_LEN}). Trim it down."
        )
        return WAITING_TEXT

    db = context.bot_data["db"]
    uhash = security.user_hash(update.effective_user.id)

    result = await moderation.moderate(
        text, context.bot_data["db"], kind="confession",
        image_b64=media.b64 if media else None,
        image_mime=media.mime if media else "image/jpeg",
    )
    if media and not media.b64:
        # Couldn't fetch/encode the image, so nobody has seen it -> human eyes.
        result.verdict = "REVIEW"
        result.reason = "Attachment could not be scanned"
    code = dbx.gen_code()

    if result.verdict == "REJECT":
        await dbx.create_confession(db, code, uhash, result.category, result.title, text, "rejected")
        strikes = await dbx.add_strike(db, uhash)
        msg = (
            f"❌ Your confession was not posted.\nReason: {result.reason}\n"
            f"Strike {strikes}/{settings.MAX_STRIKES_BEFORE_BAN}."
        )
        if result.support_flag:
            msg += SUPPORT_NOTE
        release_ownership(context)
        await update.message.reply_text(msg)
        release_ownership(context)
        return ConversationHandler.END

    cid = await dbx.create_confession(
        db, code, uhash, result.category, result.title, text,
        "review" if result.verdict == "REVIEW" else "pending",
        photo_id=media.file_id if media else None,
        media_kind=media.kind if media else None,
    )

    # Save encrypted author mapping for anon chat
    if settings.ANON_CHAT_ENABLED:
        await dbx.save_relay(db, code, security.encrypt_chat_id(update.effective_chat.id))

    if result.verdict == "REVIEW":
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Approve", callback_data=f"mod_approve_{cid}"),
            InlineKeyboardButton("❌ Reject", callback_data=f"mod_reject_{cid}"),
        ]])
        await context.bot.send_message(
            settings.ADMIN_GROUP_ID,
            f"🔎 <b>Review needed</b> — #{code}\nAI reason: {html.escape(result.reason)}\n"
            f"---\n{html.escape(text)}\n---",
            parse_mode=ParseMode.HTML, reply_markup=kb,
        )
        msg = "🕐 Your confession is queued for a quick human review. You'll be notified."
        if result.support_flag:
            msg += SUPPORT_NOTE
        release_ownership(context)
        await update.message.reply_text(msg)
        release_ownership(context)
        return ConversationHandler.END

    await _post_to_channel(context, cid)
    msg = "✅ Your confession has been posted anonymously! Check the confession channel."
    if result.support_flag:
        msg += SUPPORT_NOTE
    release_ownership(context)
    await update.message.reply_text(msg)
    release_ownership(context)
    return ConversationHandler.END


async def _post_to_channel(context: ContextTypes.DEFAULT_TYPE, cid: int):
    db = context.bot_data["db"]
    conf = await dbx.get_confession(db, cid=cid)
    caption = render_confession(conf["code"], conf["category"], conf["title"], conf["body"])
    kb = confession_keyboard(conf["code"])
    fid, kind = conf.get("photo_file_id"), conf.get("media_kind")
    if fid and kind == "photo":
        sent = await context.bot.send_photo(
            settings.CHANNEL_ID, fid, caption=caption[:1024],
            parse_mode=ParseMode.HTML, reply_markup=kb)
    elif fid and kind == "animation":
        sent = await context.bot.send_animation(
            settings.CHANNEL_ID, fid, caption=caption[:1024],
            parse_mode=ParseMode.HTML, reply_markup=kb)
    elif fid and kind == "sticker":
        await context.bot.send_sticker(settings.CHANNEL_ID, fid)
        sent = await context.bot.send_message(
            settings.CHANNEL_ID, caption, parse_mode=ParseMode.HTML, reply_markup=kb)
    else:
        sent = await context.bot.send_message(
            settings.CHANNEL_ID, caption, parse_mode=ParseMode.HTML, reply_markup=kb)
    await dbx.mark_posted(db, cid, sent.message_id)


async def mod_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Approve/Reject buttons in the admin group."""
    q = update.callback_query
    await q.answer()
    action, cid = q.data.rsplit("_", 1)
    cid = int(cid)
    db = context.bot_data["db"]
    conf = await dbx.get_confession(db, cid=cid)
    if not conf or conf["status"] not in ("review", "pending"):
        await q.edit_message_text(q.message.text + "\n\n(already handled)")
        return

    if action == "mod_approve":
        await dbx.log_mod(db, "review_outcome", "approved", conf["code"], "")
        await _post_to_channel(context, cid)
        await q.edit_message_text(q.message.text + f"\n\n✅ Approved by {q.from_user.first_name}")
        await _notify_author(context, conf["code"], "✅ Your confession passed review and is now posted!")
    else:
        await dbx.log_mod(db, "review_outcome", "rejected", conf["code"], "")
        await dbx.set_status(db, cid, "rejected")
        await dbx.add_strike(db, conf["author_hash"])
        await q.edit_message_text(q.message.text + f"\n\n❌ Rejected by {q.from_user.first_name}")
        await _notify_author(context, conf["code"], "❌ Your confession was rejected after review.")


async def _notify_author(context, code: str, text: str):
    db = context.bot_data["db"]
    enc = await dbx.get_relay(db, code)
    if not enc:
        return
    chat_id = security.decrypt_chat_id(enc)
    if chat_id:
        try:
            await context.bot.send_message(chat_id, text)
        except Exception:  # noqa: BLE001 — user may have blocked the bot
            log.info("Could not notify author of %s", code)


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
        entry_points=[CommandHandler("confess", confess_start, filters.ChatType.PRIVATE)],
        states={WAITING_TEXT: [MessageHandler(
            (filters.TEXT | filters.PHOTO | filters.ANIMATION | filters.Sticker.ALL)
            & ~filters.COMMAND, confess_receive)]},
        fallbacks=[
            CommandHandler("cancel", cancel),
            # A new command always escapes a stale conversation.
            MessageHandler(filters.COMMAND, _escape),
        ],
        conversation_timeout=180,
    )
    return [conv, CallbackQueryHandler(mod_callback, pattern=r"^mod_(approve|reject)_\d+$")]
