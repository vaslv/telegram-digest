"""Raw message storage with deduplicating upserts."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from tgdigest.db.models import Message
from tgdigest.db.repositories.base import BaseRepository

_MUTABLE_ON_EDIT = ("text", "media_caption", "reactions", "views", "edit_date")


class MessageRepository(BaseRepository):
    async def insert_many(self, rows: Sequence[dict[str, Any]]) -> int:
        """Bulk insert, skipping duplicates. Returns the number of new rows.

        Rows are normalised to a uniform column set so a multi-row VALUES clause
        compiles even when callers omit ``None`` fields.
        """
        if not rows:
            return 0
        keys = sorted({k for row in rows for k in row})
        values = [{k: row.get(k) for k in keys} for row in rows]
        stmt = (
            pg_insert(Message)
            .values(values)
            .on_conflict_do_nothing(index_elements=["chat_id", "telegram_message_id"])
            .returning(Message.id)
        )
        result = await self.session.execute(stmt)
        return len(result.fetchall())

    async def apply_edit(self, chat_id: int, telegram_message_id: int, **fields: Any) -> None:
        values = {k: v for k, v in fields.items() if k in _MUTABLE_ON_EDIT}
        if not values:
            return
        await self.session.execute(
            update(Message)
            .where(
                Message.chat_id == chat_id,
                Message.telegram_message_id == telegram_message_id,
            )
            .values(**values)
        )

    async def max_telegram_id(self, chat_id: int) -> int:
        stmt = select(func.max(Message.telegram_message_id)).where(Message.chat_id == chat_id)
        return (await self.session.execute(stmt)).scalar() or 0

    async def count_after(self, chat_id: int, after_message_id: int) -> int:
        stmt = select(func.count()).where(
            Message.chat_id == chat_id,
            Message.telegram_message_id > after_message_id,
        )
        return (await self.session.execute(stmt)).scalar() or 0

    async def fetch_window(
        self,
        chat_id: int,
        *,
        after_message_id: int = 0,
        to_message_id: int | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int | None = None,
    ) -> list[Message]:
        stmt = select(Message).where(
            Message.chat_id == chat_id,
            Message.telegram_message_id > after_message_id,
        )
        if to_message_id is not None:
            stmt = stmt.where(Message.telegram_message_id <= to_message_id)
        if since is not None:
            stmt = stmt.where(Message.date >= since)
        if until is not None:
            stmt = stmt.where(Message.date <= until)
        stmt = stmt.order_by(Message.telegram_message_id)
        if limit is not None:
            stmt = stmt.limit(limit)
        return list((await self.session.execute(stmt)).scalars().all())

    async def get_by_telegram_ids(
        self, chat_id: int, telegram_ids: Sequence[int]
    ) -> dict[int, Message]:
        if not telegram_ids:
            return {}
        stmt = select(Message).where(
            Message.chat_id == chat_id,
            Message.telegram_message_id.in_(list(telegram_ids)),
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        return {m.telegram_message_id: m for m in rows}
