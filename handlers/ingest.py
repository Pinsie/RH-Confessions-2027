"""Reads the block announcement channel and auto-extracts today/future events.

Flow: bot is an admin/member of the block channel -> every channel_post there
arrives here -> Claude parses it (moderation.parse_event) -> if it's a real
event with a resolvable date, it's stored in news_items as kind='event' with
that event_date. The existing 8am digest already pulls kind='event' rows whose
event_date == today, so parsed events flow into the blast with zero extra work.

Non-events (reminders, memes, chatter) are silently dropped. Manual /addevent
still works as a fallback / for anything the parser misses.

Dedup: we skip a post we've already ingested (by the source message id) so a
restart or an edit re-delivery doesn't double-log an event.
"""
from __future__ import annotations

import datetime as dt
import logging
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.ext import ContextTypes, MessageHandler, filters

import db as dbx
import moderation
from config import settings

log = logging.getLogger(__name__)


async def ingest_block_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg:
        return
    text = msg.text or msg.caption or ""
    if not text.strip():
        return  # photo/sticker with no words -> nothing to parse

    db = context.bot_data["db"]
    src_id = msg.message_id

    # Dedup on source message id.
    cur = await db.execute(
        "SELECT 1 FROM ingested_posts WHERE source_msg_id=?", (src_id,)
    )
    if await cur.fetchone():
        return

    tz = ZoneInfo(settings.TIMEZONE)
    posted_date = dt.datetime.now(tz).strftime("%Y-%m-%d")

    parsed = await moderation.parse_event(text, posted_date)

    # Record that we've seen this post regardless of outcome (prevents re-parse).
    await db.execute(
        "INSERT OR IGNORE INTO ingested_posts(source_msg_id, was_event, created_at) VALUES(?,?,?)",
        (src_id, int(bool(parsed.get("is_event"))), int(dt.datetime.now().timestamp())),
    )
    await db.commit()

    if not parsed.get("is_event") or not parsed.get("date"):
        return  # not an event, or no resolvable date -> nothing lands in the dated blast

    title = parsed["title"] or "Hall event"
    time_str = parsed.get("time", "")
    details = parsed.get("details", "")
    body = " · ".join(x for x in (time_str, details) if x)

    await dbx.add_news(
        db, "event", title, body, "Block Channel", event_date=parsed["date"]
    )
    log.info("Ingested event '%s' for %s", title, parsed["date"])


def build_handlers():
    if not settings.BLOCK_CHANNEL_ID:
        return []  # feature off
    return [
        MessageHandler(
            filters.Chat(settings.BLOCK_CHANNEL_ID) & filters.UpdateType.CHANNEL_POST,
            ingest_block_post,
        )
    ]
