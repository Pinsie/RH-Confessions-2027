# RH Confessions Bot 🦅

Anonymous confessions platform for Raffles Hall, modeled on the NUS ConfessIT
pattern — plus AI moderation, role-gated Press/JCRC publishing, and an
automated 8am daily digest.

## Feature summary

| Feature | How |
|---|---|
| Anonymous confessions | `/confess` in PM → AI-moderated → posted to channel with ID like `#RH4K7QZ` |
| AI moderation | Every confession/comment/relayed chat screened by Claude: ALLOW / REVIEW (admin buttons) / REJECT (strike). Blocks targeted harassment, doxxing, hate speech; flags distress and appends helpline info |
| Anonymous comments | "💬 Comment" button → deep link → PM the bot → posted in the discussion group as `Commenter quasarflick37` |
| Stable pseudonyms | Deterministic alias per user (derived from a salted hash) — recognizable across comments, unlinkable to identity |
| Chat with confessor | Double-blind encrypted relay, TTL-purged, every message moderated |
| Press/JCRC publishing | `/publish` (articles), `/announce` (JCRC), role-gated via `/addrole` |
| 8am daily digest | `/addresult` (yesterday's games) + `/addevent` (today's events) + latest press articles + hottest confession, optionally rewritten by Claude into a lively post. Skips silently if nothing to report |
| Anonymous moderation | Admins ban/unban by confession code — they never learn identities. `/takedown` deletes posts |
| Vision moderation | Photos, GIFs and stickers are shown to the model itself (not just the caption), so media auto-posts without manual review |
| Reporting | 🚩 button on every post. Deduped per person. Every report pings admins; `REPORT_PING_THRESHOLD` (3) makes it louder; `REPORT_HIDE_THRESHOLD` (20) auto-removes |
| Native polls | `/poll` uses Telegram's own poll composer. Optional photo/GIF posted directly above the poll (Telegram can't embed media in a poll message) |
| JCRC Direct Line | `/feedback` routes anonymously to `JCRC_GROUP_ID`; JCRC replies with `/fbreply <id> <msg>` |
| Moderation dial | `/setmode strict\|standard\|relaxed`, `/allowtopic`, `/blocktopic` — live, no restart. Hard floor (doxxing, hate, threats, sexual content re: real people) is never tunable |
| Mod analytics | Every verdict logged with the mode at the time; `/modstats [days]` shows the breakdown and suggests loosening |
| Heartbeat | Daily 💓 proof-of-life to the admin group 10 min after the digest — a missing heartbeat is your alert |
| Strikes & bans | Configurable strike limit → auto-ban. Cooldowns on confessing/commenting |

## Anonymity model (read this)

- **No raw user IDs stored** with content. Only `HMAC-SHA256(SECRET_SALT, user_id)`.
  If the database leaks, confessions can't be mapped to people without the salt.
- **Anon chat** needs routing, so author chat IDs are stored **Fernet-encrypted**
  (key in `.env` only) and auto-deleted after `ANON_CHAT_TTL_DAYS`. Set
  `ANON_CHAT_ENABLED=false` to store nothing reversible at all.
- **Roles are the exception**: JCRC/Press/admin user IDs are stored in plain —
  those are public, non-anonymous roles.
- Honest caveat for your `/privacy` message: whoever runs the server *could*
  modify the code to log identities. Anonymity here is a promise enforced by
  architecture + the integrity of the admin. Keep `.env` off git (see `.gitignore`).

## One-time Telegram setup (~15 min)

1. **Create the bot**: talk to [@BotFather](https://t.me/BotFather) → `/newbot`
   → get the token. Then `/setprivacy` → **Disable** (bot must read group
   messages to track comment threads).
2. **Create the channel** (e.g. "RH Confessions") — make it public or private,
   add the bot as **admin** with post/delete rights.
3. **Create the discussion group**, link it to the channel
   (Channel → Manage → Discussion), add the bot as **admin** there too.
4. **Create a private admin group** (you + trusted mods), add the bot.
5. **Get the three chat IDs**: add [@userinfobot](https://t.me/userinfobot) or
   forward a message from each chat to [@getidsbot](https://t.me/getidsbot).
   Channel/group IDs look like `-100xxxxxxxxxx`.
6. Fill in `.env` (copy from `.env.example`), generate `SECRET_SALT` and
   `FERNET_KEY` with the commands in the file comments.
7. Run the bot, then in the **admin group** send `/claimadmin` — first claimer
   becomes admin. Grant others: `/addrole <user_id> press` etc.

## Run it

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then edit it
python main.py
```

### Keep it alive for a year

**Option A — any always-on Linux box (systemd):**

```ini
# /etc/systemd/system/rhconfess.service
[Unit]
Description=RH Confessions Bot
After=network-online.target

[Service]
WorkingDirectory=/opt/rh-confessions
ExecStart=/opt/rh-confessions/venv/bin/python main.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

`sudo systemctl enable --now rhconfess`

**Option B — Docker:**

```bash
docker build -t rhconfess . && docker run -d --restart=always \
  --env-file .env -v $(pwd)/data:/app/data rhconfess
```

(Set `DB_PATH=data/rh_confessions.db` so the DB lives on the volume.)

Cheap hosting that works: Oracle Cloud free tier ARM VM (genuinely free),
a spare laptop in your hall room, or a $5/mo VPS. The bot uses long polling —
no domain, no webhook, no open ports needed.

### ⚠️ Before real launch

`.env` ships with **testing** cooldowns: `CONFESS_COOLDOWN_SECONDS=30` and
`COMMENT_COOLDOWN_SECONDS=10`. Raise these (300 / 60 are sensible) before
opening the channel to the hall, or one person can flood it.

**Back up `rh_confessions.db` weekly.** A cron line is enough:
`0 3 * * 0 cp /opt/rh-confessions/rh_confessions.db /opt/backups/rh_$(date +\%F).db`

## Daily Press/JCRC workflow

- After a game: `/addresult RH vs Eusoff — IHG Basketball | RH won 54–47, clutch Q4`
- Before an event: `/addevent 2026-07-04 | Block D Supper Night | 9pm, D lounge, free food`
- Long-form: `/publish` (guided flow, posts immediately + feeds the digest)
- Quick notice: `/announce Aircon maintenance in Block B tomorrow 10am–12pm`
- Preview the digest anytime: `/testdigest` in the admin group

At **08:00 SGT** the bot assembles everything from the past day and posts the
digest automatically. Nothing logged = no post (it won't spam an empty digest).

## Costs

- Hosting: $0–5/mo.
- Claude API moderation: Haiku is cheap — at a few hundred confessions/comments
  a month you're looking at well under US$1/mo. Without an API key the bot
  still runs, falling back to a keyword filter + human review for anything
  that looks like a name.

## File map

```
main.py                entrypoint, handler wiring, 8am job
config.py              .env loading + validation
db.py                  SQLite schema + queries
security.py            hashing, aliases, encryption, cooldowns
moderation.py          Claude moderation + digest polish
digest.py              8am digest builder
handlers/confess.py    /confess flow + admin review buttons
handlers/comments.py   /start deep links, anonymous comments, thread mapping
handlers/anonchat.py   chat-with-confessor relay
handlers/press.py      /publish /announce /addevent /addresult /mynews
handlers/admin.py      roles, bans-by-code, takedowns, stats, /testdigest
```

## Extending later

Easy wins in rough priority order: photo confessions (accept `filters.PHOTO`,
moderate the caption, forward with `send_photo`); weekly stats post; `/poll`
passthrough to native Telegram polls; reaction-count tracking via
`message_reaction` updates; a web dashboard reading the same SQLite file.
