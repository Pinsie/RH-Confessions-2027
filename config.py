"""Central configuration. Everything comes from .env — nothing hardcoded."""
import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
    BOT_USERNAME: str = os.getenv("BOT_USERNAME", "").lstrip("@")
    CHANNEL_ID: int = _int("CHANNEL_ID", 0)
    DISCUSSION_GROUP_ID: int = _int("DISCUSSION_GROUP_ID", 0)
    ADMIN_GROUP_ID: int = _int("ADMIN_GROUP_ID", 0)

    SECRET_SALT: str = os.getenv("SECRET_SALT", "")
    FERNET_KEY: str = os.getenv("FERNET_KEY", "")

    ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
    MODERATION_MODEL: str = os.getenv("MODERATION_MODEL", "claude-haiku-4-5-20251001")

    # OpenAI-compatible alternative (Gemini/Groq/Ollama). Takes priority if set.
    LLM_BASE_URL: str = os.getenv("LLM_BASE_URL", "").rstrip("/")
    LLM_API_KEY: str = os.getenv("LLM_API_KEY", "")
    LLM_MODEL: str = os.getenv("LLM_MODEL", "")

    AUTHOR_HASH_TTL_DAYS: int = _int("AUTHOR_HASH_TTL_DAYS", 4)

    JCRC_GROUP_ID: int = _int("JCRC_GROUP_ID", 0)   # 0 = feedback goes to admin group
    REPORT_PING_THRESHOLD: int = _int("REPORT_PING_THRESHOLD", 3)
    REPORT_HIDE_THRESHOLD: int = _int("REPORT_HIDE_THRESHOLD", 20)
    HEARTBEAT_ENABLED: bool = _bool("HEARTBEAT_ENABLED", True)

    TIMEZONE: str = os.getenv("TIMEZONE", "Asia/Singapore")
    DIGEST_HOUR: int = _int("DIGEST_HOUR", 8)
    DIGEST_MINUTE: int = _int("DIGEST_MINUTE", 0)

    CONFESS_COOLDOWN_SECONDS: int = _int("CONFESS_COOLDOWN_SECONDS", 300)
    COMMENT_COOLDOWN_SECONDS: int = _int("COMMENT_COOLDOWN_SECONDS", 60)
    MAX_CONFESSION_LEN: int = _int("MAX_CONFESSION_LEN", 2000)
    MAX_STRIKES_BEFORE_BAN: int = _int("MAX_STRIKES_BEFORE_BAN", 3)

    ANON_CHAT_ENABLED: bool = _bool("ANON_CHAT_ENABLED", True)
    ANON_CHAT_TTL_DAYS: int = _int("ANON_CHAT_TTL_DAYS", 30)

    # Block announcement channel the bot reads to auto-populate today's events.
    # 0 = feature off (fall back to manual /addevent only).
    BLOCK_CHANNEL_ID: int = _int("BLOCK_CHANNEL_ID", 0)

    DB_PATH: str = os.getenv("DB_PATH", "rh_confessions.db")


settings = Settings()


def validate() -> list[str]:
    """Return a list of fatal config problems (empty = OK)."""
    problems = []
    if not settings.BOT_TOKEN:
        problems.append("BOT_TOKEN missing")
    if not settings.BOT_USERNAME:
        problems.append("BOT_USERNAME missing")
    if not settings.CHANNEL_ID:
        problems.append("CHANNEL_ID missing")
    if not settings.DISCUSSION_GROUP_ID:
        problems.append("DISCUSSION_GROUP_ID missing")
    if not settings.ADMIN_GROUP_ID:
        problems.append("ADMIN_GROUP_ID missing")
    if not settings.SECRET_SALT or "CHANGE_ME" in settings.SECRET_SALT:
        problems.append("SECRET_SALT not set — generate one, this protects anonymity")
    if settings.ANON_CHAT_ENABLED and ("CHANGE_ME" in settings.FERNET_KEY or not settings.FERNET_KEY):
        problems.append("FERNET_KEY not set but ANON_CHAT_ENABLED=true")
    return problems
