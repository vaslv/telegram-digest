"""Stage-1 importance events."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import select

from tgdigest.db.models import ImportanceEvent
from tgdigest.db.repositories.base import BaseRepository


class AnalysisRepository(BaseRepository):
    async def add_events(
        self, *, run_id: int, chat_id: int, events: Sequence[dict[str, Any]]
    ) -> list[ImportanceEvent]:
        if not events:
            return []
        rows = [
            ImportanceEvent(
                chat_id=chat_id,
                digest_run_id=run_id,
                message_id=event.get("message_id"),
                telegram_message_id=event.get("telegram_message_id"),
                importance_type=event["importance_type"],
                summary=event["summary"],
                reason=event.get("reason"),
                confidence=float(event.get("confidence", 0.0)),
                related_message_ids=event.get("related_message_ids"),
            )
            for event in events
        ]
        self.session.add_all(rows)
        await self.session.flush()
        return rows

    async def events_for_run(self, run_id: int) -> list[ImportanceEvent]:
        stmt = (
            select(ImportanceEvent)
            .where(ImportanceEvent.digest_run_id == run_id)
            .order_by(ImportanceEvent.confidence.desc())
        )
        return list((await self.session.execute(stmt)).scalars().all())
