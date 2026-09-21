"""Media helpers: pull a Telegram attachment down and encode it for vision moderation.

Handles photos, GIFs/animations (moderated on their thumbnail — note the model
only sees one frame, so an innocent first frame can hide a bad later one) and
stickers. Anything above MAX_MEDIA_BYTES is refused rather than downloaded.
"""
from __future__ import annotations

import base64
import logging

log = logging.getLogger(__name__)

MAX_MEDIA_BYTES = 8 * 1024 * 1024  # 8 MB ceiling on what we'll fetch


class Media:
    """A moderatable attachment pulled off a Telegram message."""

    def __init__(self, kind: str, file_id: str, b64: str | None, mime: str):
        self.kind = kind          # photo | animation | sticker
        self.file_id = file_id    # what we re-send to the channel
        self.b64 = b64            # base64 image bytes for the vision model
        self.mime = mime


async def extract(message, bot) -> Media | None:
    """Return a Media object for the message's attachment, or None if text-only."""
    photo = getattr(message, "photo", None)
    animation = getattr(message, "animation", None)
    sticker = getattr(message, "sticker", None)

    if photo:
        best = photo[-1]                       # highest resolution variant
        # Moderate a mid-size variant to keep tokens down, post the full one.
        mod_target = photo[len(photo) // 2] if len(photo) > 1 else best
        return Media("photo", best.file_id, await _b64(mod_target.file_id, bot), "image/jpeg")

    if animation:
        thumb = getattr(animation, "thumbnail", None)
        b64 = await _b64(thumb.file_id, bot) if thumb else None
        return Media("animation", animation.file_id, b64, "image/jpeg")

    if sticker:
        if getattr(sticker, "is_animated", False) or getattr(sticker, "is_video", False):
            thumb = getattr(sticker, "thumbnail", None)
            b64 = await _b64(thumb.file_id, bot) if thumb else None
        else:
            b64 = await _b64(sticker.file_id, bot)
        return Media("sticker", sticker.file_id, b64, "image/webp")

    return None


async def _b64(file_id: str, bot) -> str | None:
    try:
        f = await bot.get_file(file_id)
        if f.file_size and f.file_size > MAX_MEDIA_BYTES:
            log.info("Media too large to moderate: %s bytes", f.file_size)
            return None
        data = await f.download_as_bytearray()
        return base64.b64encode(bytes(data)).decode()
    except Exception:  # noqa: BLE001 — never let media fetching crash a handler
        log.exception("Media download failed")
        return None
