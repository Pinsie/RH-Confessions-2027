"""Admin tooling. All commands only work inside the private ADMIN_GROUP.

Key design point: /banauthor bans by confession code — the admin never learns
WHO wrote it. Anonymity survives moderation.

Bootstrap: the FIRST person to run /claimadmin inside the admin group becomes
admin. After that the command is dead. Add others with /addrole.
"""
from __future__ import annotations

import html

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import CommandHandler, ContextTypes, filters

import db as dbx
from config import settings

ROLES = ("admin", "jcrc", "press")


def _admin_chat(update: Update) -> bool:
    return update.effective_chat and update.effective_chat.id == settings.ADMIN_GROUP_ID


async def _require_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if not _admin_chat(update):
        return False
    if await dbx.has_role(context.bot_data["db"], update.effective_user.id, "admin"):
        return True
    await update.message.reply_text("Admins only.")
    return False


async def claimadmin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _admin_chat(update):
        return
    db = context.bot_data["db"]
    cur = await db.execute("SELECT COUNT(*) c FROM roles WHERE role='admin'")
    if (await cur.fetchone())["c"] > 0:
        await update.message.reply_text("Admin already claimed.")
        return
    await dbx.add_role(db, update.effective_user.id, "admin", update.effective_user.id)
    await update.message.reply_text(f"👑 {update.effective_user.first_name} is now admin.")


