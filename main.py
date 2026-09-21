"""RH Confessions bot — entrypoint.

Run:  python main.py
Requires a filled-in .env (see .env.example) and one-time Telegram setup
described in README.md.
"""
from __future__ import annotations

import datetime as dt
import logging
import sys
from zoneinfo import ZoneInfo

from telegram import BotCommand
from telegram.ext import Application, ApplicationBuilder

import db as dbx
from config import settings, validate
from digest import send_digest
from handlers import (admin, anonchat, comments, confess, feedback, ingest, polls,
                      press, reports)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
# Privacy: don't let httpx log request URLs (they contain the bot token).
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("rh")


async def post_init(app: Application):
    app.bot_data["db"] = await dbx.connect()
    await app.bot.set_my_commands([
        BotCommand("confess", "Share an anonymous confession (text or photo)"),
        BotCommand("poll", "Send an anonymous poll to the channel"),
        BotCommand("feedback", "Message the JCRC anonymously"),
        BotCommand("rules", "Ground rules"),
        BotCommand("privacy", "What we store about you"),
        BotCommand("cancel", "Cancel current action"),
        BotCommand("publish", "Press/JCRC: publish an article"),
        BotCommand("announce", "JCRC: quick announcement"),
        BotCommand("addevent", "Press/JCRC: add event to 8am digest"),
        BotCommand("addresult", "Press/JCRC: log a game result"),
    ])
    log.info("Bot ready.")


async def heartbeat(context):
    """Daily proof-of-life to the admin group, 10 min after the digest.

    If this message ever FAILS to arrive, the bot is down — that absence is
    your alert. (A fully-dead process cannot self-report; see README for an
    optional external uptime check.)"""
    db = context.bot_data["db"]
    s = await dbx.stats(db)
    try:
        await context.bot.send_message(
            settings.ADMIN_GROUP_ID,
            f"💓 Bot alive. Totals — confessions: {s['confessions_posted']}, "
            f"comments: {s['comments']}, users: {s['users']}, banned: {s['banned']}.",
        )
    except Exception:  # noqa: BLE001
        logging.getLogger("rh").exception("Heartbeat send failed")


async def post_shutdown(app: Application):
    db = app.bot_data.get("db")
    if db:
        await db.close()


def main():
    problems = validate()
    if problems:
        print("Config errors:\n - " + "\n - ".join(problems))
        sys.exit(1)

    app = (
        ApplicationBuilder()
        .token(settings.BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    # ConversationHandlers + commands in group 0 (default) win over the
    # free-text router registered in group 10.
    for h in confess.build_handlers():
        app.add_handler(h)
    for h in press.build_handlers():
        app.add_handler(h)
    for h in admin.build_handlers():
        app.add_handler(h)
    for h in anonchat.build_handlers():
        app.add_handler(h)
    for h in ingest.build_handlers():
        app.add_handler(h)
    for h in polls.build_handlers():
        app.add_handler(h)
    for h in feedback.build_handlers():
        app.add_handler(h)
    for h in reports.build_handlers():
        app.add_handler(h)

    comment_handlers = comments.build_handlers()
    for h in comment_handlers[:-1]:
        app.add_handler(h)
    app.add_handler(comment_handlers[-1], group=10)  # free-text router last

    if settings.HEARTBEAT_ENABLED:
        app.job_queue.run_daily(
            heartbeat,
            time=dt.time(
                hour=settings.DIGEST_HOUR,
                minute=settings.DIGEST_MINUTE + 10,
                tzinfo=ZoneInfo(settings.TIMEZONE),
            ),
            name="heartbeat",
        )

    app.job_queue.run_daily(
        send_digest,
        time=dt.time(
            hour=settings.DIGEST_HOUR,
            minute=settings.DIGEST_MINUTE,
            tzinfo=ZoneInfo(settings.TIMEZONE),
        ),
        name="daily_digest",
    )

    log.info("Starting polling…")
    app.run_polling(allowed_updates=["message", "callback_query", "channel_post"])


if __name__ == "__main__":
    main()
