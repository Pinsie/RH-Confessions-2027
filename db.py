"""SQLite persistence layer (aiosqlite).

Privacy notes:
- users / confessions / comments store only the HMAC hash, never a user ID.
- roles stores real user IDs by necessity (JCRC/Press are NOT anonymous roles).
- relay stores Fernet-encrypted chat IDs for the anon-chat feature, TTL-purged.
"""
from __future__ import annotations

import random
import string
import time
from typing import Any

import aiosqlite

from config import settings

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_hash   TEXT PRIMARY KEY,
    alias       TEXT NOT NULL,
    strikes     INTEGER NOT NULL DEFAULT 0,
    banned      INTEGER NOT NULL DEFAULT 0,
    created_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS confessions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    code            TEXT UNIQUE NOT NULL,          -- e.g. RH4K7QZ
    author_hash     TEXT NOT NULL,
    category        TEXT,                          -- #Studies, #Hall, ...
    title           TEXT,
    body            TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending', -- pending|posted|rejected|review
    channel_msg_id  INTEGER,
    group_msg_id    INTEGER,                       -- auto-forward in discussion group
    photo_file_id   TEXT,
    media_kind      TEXT,
    created_at      INTEGER NOT NULL,
    posted_at       INTEGER
);

CREATE TABLE IF NOT EXISTS comments (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    confession_id INTEGER NOT NULL REFERENCES confessions(id),
    author_hash   TEXT NOT NULL,
    alias         TEXT NOT NULL,
    body          TEXT NOT NULL,
    created_at    INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS roles (
    user_id   INTEGER NOT NULL,
    role      TEXT NOT NULL CHECK (role IN ('admin','jcrc','press')),
    added_by  INTEGER,
    added_at  INTEGER NOT NULL,
    PRIMARY KEY (user_id, role)
);

CREATE TABLE IF NOT EXISTS news_items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT NOT NULL CHECK (kind IN ('article','event','sport','announce')),
    title       TEXT NOT NULL,
    body        TEXT NOT NULL,
    byline      TEXT,                 -- "RH Press" / "JCRC"
    event_date  TEXT,                 -- YYYY-MM-DD for events
    channel_msg_id INTEGER,
    created_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS relay (
    confession_code   TEXT PRIMARY KEY,
    enc_author_chat   TEXT NOT NULL,   -- Fernet-encrypted chat id
    created_at        INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS chat_sessions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    confession_code TEXT NOT NULL,
    a_hash        TEXT NOT NULL,       -- initiator (hash)
    enc_a_chat    TEXT NOT NULL,
    enc_b_chat    TEXT NOT NULL,       -- confessor
    active        INTEGER NOT NULL DEFAULT 1,
    created_at    INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mod_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    kind       TEXT NOT NULL,        -- confession|comment|chat|poll|feedback|review_outcome
    verdict    TEXT NOT NULL,        -- ALLOW|REVIEW|REJECT|approved|rejected
    reason     TEXT,
    mode       TEXT,                 -- strictness level at decision time
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS feedback (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    enc_chat   TEXT NOT NULL,        -- Fernet-encrypted sender chat id
    body       TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'open',
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS reports (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    target_kind   TEXT NOT NULL,          -- confession|poll
    target_ref    TEXT NOT NULL,          -- confession code, or poll msg id
    channel_msg_id INTEGER,
    reporter_hash TEXT NOT NULL,
    created_at    INTEGER NOT NULL,
    UNIQUE(target_ref, reporter_hash)     -- one report per person per post
);

CREATE TABLE IF NOT EXISTS ingested_posts (
    source_msg_id INTEGER PRIMARY KEY,   -- message id in the block channel
    was_event     INTEGER NOT NULL DEFAULT 0,
    created_at    INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_conf_status ON confessions(status);
CREATE INDEX IF NOT EXISTS idx_news_kind ON news_items(kind, created_at);
"""


async def connect() -> aiosqlite.Connection:
    conn = await aiosqlite.connect(settings.DB_PATH)
    conn.row_factory = aiosqlite.Row
    await conn.executescript(_SCHEMA)
    # Lightweight migration for DBs created before photo support.
    for stmt in (
        "ALTER TABLE confessions ADD COLUMN photo_file_id TEXT",
        "ALTER TABLE confessions ADD COLUMN media_kind TEXT",
    ):
        try:
            await conn.execute(stmt)
        except Exception:  # noqa: BLE001 — column already exists
            pass
    await conn.commit()
    return conn


def _now() -> int:
    return int(time.time())


def gen_code() -> str:
    return "RH" + "".join(random.choices(string.ascii_uppercase + string.digits, k=5))


# ------------------------------------------------------------------ users

async def ensure_user(db, uhash: str, alias: str) -> dict[str, Any]:
    await db.execute(
        "INSERT OR IGNORE INTO users(user_hash, alias, created_at) VALUES(?,?,?)",
        (uhash, alias, _now()),
    )
    await db.commit()
    cur = await db.execute("SELECT * FROM users WHERE user_hash=?", (uhash,))
    return dict(await cur.fetchone())


async def add_strike(db, uhash: str) -> int:
    await db.execute("UPDATE users SET strikes = strikes + 1 WHERE user_hash=?", (uhash,))
    await db.commit()
    cur = await db.execute("SELECT strikes FROM users WHERE user_hash=?", (uhash,))
    row = await cur.fetchone()
    strikes = row["strikes"] if row else 0
    if strikes >= settings.MAX_STRIKES_BEFORE_BAN:
        await db.execute("UPDATE users SET banned=1 WHERE user_hash=?", (uhash,))
        await db.commit()
    return strikes


async def set_banned(db, uhash: str, banned: bool):
    await db.execute("UPDATE users SET banned=? WHERE user_hash=?", (int(banned), uhash))
    await db.commit()


async def is_banned(db, uhash: str) -> bool:
    cur = await db.execute("SELECT banned FROM users WHERE user_hash=?", (uhash,))
    row = await cur.fetchone()
    return bool(row and row["banned"])


# ------------------------------------------------------------ confessions

async def create_confession(db, code, author_hash, category, title, body, status,
                            photo_id=None, media_kind=None) -> int:
    cur = await db.execute(
        "INSERT INTO confessions(code, author_hash, category, title, body, status,"
        " photo_file_id, media_kind, created_at) VALUES(?,?,?,?,?,?,?,?,?)",
        (code, author_hash, category, title, body, status, photo_id, media_kind, _now()),
    )
    await db.commit()
    return cur.lastrowid


async def get_confession(db, *, code=None, cid=None):
    if code:
        cur = await db.execute("SELECT * FROM confessions WHERE code=?", (code,))
    else:
        cur = await db.execute("SELECT * FROM confessions WHERE id=?", (cid,))
    row = await cur.fetchone()
    return dict(row) if row else None


async def mark_posted(db, cid: int, channel_msg_id: int):
    await db.execute(
        "UPDATE confessions SET status='posted', channel_msg_id=?, posted_at=? WHERE id=?",
        (channel_msg_id, _now(), cid),
    )
    await db.commit()


async def set_status(db, cid: int, status: str):
    await db.execute("UPDATE confessions SET status=? WHERE id=?", (status, cid))
    await db.commit()


async def map_group_msg(db, channel_msg_id: int, group_msg_id: int):
    await db.execute(
        "UPDATE confessions SET group_msg_id=? WHERE channel_msg_id=?",
        (group_msg_id, channel_msg_id),
    )
    await db.commit()


async def top_confession_yesterday(db):
    """Most-commented confession posted in the last ~36h (proxy for 'top')."""
    cutoff = _now() - 36 * 3600
    cur = await db.execute(
        """SELECT c.code, c.title, c.body, COUNT(cm.id) AS n
           FROM confessions c LEFT JOIN comments cm ON cm.confession_id = c.id
           WHERE c.status='posted' AND c.posted_at >= ?
           GROUP BY c.id ORDER BY n DESC, c.posted_at DESC LIMIT 1""",
        (cutoff,),
    )
    row = await cur.fetchone()
    return dict(row) if row else None


# --------------------------------------------------------------- comments

async def add_comment(db, confession_id, author_hash, alias, body) -> int:
    cur = await db.execute(
        "INSERT INTO comments(confession_id, author_hash, alias, body, created_at) VALUES(?,?,?,?,?)",
        (confession_id, author_hash, alias, body, _now()),
    )
    await db.commit()
    return cur.lastrowid


# ------------------------------------------------------------------ roles

async def add_role(db, user_id: int, role: str, added_by: int):
    await db.execute(
        "INSERT OR IGNORE INTO roles(user_id, role, added_by, added_at) VALUES(?,?,?,?)",
        (user_id, role, added_by, _now()),
    )
    await db.commit()


async def remove_role(db, user_id: int, role: str):
    await db.execute("DELETE FROM roles WHERE user_id=? AND role=?", (user_id, role))
    await db.commit()


async def has_role(db, user_id: int, *roles: str) -> bool:
    q = ",".join("?" for _ in roles)
    cur = await db.execute(
        f"SELECT 1 FROM roles WHERE user_id=? AND role IN ({q}) LIMIT 1",
        (user_id, *roles),
    )
    return (await cur.fetchone()) is not None


# ------------------------------------------------------------------- news

async def add_news(db, kind, title, body, byline, event_date=None) -> int:
    cur = await db.execute(
        "INSERT INTO news_items(kind, title, body, byline, event_date, created_at) VALUES(?,?,?,?,?,?)",
        (kind, title, body, byline, event_date, _now()),
    )
    await db.commit()
    return cur.lastrowid


async def news_for_digest(db, today: str):
    """Returns (yesterday's sports results, today's events, recent articles/announcements)."""
    day_ago = _now() - 26 * 3600
    cur = await db.execute(
        "SELECT * FROM news_items WHERE kind='sport' AND created_at>=? ORDER BY created_at", (day_ago,)
    )
    sports = [dict(r) for r in await cur.fetchall()]

    cur = await db.execute(
        "SELECT * FROM news_items WHERE kind='event' AND event_date=? ORDER BY created_at", (today,)
    )
    events = [dict(r) for r in await cur.fetchall()]

    cur = await db.execute(
        "SELECT * FROM news_items WHERE kind IN ('article','announce') AND created_at>=? "
        "ORDER BY created_at DESC LIMIT 3", (day_ago,)
    )
    articles = [dict(r) for r in await cur.fetchall()]
    return sports, events, articles


# ------------------------------------------------------------------ relay

async def save_relay(db, code: str, enc_chat: str):
    await db.execute(
        "INSERT OR REPLACE INTO relay(confession_code, enc_author_chat, created_at) VALUES(?,?,?)",
        (code, enc_chat, _now()),
    )
    await db.commit()


async def get_relay(db, code: str) -> str | None:
    cur = await db.execute("SELECT enc_author_chat FROM relay WHERE confession_code=?", (code,))
    row = await cur.fetchone()
    return row["enc_author_chat"] if row else None


async def purge_expired_relays(db):
    cutoff = _now() - settings.ANON_CHAT_TTL_DAYS * 86400
    await db.execute("DELETE FROM relay WHERE created_at < ?", (cutoff,))
    await db.execute("DELETE FROM chat_sessions WHERE created_at < ?", (cutoff,))
    await db.commit()


async def purge_old_author_hashes(db):
    """Blank author hashes older than AUTHOR_HASH_TTL_DAYS (0 = keep forever).
    Posts stay; the author link is permanently severed. Bans placed earlier
    remain enforced (they live on the users table, keyed by the stable hash)."""
    if settings.AUTHOR_HASH_TTL_DAYS <= 0:
        return
    cutoff = _now() - settings.AUTHOR_HASH_TTL_DAYS * 86400
    await db.execute(
        "UPDATE confessions SET author_hash='purged' "
        "WHERE author_hash != 'purged' AND created_at < ?", (cutoff,),
    )
    await db.execute(
        "UPDATE comments SET author_hash='purged' "
        "WHERE author_hash != 'purged' AND created_at < ?", (cutoff,),
    )
    await db.commit()


# --------------------------------------------------------------- settings

async def get_setting(db, key: str) -> str | None:
    cur = await db.execute("SELECT value FROM settings WHERE key=?", (key,))
    row = await cur.fetchone()
    return row["value"] if row else None


async def set_setting(db, key: str, value: str):
    await db.execute("INSERT OR REPLACE INTO settings(key, value) VALUES(?,?)", (key, value))
    await db.commit()


async def del_setting(db, key: str):
    await db.execute("DELETE FROM settings WHERE key=?", (key,))
    await db.commit()


# ---------------------------------------------------------------- reports

async def add_report(db, target_kind: str, target_ref: str,
                     channel_msg_id: int, reporter_hash: str) -> int | None:
    """Record a report. Returns the new total, or None if this person already
    reported this post (duplicate reports don't count twice)."""
    try:
        await db.execute(
            "INSERT INTO reports(target_kind, target_ref, channel_msg_id, reporter_hash, created_at)"
            " VALUES(?,?,?,?,?)",
            (target_kind, target_ref, channel_msg_id, reporter_hash, _now()),
        )
        await db.commit()
    except Exception:  # noqa: BLE001 — UNIQUE violation = already reported
        return None
    cur = await db.execute("SELECT COUNT(*) c FROM reports WHERE target_ref=?", (target_ref,))
    return (await cur.fetchone())["c"]


async def report_count(db, target_ref: str) -> int:
    cur = await db.execute("SELECT COUNT(*) c FROM reports WHERE target_ref=?", (target_ref,))
    return (await cur.fetchone())["c"]


# ---------------------------------------------------------------- mod log

async def log_mod(db, kind: str, verdict: str, reason: str, mode: str):
    await db.execute(
        "INSERT INTO mod_log(kind, verdict, reason, mode, created_at) VALUES(?,?,?,?,?)",
        (kind, verdict, (reason or "")[:200], mode, _now()),
    )
    await db.commit()


async def mod_stats(db, days: int = 30) -> dict:
    cutoff = _now() - days * 86400
    out = {"days": days, "by_verdict": {}, "review_outcomes": {}}
    cur = await db.execute(
        "SELECT verdict, COUNT(*) c FROM mod_log "
        "WHERE created_at>=? AND kind != 'review_outcome' GROUP BY verdict", (cutoff,)
    )
    for r in await cur.fetchall():
        out["by_verdict"][r["verdict"]] = r["c"]
    cur = await db.execute(
        "SELECT verdict, COUNT(*) c FROM mod_log "
        "WHERE created_at>=? AND kind='review_outcome' GROUP BY verdict", (cutoff,)
    )
    for r in await cur.fetchall():
        out["review_outcomes"][r["verdict"]] = r["c"]
    return out


# --------------------------------------------------------------- feedback

async def create_feedback(db, enc_chat: str, body: str) -> int:
    cur = await db.execute(
        "INSERT INTO feedback(enc_chat, body, created_at) VALUES(?,?,?)",
        (enc_chat, body, _now()),
    )
    await db.commit()
    return cur.lastrowid


async def get_feedback(db, fid: int):
    cur = await db.execute("SELECT * FROM feedback WHERE id=?", (fid,))
    row = await cur.fetchone()
    return dict(row) if row else None


async def purge_old_feedback(db, ttl_days: int):
    cutoff = _now() - ttl_days * 86400
    await db.execute("DELETE FROM feedback WHERE created_at < ?", (cutoff,))
    await db.commit()


# ---------------------------------------------------------- chat sessions

async def create_session(db, code, a_hash, enc_a, enc_b) -> int:
    cur = await db.execute(
        "INSERT INTO chat_sessions(confession_code, a_hash, enc_a_chat, enc_b_chat, created_at)"
        " VALUES(?,?,?,?,?)",
        (code, a_hash, enc_a, enc_b, _now()),
    )
    await db.commit()
    return cur.lastrowid


async def get_session(db, sid: int):
    cur = await db.execute("SELECT * FROM chat_sessions WHERE id=? AND active=1", (sid,))
    row = await cur.fetchone()
    return dict(row) if row else None


async def end_session(db, sid: int):
    await db.execute("UPDATE chat_sessions SET active=0 WHERE id=?", (sid,))
    await db.commit()


# ------------------------------------------------------------------ stats

async def stats(db) -> dict:
    out = {}
    for name, q in [
        ("confessions_posted", "SELECT COUNT(*) c FROM confessions WHERE status='posted'"),
        ("confessions_rejected", "SELECT COUNT(*) c FROM confessions WHERE status='rejected'"),
        ("comments", "SELECT COUNT(*) c FROM comments"),
        ("users", "SELECT COUNT(*) c FROM users"),
        ("banned", "SELECT COUNT(*) c FROM users WHERE banned=1"),
        ("news_items", "SELECT COUNT(*) c FROM news_items"),
    ]:
        cur = await db.execute(q)
        out[name] = (await cur.fetchone())["c"]
    return out