async def addrole(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/addrole <user_id> <admin|jcrc|press> — get the ID by having them message @userinfobot."""
    if not await _require_admin(update, context):
        return
    args = context.args or []
    if len(args) != 2 or not args[0].isdigit() or args[1] not in ROLES:
        await update.message.reply_text("Usage: /addrole <telegram_user_id> <admin|jcrc|press>")
        return
    await dbx.add_role(context.bot_data["db"], int(args[0]), args[1], update.effective_user.id)
    await update.message.reply_text(f"✅ Gave {args[1]} to {args[0]}.")


async def delrole(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _require_admin(update, context):
        return
    args = context.args or []
    if len(args) != 2 or not args[0].isdigit() or args[1] not in ROLES:
        await update.message.reply_text("Usage: /delrole <telegram_user_id> <admin|jcrc|press>")
        return
    await dbx.remove_role(context.bot_data["db"], int(args[0]), args[1])
    await update.message.reply_text("✅ Removed.")


async def banauthor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/banauthor RHXXXXX — ban the (still-anonymous) author of a confession."""
    if not await _require_admin(update, context):
        return
    args = context.args or []
    if not args:
        await update.message.reply_text("Usage: /banauthor <confession code>")
        return
    db = context.bot_data["db"]
    conf = await dbx.get_confession(db, code=args[0].lstrip("#"))
    if not conf:
        await update.message.reply_text("Code not found.")
        return
    await dbx.set_banned(db, conf["author_hash"], True)
    await update.message.reply_text(f"🚫 Author of #{conf['code']} banned (identity unknown to us).")


async def unbanauthor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _require_admin(update, context):
        return
    args = context.args or []
    if not args:
        await update.message.reply_text("Usage: /unbanauthor <confession code>")
        return
    db = context.bot_data["db"]
    conf = await dbx.get_confession(db, code=args[0].lstrip("#"))
    if not conf:
        await update.message.reply_text("Code not found.")
        return
    await dbx.set_banned(db, conf["author_hash"], False)
    await update.message.reply_text(f"✅ Author of #{conf['code']} unbanned.")


async def clearstrikes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/clearstrikes <confession code> — reset strikes for that (anonymous) author.
    /clearstrikes all — reset everyone's strikes and unban all."""
    if not await _require_admin(update, context):
        return
    db = context.bot_data["db"]
    args = context.args or []
    if not args:
        await update.message.reply_text("Usage: /clearstrikes <code>  |  /clearstrikes all")
        return
    if args[0].lower() == "all":
        await db.execute("UPDATE users SET strikes=0, banned=0")
        await db.commit()
        await update.message.reply_text("✅ All strikes cleared and bans lifted.")
        return
    conf = await dbx.get_confession(db, code=args[0].lstrip("#"))
    if not conf:
        await update.message.reply_text("Code not found.")
        return
    await db.execute(
        "UPDATE users SET strikes=0, banned=0 WHERE user_hash=?", (conf["author_hash"],)
    )
    await db.commit()
    await update.message.reply_text(f"✅ Strikes cleared for the author of #{conf['code']}.")


async def takedown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/takedown RHXXXXX — delete a posted confession from the channel."""
    if not await _require_admin(update, context):
        return
    args = context.args or []
    if not args:
        await update.message.reply_text("Usage: /takedown <confession code>")
        return
    db = context.bot_data["db"]
    conf = await dbx.get_confession(db, code=args[0].lstrip("#"))
    if not conf or not conf.get("channel_msg_id"):
        await update.message.reply_text("Code not found / not posted.")
        return
    try:
        await context.bot.delete_message(settings.CHANNEL_ID, conf["channel_msg_id"])
    except Exception:  # noqa: BLE001 — >48h old messages can't be deleted by bots
        await update.message.reply_text("⚠️ Couldn't delete (older than 48h?). Marked as rejected in DB.")
    await dbx.set_status(db, conf["id"], "rejected")
    await update.message.reply_text(f"🗑 #{conf['code']} taken down.")


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _require_admin(update, context):
        return
    s = await dbx.stats(context.bot_data["db"])
    await update.message.reply_text(
        "📊 <b>Stats</b>\n" + "\n".join(f"• {k}: {v}" for k, v in s.items()),
        parse_mode=ParseMode.HTML,
    )


async def testdigest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/testdigest — fire the 8am digest right now to preview it."""
    if not await _require_admin(update, context):
        return
    from digest import send_digest
    await send_digest(context)
    await update.message.reply_text("Digest fired.")


async def modstats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/modstats [days] — moderation decisions summary, to tune /setmode with data."""
    if not await _require_admin(update, context):
        return
    args = context.args or []
    days = int(args[0]) if args and args[0].isdigit() else 30
    s = await dbx.mod_stats(context.bot_data["db"], days)
    v = s["by_verdict"]
    total = sum(v.values()) or 1
    lines = [f"🧮 <b>Moderation, last {days}d</b> ({total} decisions)"]
    for verdict in ("ALLOW", "REVIEW", "REJECT"):
        n = v.get(verdict, 0)
        lines.append(f"• {verdict}: {n} ({100*n//total}%)")
    ro = s["review_outcomes"]
    approved, rejected = ro.get("approved", 0), ro.get("rejected", 0)
    if approved + rejected:
        rate = 100 * approved // (approved + rejected)
        lines.append(f"• Reviews resolved: {approved} approved / {rejected} rejected ({rate}% approved)")
        if rate >= 80:
            lines.append("💡 Most reviews get approved — consider /setmode standard.")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def setmode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/setmode strict|standard|relaxed — change moderation strictness live."""
    if not await _require_admin(update, context):
        return
    args = context.args or []
    if not args or args[0] not in ("strict", "standard", "relaxed"):
        cur = await dbx.get_setting(context.bot_data["db"], "mod_level")
        await update.message.reply_text(
            f"Current mode: {cur or 'strict'}\nUsage: /setmode strict|standard|relaxed"
        )
        return
    await dbx.set_setting(context.bot_data["db"], "mod_level", args[0])
    await update.message.reply_text(f"🎚 Moderation level set to: {args[0]} (effective immediately)")


async def allowtopic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/allowtopic <topic phrase> — whitelist a topic. No args = show list. 'clear' = reset."""
    await _edit_topic_list(update, context, "allow_topics", "whitelisted")


async def blocktopic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/blocktopic <topic phrase> — hard-block a topic. No args = show list. 'clear' = reset."""
    await _edit_topic_list(update, context, "block_topics", "blocked")


async def _edit_topic_list(update, context, key, label):
    if not await _require_admin(update, context):
        return
    db = context.bot_data["db"]
    phrase = " ".join(context.args or []).strip()
    current = await dbx.get_setting(db, key)
    topics = [t for t in (current.split("|") if current else []) if t]
    if not phrase:
        await update.message.reply_text(
            f"{label.capitalize()} topics: {', '.join(topics) if topics else '(none)'}\n"
            f"Add: /{'allowtopic' if key == 'allow_topics' else 'blocktopic'} <phrase> · reset: … clear"
        )
        return
    if phrase.lower() == "clear":
        await dbx.del_setting(db, key)
        await update.message.reply_text(f"✅ {label.capitalize()} topics cleared.")
        return
    if "|" in phrase:
        await update.message.reply_text("Topic phrases can't contain '|'.")
        return
    topics.append(phrase)
    await dbx.set_setting(db, key, "|".join(topics))
    await update.message.reply_text(f"✅ Now {label}: {', '.join(topics)} (effective immediately)")


def build_handlers():
    f = filters.ChatType.GROUPS
    return [
        CommandHandler("claimadmin", claimadmin, f),
        CommandHandler("addrole", addrole, f),
        CommandHandler("delrole", delrole, f),
        CommandHandler("banauthor", banauthor, f),
        CommandHandler("unbanauthor", unbanauthor, f),
        CommandHandler("takedown", takedown, f),
        CommandHandler("clearstrikes", clearstrikes, f),
        CommandHandler("stats", stats, f),
        CommandHandler("testdigest", testdigest, f),
        CommandHandler("modstats", modstats, f),
        CommandHandler("setmode", setmode, f),
        CommandHandler("allowtopic", allowtopic, f),
        CommandHandler("blocktopic", blocktopic, f),
    ]
