"""🚩 Report button on channel posts.

Any reader can tap Report under a confession or poll. Reports are deduped (one
per person per post — enforced by a UNIQUE constraint on the reporter's hash),
so a single angry user can't run up the count.

Every report pings the admin group. At REPORT_PING_THRESHOLD the ping gets
louder. At REPORT_HIDE_THRESHOLD the post is deleted from the channel
automatically and the admin group is told.

Honest note on thresholds: at hall scale, hitting 20 distinct reporters on one
post is extremely unlikely. The per-report ping to admins is the real safety
mechanism; auto-hide is a backstop.
"""
from __future__ import annotations

import html
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import CallbackQueryHandler, ContextTypes

import db as dbx
import security
from config import settings

log = logging.getLogger(__name__)


def report_button(target_ref: str) -> InlineKeyboardButton:
    return InlineKeyboardButton("🚩 Report", callback_data=f"report_{target_ref}")


async def report_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    target_ref = q.data.removeprefix("report_")
    db = context.bot_data["db"]
    rhash = security.user_hash(q.from_user.id)

    msg_id = q.message.message_id if q.message else None
    total = await dbx.add_report(db, "post", target_ref, msg_id, rhash)

    if total is None:
        await q.answer("You've already reported this post.", show_alert=True)
        return
    await q.answer("🚩 Reported. Mods will take a look.", show_alert=True)

    conf = await dbx.get_confession(db, code=target_ref)
    preview = html.escape((conf["body"] if conf else "")[:200]) if conf else "(poll or unknown post)"

    loud = "⚠️⚠️ " if total >= settings.REPORT_PING_THRESHOLD else ""
    try:
        await context.bot.send_message(
            settings.ADMIN_GROUP_ID,
            f"{loud}🚩 <b>Report #{total}</b> on #{html.escape(target_ref)}\n"
            f"---\n{preview}\n---",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🗑 Take down", callback_data=f"rtakedown_{target_ref}"),
                InlineKeyboardButton("✅ Keep", callback_data=f"rkeep_{target_ref}"),
            ]]),
        )
    except Exception:  # noqa: BLE001
        log.exception("Report notify failed")

    if total >= settings.REPORT_HIDE_THRESHOLD:
        await _auto_hide(context, target_ref, total)


async def _auto_hide(context, target_ref: str, total: int):
    db = context.bot_data["db"]
    conf = await dbx.get_confession(db, code=target_ref)
    if not conf or not conf.get("channel_msg_id"):
        return
    if conf["status"] == "rejected":
        return  # already gone
    try:
        await context.bot.delete_message(settings.CHANNEL_ID, conf["channel_msg_id"])
    except Exception:  # noqa: BLE001 — >48h old messages can't be bot-deleted
        log.info("Auto-hide could not delete %s", target_ref)
    await dbx.set_status(db, conf["id"], "rejected")
    await context.bot.send_message(
        settings.ADMIN_GROUP_ID,
        f"🗑 <b>#{html.escape(target_ref)} auto-removed</b> — hit {total} reports "
        f"(threshold {settings.REPORT_HIDE_THRESHOLD}).",
        parse_mode=ParseMode.HTML,
    )


async def admin_report_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Take down / keep buttons on the admin-group report notice."""
    q = update.callback_query
    await q.answer()
    action, ref = q.data.split("_", 1)
    db = context.bot_data["db"]
    conf = await dbx.get_confession(db, code=ref)

    if action == "rkeep":
        await q.edit_message_text(
            (q.message.text or "") + f"\n\n✅ Kept by {q.from_user.first_name}"
        )
        return

    if conf and conf.get("channel_msg_id"):
        try:
            await context.bot.delete_message(settings.CHANNEL_ID, conf["channel_msg_id"])
        except Exception:  # noqa: BLE001
            pass
        await dbx.set_status(db, conf["id"], "rejected")
    await q.edit_message_text(
        (q.message.text or "") + f"\n\n🗑 Taken down by {q.from_user.first_name}"
    )


def build_handlers():
    return [
        CallbackQueryHandler(report_callback, pattern=r"^report_"),
        CallbackQueryHandler(admin_report_action, pattern=r"^(rtakedown|rkeep)_"),
    ]
