"""Cache of available Telegram dialogs (written by the daemon)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from tgdigest.db.models import Dialog
from tgdigest.db.repositories.base import BaseRepository


class DialogRepository(BaseRepository):
    async def upsert_many(self, rows: Sequence[dict[str, Any]]) -> int:
        if not rows:
            return 0
        keys = sorted({k for row in rows for k in row})
        values = [{k: row.get(k) for k in keys} for row in rows]
        stmt = pg_insert(Dialog).values(values)
        stmt = stmt.on_conflict_do_update(
            index_elements=["telegram_chat_id"],
            set_={
                "title": stmt.excluded.title,
                "chat_type": stmt.excluded.chat_type,
                "username": stmt.excluded.username,
                "is_member": stmt.excluded.is_member,
                "refreshed_at": func.now(),
            },
        )
        await self.session.execute(stmt)
        return len(values)

    async def list(self, *, query: str | None = None, limit: int = 200) -> list[Dialog]:
        stmt = select(Dialog)
        if query:
            stmt = stmt.where(func.lower(Dialog.title).like(f"%{query.lower()}%"))
        stmt = stmt.order_by(Dialog.title).limit(limit)
        return list((await self.session.execute(stmt)).scalars().all())

    async def get(self, telegram_chat_id: int) -> Dialog | None:
        return await self.session.get(Dialog, telegram_chat_id)

    async def get_by_username(self, username: str) -> Dialog | None:
        stmt = select(Dialog).where(func.lower(Dialog.username) == username.lstrip("@").lower())
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def last_refresh(self) -> datetime | None:
        return (await self.session.execute(select(func.max(Dialog.refreshed_at)))).scalar()
