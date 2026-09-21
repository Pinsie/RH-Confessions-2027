"""'Chat with confessor' — double-blind message relay.

Initiator taps the button -> we open a session. Their PMs to the bot are
relayed to the confessor tagged with the initiator's alias; the confessor
replies with /reply <session_id> <text>. Neither side ever sees the other's
identity; chat IDs live only Fernet-encrypted in the DB and are purged on TTL.

Every relayed message passes AI moderation, so the anon-chat channel can't be
used to deliver the harassment the public channel blocks.
"""
from __future__ import annotations

import html
import logging

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import CommandHandler, ContextTypes, filters

import db as dbx
import moderation
import security
from config import settings

log = logging.getLogger(__name__)


async def begin_chat(update: Update, context: ContextTypes.DEFAULT_TYPE, code: str):
    if not settings.ANON_CHAT_ENABLED:
        await update.message.reply_text("Anonymous chat is disabled.")
        return
    db = context.bot_data["db"]
    uhash = security.user_hash(update.effective_user.id)
    if await dbx.is_banned(db, uhash):
        await update.message.reply_text("🚫 You are banned.")
        return

    enc_author = await dbx.get_relay(db, code)
    if not enc_author:
        await update.message.reply_text(
            "Can't open a chat for this confession (expired or author unreachable)."
        )
        return

    enc_me = security.encrypt_chat_id(update.effective_chat.id)
    sid = await dbx.create_session(db, code, uhash, enc_me, enc_author)
    from handlers.comments import AWAITING_KEY
    context.user_data[AWAITING_KEY] = ("chat", str(sid))
    await update.message.reply_text(
        f"🕵️ Anonymous chat opened about #{code}. Send your message — the confessor "
        f"will see it from an anonymous sender. /cancel to stop.",
        parse_mode=ParseMode.HTML,
    )


async def relay_message(update: Update, context: ContextTypes.DEFAULT_TYPE, sid_str: str):
    db = context.bot_data["db"]
    sess = await dbx.get_session(db, int(sid_str))
    if not sess:
        await update.message.reply_text("This chat session has ended.")
        from handlers.comments import AWAITING_KEY
        context.user_data.pop(AWAITING_KEY, None)
        return

    text = (update.message.text or "").strip()
    result = await moderation.moderate(text, context.bot_data["db"], kind="chat")
    if result.verdict != "ALLOW":
        await update.message.reply_text(f"❌ Not relayed: {result.reason}")
        return

    dest = security.decrypt_chat_id(sess["enc_b_chat"])
    if not dest:
        await update.message.reply_text("Couldn't reach the confessor.")
        return
    try:
        await context.bot.send_message(
            dest,
            f"🕵️ <b>Someone</b> (chat #{sess['id']} · re your confession "
            f"#{sess['confession_code']}):\n\n"
            f"{html.escape(text)}\n\n"
            f"Reply with: <code>/reply {sess['id']} your message</code> · "
            f"<code>/endchat {sess['id']}</code> to stop",
            parse_mode=ParseMode.HTML,
        )
        await update.message.reply_text("📨 Sent anonymously. Keep typing to continue, /cancel to stop.")
    except Exception:  # noqa: BLE001
        log.info("Relay failed for session %s", sess["id"])
        await update.message.reply_text("Couldn't deliver (they may have blocked the bot).")


async def reply_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Confessor side: /reply <session_id> <text>."""
    args = context.args or []
    if len(args) < 2:
        await update.message.reply_text("Usage: /reply <session_id> <message>")
        return
    db = context.bot_data["db"]
    sess = await dbx.get_session(db, int(args[0])) if args[0].isdigit() else None
    if not sess:
        await update.message.reply_text("Session not found or ended.")
        return

    text = " ".join(args[1:])
    result = await moderation.moderate(text, context.bot_data["db"], kind="chat")
    if result.verdict != "ALLOW":
        await update.message.reply_text(f"❌ Not relayed: {result.reason}")
        return

    dest = security.decrypt_chat_id(sess["enc_a_chat"])
    if not dest:
        await update.message.reply_text("Couldn't reach them.")
        return
    try:
        await context.bot.send_message(
            dest,
            f"🕵️ <b>Confessor of #{sess['confession_code']}</b>:\n\n{html.escape(text)}",
            parse_mode=ParseMode.HTML,
        )
        await update.message.reply_text("📨 Sent.")
    except Exception:  # noqa: BLE001
        await update.message.reply_text("Couldn't deliver.")


async def endchat_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args or []
    if not args or not args[0].isdigit():
        await update.message.reply_text("Usage: /endchat <session_id>")
        return
    await dbx.end_session(context.bot_data["db"], int(args[0]))
    await update.message.reply_text("Chat ended. 👋")


def build_handlers():
    return [
        CommandHandler("reply", reply_cmd, filters.ChatType.PRIVATE),
        CommandHandler("endchat", endchat_cmd, filters.ChatType.PRIVATE),
    ]
