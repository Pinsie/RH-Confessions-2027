"""Publishing tools for RH Press & JCRC (role-gated, NOT anonymous).

/publish            -> long-form article, posted to the channel immediately
/announce           -> short JCRC announcement, posted immediately
/addevent  DATE | Title | details      -> feeds tomorrow's 8am digest
/addresult Title | details             -> yesterday's game result, feeds digest
/mynews             -> list recent items

Everything these commands post ALSO lands in news_items so the 8am digest can
pick it up ("trending press article" = most recent articles).
"""
from __future__ import annotations

import html
import logging
import re

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    CommandHandler, ContextTypes, ConversationHandler, MessageHandler, filters,
)

import db as dbx
from config import settings

log = logging.getLogger(__name__)

TITLE, BODY = range(2)

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


async def _require_role(update: Update, context: ContextTypes.DEFAULT_TYPE, *roles) -> bool:
    db = context.bot_data["db"]
    if await dbx.has_role(db, update.effective_user.id, *roles):
        return True
    await update.message.reply_text("🚫 This command is for RH Press / JCRC. Ask an admin for access.")
    return False


# ------------------------------------------------------------- /publish

async def publish_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await _require_role(update, context, "press", "jcrc", "admin"):
        return ConversationHandler.END
    await update.message.reply_text("📰 Article title?")
    return TITLE


async def publish_title(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["pub_title"] = update.message.text.strip()[:120]
    await update.message.reply_text("Now send the article body (one message). /cancel to abort.")
    return BODY


async def publish_body(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    db = context.bot_data["db"]
    title = context.user_data.pop("pub_title", "Untitled")
    body = update.message.text.strip()
    is_jcrc = await dbx.has_role(db, update.effective_user.id, "jcrc")
    byline = "JCRC" if is_jcrc and not await dbx.has_role(db, update.effective_user.id, "press") else "RH Press"

    text = (
        f"📰 <b>{html.escape(byline)}</b>\n\n"
        f"<b><u>{html.escape(title)}</u></b>\n\n"
        f"{html.escape(body)}\n\n"
        f"💬 Discuss below ⬇️"
    )
    sent = await context.bot.send_message(settings.CHANNEL_ID, text, parse_mode=ParseMode.HTML)
    nid = await dbx.add_news(db, "article", title, body, byline)
    await db.execute("UPDATE news_items SET channel_msg_id=? WHERE id=?", (sent.message_id, nid))
    await db.commit()
    await update.message.reply_text(f"✅ Published as {byline}.")
    return ConversationHandler.END


# ------------------------------------------------------------ /announce

async def announce(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _require_role(update, context, "jcrc", "admin"):
        return
    body = " ".join(context.args or []).strip()
    if not body:
        await update.message.reply_text("Usage: /announce <message>")
        return
    await context.bot.send_message(
        settings.CHANNEL_ID,
        f"📣 <b>JCRC Announcement</b>\n\n{html.escape(body)}",
        parse_mode=ParseMode.HTML,
    )
    await dbx.add_news(context.bot_data["db"], "announce", "JCRC Announcement", body, "JCRC")
    await update.message.reply_text("✅ Announced.")


# ------------------------------------------------------------ /addevent

async def addevent(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/addevent 2026-07-04 | Block D Supper Night | 9pm at the lounge, free food"""
    if not await _require_role(update, context, "admin"):  # events are yours to control
        return
    raw = " ".join(context.args or [])
    parts = [p.strip() for p in raw.split("|")]
    if len(parts) < 2 or not _DATE_RE.match(parts[0]):
        await update.message.reply_text(
            "Usage: /addevent YYYY-MM-DD | Event title | optional details\n"
            "It'll appear in the 8am digest on that date."
        )
        return
    date, title = parts[0], parts[1]
    details = parts[2] if len(parts) > 2 else ""
    await dbx.add_news(context.bot_data["db"], "event", title, details, "JCRC", event_date=date)
    await update.message.reply_text(f"✅ Event saved for {date}: {title}")


# ----------------------------------------------------------- /addresult

async def addresult(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/addresult RH vs Eusoff — IHG Basketball | RH won 54–47, MVP performance from the bench"""
    if not await _require_role(update, context, "admin"):  # results are yours to control
        return
    raw = " ".join(context.args or [])
    parts = [p.strip() for p in raw.split("|")]
    if not parts or not parts[0]:
        await update.message.reply_text(
            "Usage: /addresult Match title | result & highlights\n"
            "It'll headline tomorrow's 8am digest."
        )
        return
    title = parts[0]
    details = parts[1] if len(parts) > 1 else ""
    await dbx.add_news(context.bot_data["db"], "sport", title, details, "RH Sports")
    await update.message.reply_text(f"✅ Result logged: {title}")


# --------------------------------------------------------------- /mynews

async def mynews(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _require_role(update, context, "admin"):
        return
    db = context.bot_data["db"]
    cur = await db.execute("SELECT kind, title, event_date FROM news_items ORDER BY id DESC LIMIT 10")
    rows = await cur.fetchall()
    if not rows:
        await update.message.reply_text("No news items yet.")
        return
    lines = [f"• [{r['kind']}] {r['title']}" + (f" ({r['event_date']})" if r["event_date"] else "")
             for r in rows]
    await update.message.reply_text("🗞 Last 10 items:\n" + "\n".join(lines))


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Cancelled. 👌")
    return ConversationHandler.END


def build_handlers():
    conv = ConversationHandler(
        entry_points=[CommandHandler("publish", publish_start, filters.ChatType.PRIVATE)],
        states={
            TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, publish_title)],
            BODY: [MessageHandler(filters.TEXT & ~filters.COMMAND, publish_body)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        conversation_timeout=900,
    )
    return [
        conv,
        CommandHandler("announce", announce, filters.ChatType.PRIVATE),
        CommandHandler("addevent", addevent, filters.ChatType.PRIVATE),
        CommandHandler("addresult", addresult, filters.ChatType.PRIVATE),
        CommandHandler("mynews", mynews, filters.ChatType.PRIVATE),
    ]
