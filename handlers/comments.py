"""Anonymous comments + /start deep-link dispatch + channel->group thread mapping.

How commenting works (ConfessIT-style "Commenter Bot"):
1. Channel post carries a "💬 Comment" button -> deep link t.me/bot?start=comment_RHXXXXX
2. User lands in PM; bot asks for the comment text.
3. Comment is AI-moderated, then posted by the bot into the linked discussion
   group as a reply to the auto-forwarded copy of the confession, signed with
   the user's stable pseudonym (e.g. "quasarflick37").

The channel->group message mapping comes from Telegram's automatic forward:
when a channel posts, Telegram mirrors it into the linked group; we catch that
mirror message and store its id.
"""
from __future__ import annotations

import html
import logging

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import CommandHandler, ContextTypes, MessageHandler, filters

import db as dbx
import media as mediamod
import moderation
import security
import texts as T
from config import settings

log = logging.getLogger(__name__)

AWAITING_KEY = "awaiting"
BUSY_KEY = "in_conversation"      # set while a ConversationHandler owns this user
BUSY_AT_KEY = "in_conversation_at"  # when it was set, so a stale flag self-heals


def take_ownership(context):
    """Called by /confess, /poll, /feedback so the free-text router stays out."""
    import time
    context.user_data[BUSY_KEY] = True
    context.user_data[BUSY_AT_KEY] = time.monotonic()
    context.user_data.pop(AWAITING_KEY, None)


def release_ownership(context):
    context.user_data.pop(BUSY_KEY, None)
    context.user_data.pop(BUSY_AT_KEY, None)


def _busy(context) -> bool:
    """True if a conversation owns this user. Self-heals if the flag is older
    than the conversation timeout (180s) — a crashed flow can't wedge the bot."""
    import time
    if not context.user_data.get(BUSY_KEY):
        return False
    since = context.user_data.get(BUSY_AT_KEY, 0)
    if time.monotonic() - since > 200:
        release_ownership(context)
        return False
    return True  # context.user_data[AWAITING_KEY] = ("comment", code) | ("chat", code)


WELCOME = T.WELCOME


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/start — plain welcome, or deep-link dispatch (comment_XXX / chat_XXX / confess)."""
    uhash = security.user_hash(update.effective_user.id)
    alias = security.alias_for(uhash)
    db = context.bot_data["db"]
    await dbx.ensure_user(db, uhash, alias)

    args = context.args or []
    if args:
        payload = args[0]
        if payload == "confess":
            # Re-dispatch into the confession conversation entry
            await update.message.reply_text("Use /confess to begin! 📨")
            return
        if payload.startswith("comment_"):
            code = payload.removeprefix("comment_")
            conf = await dbx.get_confession(db, code=code)
            if not conf or conf["status"] != "posted":
                await update.message.reply_text("That confession doesn't exist (or isn't public).")
                return
            context.user_data[AWAITING_KEY] = ("comment", code)
            await update.message.reply_text(
                f"💬 Send your anonymous comment for #{code}. "
                f"It'll be posted as an anonymous <b>Commenter</b>. /cancel to abort.",
                parse_mode=ParseMode.HTML,
            )
            return
        if payload.startswith("chat_"):
            from handlers import anonchat
            await anonchat.begin_chat(update, context, payload.removeprefix("chat_"))
            return

    await update.message.reply_text(WELCOME.format(alias=alias), parse_mode=ParseMode.HTML)


async def private_text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Catches free text in PM when we're expecting a comment or relaying a chat.

    Only one flow may own a message. If a ConversationHandler (confess/poll/
    feedback) is mid-flow for this user, it has already handled the text and we
    must stay out of the way — otherwise a single message gets processed twice.
    """
    if _busy(context):
        return
    state = context.user_data.get(AWAITING_KEY)
    if not state:
        return  # not for us; other handlers (conversations) have their own states


    kind, code = state
    if kind == "comment":
        context.user_data.pop(AWAITING_KEY, None)
        await _handle_comment(update, context, code)
    elif kind == "chat":
        from handlers import anonchat
        await anonchat.relay_message(update, context, code)


