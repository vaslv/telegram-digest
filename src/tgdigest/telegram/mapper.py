"""Map a Telethon message into a plain dict ready for ``MessageRepository``.

Kept free of I/O and Telethon-only imports for the attribute access path so it
can be unit-tested with lightweight fakes. Sender names are best-effort: the
ingestor may pass a resolved name, otherwise it is derived from an already
loaded ``message.sender``.
"""

from __future__ import annotations

from typing import Any

from tgdigest.db.enums import MediaType


def display_name(entity: Any) -> str | None:
    """Human-readable name for a User / Chat / Channel entity."""
    if entity is None:
        return None
    title = getattr(entity, "title", None)
    if title:
        return str(title)
    first = getattr(entity, "first_name", None)
    last = getattr(entity, "last_name", None)
    parts = [p for p in (first, last) if p]
    if parts:
        return " ".join(parts)
    username = getattr(entity, "username", None)
    return str(username) if username else None


def _media_type(message: Any) -> MediaType | None:
    checks: tuple[tuple[str, MediaType], ...] = (
        ("photo", MediaType.photo),
        ("video_note", MediaType.video_note),
        ("video", MediaType.video),
        ("voice", MediaType.voice),
        ("audio", MediaType.audio),
        ("gif", MediaType.gif),
        ("sticker", MediaType.sticker),
        ("poll", MediaType.poll),
        ("geo", MediaType.geo),
        ("contact", MediaType.contact),
        ("web_preview", MediaType.webpage),
        ("document", MediaType.document),
    )
    for attr, media in checks:
        if getattr(message, attr, None):
            return media
    if getattr(message, "media", None):
        return MediaType.other
    return None


def _reactions(message: Any) -> list[dict[str, Any]] | None:
    reactions = getattr(message, "reactions", None)
    results = getattr(reactions, "results", None) if reactions else None
    if not results:
        return None
    out: list[dict[str, Any]] = []
    for item in results:
        reaction = getattr(item, "reaction", None)
        emoji = getattr(reaction, "emoticon", None) or "custom"
        out.append({"emoji": emoji, "count": getattr(item, "count", 0)})
    return out or None


def _reply_to(message: Any) -> int | None:
    reply = getattr(message, "reply_to", None)
    if reply is None:
        return None
    return getattr(reply, "reply_to_msg_id", None)


def _forward_from(message: Any) -> str | None:
    forward = getattr(message, "forward", None)
    if not forward:
        return None
    name = getattr(forward, "from_name", None)
    return str(name) if name else None


def map_message(message: Any, chat_id: int, *, sender_name: str | None = None) -> dict[str, Any]:
    action = getattr(message, "action", None)
    is_service = action is not None
    service_action = type(action).__name__ if is_service else None

    raw_text = getattr(message, "message", None) or None
    media_type = _media_type(message)
    has_real_media = (
        getattr(message, "media", None) is not None
        and getattr(message, "web_preview", None) is None
    )

    if is_service:
        text, caption = None, None
    elif has_real_media:
        text, caption = None, raw_text
    else:
        text, caption = raw_text, None

    name = sender_name or display_name(getattr(message, "sender", None))

    return {
        "chat_id": chat_id,
        "telegram_message_id": message.id,
        "sender_id": getattr(message, "sender_id", None),
        "sender_name": name,
        "date": message.date,
        "reply_to_message_id": _reply_to(message),
        "text": text,
        "media_type": media_type,
        "media_caption": caption,
        "views": getattr(message, "views", None),
        "reactions": _reactions(message),
        "forward_from": _forward_from(message),
        "is_service": is_service,
        "service_action": service_action,
        "edit_date": getattr(message, "edit_date", None),
        "raw": None,
    }
