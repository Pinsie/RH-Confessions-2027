"""Anonymity & abuse-control primitives.

Design (read this before touching anything):

1. IDENTITY HASHING — we never store a raw Telegram user ID next to any
   confession or comment. We store HMAC-SHA256(SECRET_SALT, user_id).
   * Bans, strikes and rate limits are enforced on the hash.
   * Admins can ban "the author of #RH123AB" without ever learning who it is.
   * If the DB leaks, confessions cannot be mapped back to people without
     the salt (kept only in .env on the server).

2. ANON CHAT RELAY — "Chat with confessor" needs a way to route messages to
   the author, which requires their chat ID. That mapping is stored
   Fernet-ENCRYPTED (key in .env, not in DB) and auto-deleted after
   ANON_CHAT_TTL_DAYS. It is decrypted only in-memory at relay time.

3. ALIASES — every user gets a deterministic fun alias (e.g. quasarflick37)
   derived from their hash, so commenters have a stable pseudonym per
   account but no link to their real handle.

4. RATE LIMITING — in-memory sliding cooldowns per hash. Restarting the bot
   resets them, which is acceptable.
"""
from __future__ import annotations

import hashlib
import hmac
import time

from cryptography.fernet import Fernet, InvalidToken

from config import settings

# ---------------------------------------------------------------- identity

def user_hash(user_id: int) -> str:
    return hmac.new(
        settings.SECRET_SALT.encode(), str(user_id).encode(), hashlib.sha256
    ).hexdigest()


# ---------------------------------------------------------------- aliases

_ADJ = [
    "quasar", "nebula", "turbo", "mellow", "crimson", "velvet", "cosmic",
    "silent", "electric", "golden", "midnight", "jade", "solar", "frosty",
    "wild", "lunar", "amber", "swift", "shadow", "pixel", "mystic", "neon",
    "rustic", "azure", "ember", "drift", "echo", "nova", "zen", "arc",
]
_NOUN = [
    "flick", "otter", "falcon", "willow", "comet", "panda", "sprocket",
    "wave", "ripple", "raven", "maple", "storm", "ember", "lynx", "orbit",
    "sprout", "glide", "spark", "fern", "tide", "cricket", "breeze",
    "pebble", "quill", "dusk", "meadow", "flare", "moth", "reef", "cinder",
]


def alias_for(uhash: str) -> str:
    """Deterministic pseudonym per user hash, e.g. quasarflick37."""
    n = int(uhash[:12], 16)
    a = _ADJ[n % len(_ADJ)]
    b = _NOUN[(n // len(_ADJ)) % len(_NOUN)]
    num = (n // (len(_ADJ) * len(_NOUN))) % 100
    return f"{a}{b}{num:02d}"


# ---------------------------------------------------------------- relay crypto

def _fernet() -> Fernet:
    return Fernet(settings.FERNET_KEY.encode())


def encrypt_chat_id(chat_id: int) -> str:
    return _fernet().encrypt(str(chat_id).encode()).decode()


def decrypt_chat_id(token: str) -> int | None:
    try:
        return int(_fernet().decrypt(token.encode()).decode())
    except (InvalidToken, ValueError):
        return None


# ---------------------------------------------------------------- rate limits

_last_action: dict[tuple[str, str], float] = {}


def cooldown_remaining(uhash: str, action: str, seconds: int) -> int:
    """0 if allowed; otherwise seconds remaining. Records the attempt on success."""
    key = (uhash, action)
    now = time.monotonic()
    last = _last_action.get(key)
    if last is not None and now - last < seconds:
        return int(seconds - (now - last)) + 1
    _last_action[key] = now
    return 0