async def _handle_comment(update: Update, context: ContextTypes.DEFAULT_TYPE, code: str):
    db = context.bot_data["db"]
    uhash = security.user_hash(update.effective_user.id)
    alias = security.alias_for(uhash)

    if await dbx.is_banned(db, uhash):
        await update.message.reply_text("🚫 You are banned from commenting.")
        return
    wait = security.cooldown_remaining(uhash, "comment", settings.COMMENT_COOLDOWN_SECONDS)
    if wait:
        await update.message.reply_text(f"⏳ Slow down — try again in {wait}s.")
        return

    media = await mediamod.extract(update.message, context.bot)
    text = ((update.message.caption if media else update.message.text) or "").strip()
    if not media and not text:
        await update.message.reply_text("Send text, a photo, or a GIF — or /cancel.")
        return
    conf = await dbx.get_confession(db, code=code)
    if not conf:
        await update.message.reply_text("Confession not found.")
        return

    result = await moderation.moderate(
        text, context.bot_data["db"], kind="comment",
        image_b64=media.b64 if media else None,
        image_mime=media.mime if media else "image/jpeg",
    )
    if media and not media.b64:
        result.verdict = "REVIEW"
        result.reason = "Attachment could not be scanned"
    if result.verdict == "REJECT":
        # Comments don't carry strikes — a bad one-liner isn't a bannable offence.
        # Repeat abusers are handled by admins via /banauthor on their confessions.
        await update.message.reply_text(f"❌ Comment blocked: {result.reason}")
        await context.bot.send_message(
            settings.ADMIN_GROUP_ID,
            f"🚫 Comment auto-blocked on #{code}:\n---\n{html.escape(text)}\n---\n"
            f"Reason: {html.escape(result.reason)}",
            parse_mode=ParseMode.HTML,
        )
        return
    if result.verdict == "REVIEW":
        # Comments are lower-stakes: REVIEW comments are simply not posted,
        # but the author is told why instead of silently dropping them.
        await update.message.reply_text(
            f"🕐 That comment needs human review and wasn't posted: {result.reason}"
        )
        await context.bot.send_message(
            settings.ADMIN_GROUP_ID,
            f"🔎 Comment held (not posted) on #{code}:\n---\n{html.escape(text)}\n---\n"
            f"Reason: {html.escape(result.reason)}",
            parse_mode=ParseMode.HTML,
        )
        return

    await dbx.add_comment(db, conf["id"], uhash, alias, text)

    reply_to = conf.get("group_msg_id")
    body = "💬 <b>Commenter</b>:" + (f"\n\n{html.escape(text)}" if text else "")
    try:
        if media and media.kind == "photo":
            await context.bot.send_photo(
                settings.DISCUSSION_GROUP_ID, media.file_id, caption=body[:1024],
                parse_mode=ParseMode.HTML, reply_to_message_id=reply_to)
        elif media and media.kind == "animation":
            await context.bot.send_animation(
                settings.DISCUSSION_GROUP_ID, media.file_id, caption=body[:1024],
                parse_mode=ParseMode.HTML, reply_to_message_id=reply_to)
        elif media and media.kind == "sticker":
            await context.bot.send_message(
                settings.DISCUSSION_GROUP_ID, body, parse_mode=ParseMode.HTML,
                reply_to_message_id=reply_to)
            await context.bot.send_sticker(
                settings.DISCUSSION_GROUP_ID, media.file_id, reply_to_message_id=reply_to)
        else:
            await context.bot.send_message(
                settings.DISCUSSION_GROUP_ID, body, parse_mode=ParseMode.HTML,
                reply_to_message_id=reply_to)
        await update.message.reply_text("✅ Comment posted anonymously!")
    except Exception:  # noqa: BLE001
        log.exception("Failed to post comment")
        await update.message.reply_text("⚠️ Couldn't post the comment — tell an admin.")


async def track_auto_forward(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Store the discussion-group mirror id of each channel post."""
    msg = update.effective_message
    if not msg or not msg.is_automatic_forward:
        return
    origin = getattr(msg, "forward_origin", None)
    chan = getattr(origin, "chat", None)
    if chan and chan.id == settings.CHANNEL_ID:
        await dbx.map_group_msg(context.bot_data["db"], origin.message_id, msg.message_id)


async def rules(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        T.RULES.format(strikes=settings.MAX_STRIKES_BEFORE_BAN), parse_mode=ParseMode.HTML
    )


async def privacy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        T.PRIVACY.format(
            hash_ttl=settings.AUTHOR_HASH_TTL_DAYS,
            chat_ttl=settings.ANON_CHAT_TTL_DAYS,
        ),
        parse_mode=ParseMode.HTML,
    )


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop(AWAITING_KEY, None)
    await update.message.reply_text("Cancelled. 👌")


def build_handlers():
    return [
        CommandHandler("start", start, filters.ChatType.PRIVATE),
        CommandHandler("rules", rules, filters.ChatType.PRIVATE),
        CommandHandler("privacy", privacy, filters.ChatType.PRIVATE),
        CommandHandler("cancel", cancel, filters.ChatType.PRIVATE),
        MessageHandler(
            filters.Chat(settings.DISCUSSION_GROUP_ID) & filters.IS_AUTOMATIC_FORWARD,
            track_auto_forward,
        ),
        # Low priority (group=10) so ConversationHandlers win when active.
        MessageHandler(
            filters.ChatType.PRIVATE
            & (filters.TEXT | filters.PHOTO | filters.ANIMATION | filters.Sticker.ALL)
            & ~filters.COMMAND,
            private_text_router),
    ]
