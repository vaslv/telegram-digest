"""Small cross-cutting helpers."""

from __future__ import annotations

from datetime import UTC, datetime

from tgdigest.db.enums import ChatType


def utcnow() -> datetime:
    """Timezone-aware current time in UTC (DB columns are ``timestamptz``)."""
    return datetime.now(UTC)


def infer_chat_type(telegram_chat_id: int) -> ChatType:
    """Best-effort chat type from a marked id (daemon backfills the real one)."""
    if telegram_chat_id >= 0:
        return ChatType.private
    if str(telegram_chat_id).startswith("-100"):
        return ChatType.supergroup
    return ChatType.group
