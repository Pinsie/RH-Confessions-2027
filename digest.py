"""☀️ The 8:00 AM daily digest.

Sources (all fed by Press/JCRC commands the day before):
  • /addresult  -> "Yesterday on the court/field" section
  • /addevent   -> "Happening today" (events whose event_date == today)
  • /publish    -> "From RH Press" (latest articles in the past 26h)
  • top confession of yesterday (most comments) as a fun closer

If ANTHROPIC_API_KEY is set, Claude rewrites the assembled bullets into a
lively post (facts locked — it may not invent scores or events).
If there's literally nothing to report, no digest is sent (no spam).
"""
from __future__ import annotations

import datetime as dt
import logging
from zoneinfo import ZoneInfo

from telegram.constants import ParseMode
from telegram.ext import ContextTypes

import db as dbx
import moderation
from config import settings

log = logging.getLogger(__name__)


async def send_digest(context: ContextTypes.DEFAULT_TYPE):
    db = context.bot_data["db"]
    tz = ZoneInfo(settings.TIMEZONE)
    today = dt.datetime.now(tz).strftime("%Y-%m-%d")
    nice_date = dt.datetime.now(tz).strftime("%A, %d %B")

    # Housekeeping first, so it runs even on empty-digest days.
    await dbx.purge_expired_relays(db)
    await dbx.purge_old_author_hashes(db)
    await dbx.purge_old_feedback(db, settings.ANON_CHAT_TTL_DAYS)

    sports, events, articles = await dbx.news_for_digest(db, today)
    top = await dbx.top_confession_yesterday(db)

    if not (sports or events or articles or top):
        log.info("Nothing to digest today; skipping.")
        return

    lines = [f"☀️ RH MORNING DIGEST — {nice_date}", ""]
    if sports:
        lines.append("🏆 YESTERDAY'S GAMES:")
        for s in sports:
            lines.append(f"- {s['title']}" + (f": {s['body']}" if s["body"] else ""))
        lines.append("")
    if events:
        lines.append("📅 HAPPENING TODAY:")
        for e in events:
            lines.append(f"- {e['title']}" + (f": {e['body']}" if e["body"] else ""))
        lines.append("")
    if articles:
        lines.append("🗞 FROM RH PRESS / JCRC:")
        for a in articles:
            lines.append(f"- {a['title']}")
        lines.append("")
    if top and top["n"] > 0:
        snippet = top["body"][:120] + ("…" if len(top["body"]) > 120 else "")
        lines.append(f"🔥 HOTTEST CONFESSION YTD (#{top['code']}, {top['n']} comments): {snippet}")

    raw = "\n".join(lines)
    text = await moderation.polish_digest(raw)

    try:
        await context.bot.send_message(settings.CHANNEL_ID, text, parse_mode=None)
    except Exception:  # noqa: BLE001
        log.exception("Digest send failed")

