"""Digest runs and rendered digests."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update

from tgdigest.db.enums import RunStatus, RunTrigger
from tgdigest.db.models import Digest, DigestRun
from tgdigest.db.repositories.base import BaseRepository


class DigestRepository(BaseRepository):
    async def create_run(
        self,
        *,
        chat_id: int,
        trigger: RunTrigger,
        period_start: datetime | None = None,
        period_end: datetime | None = None,
        from_message_id: int | None = None,
        to_message_id: int | None = None,
        llm_provider: str | None = None,
        llm_model: str | None = None,
    ) -> DigestRun:
        run = DigestRun(
            chat_id=chat_id,
            trigger=trigger,
            status=RunStatus.running,
            period_start=period_start,
            period_end=period_end,
            from_message_id=from_message_id,
            to_message_id=to_message_id,
            llm_provider=llm_provider,
            llm_model=llm_model,
            started_at=datetime.now(UTC),
        )
        self.session.add(run)
        await self.session.flush()
        return run

    async def update_run(self, run_id: int, **fields: Any) -> None:
        if not fields:
            return
        await self.session.execute(
            update(DigestRun).where(DigestRun.id == run_id).values(**fields)
        )

    async def reap_stale_runs(self) -> int:
        """Mark runs left in ``running`` (e.g. after a crash) as failed."""
        stmt = (
            update(DigestRun)
            .where(DigestRun.status == RunStatus.running)
            .values(status=RunStatus.failed, error="stale run reaped on startup")
            .returning(DigestRun.id)
        )
        return len((await self.session.execute(stmt)).fetchall())

    async def save_digest(
        self,
        *,
        run_id: int,
        chat_id: int,
        title: str,
        period_start: datetime | None,
        period_end: datetime | None,
        summary: str | None,
        structured: dict[str, Any] | None,
        body_markdown: str | None,
        is_empty: bool,
        target_chat_id: int | None,
    ) -> Digest:
        digest = Digest(
            digest_run_id=run_id,
            chat_id=chat_id,
            title=title,
            period_start=period_start,
            period_end=period_end,
            summary=summary,
            structured=structured,
            body_markdown=body_markdown,
            is_empty=is_empty,
            target_chat_id=target_chat_id,
        )
        self.session.add(digest)
        await self.session.flush()
        return digest

    async def mark_sent(
        self, digest_id: int, *, target_chat_id: int | None, when: datetime
    ) -> None:
        await self.session.execute(
            update(Digest)
            .where(Digest.id == digest_id)
            .values(sent=True, sent_at=when, target_chat_id=target_chat_id)
        )

    async def recent_runs(self, chat_id: int, limit: int = 5) -> list[DigestRun]:
        stmt = (
            select(DigestRun)
            .where(DigestRun.chat_id == chat_id)
            .order_by(DigestRun.id.desc())
            .limit(limit)
        )
        return list((await self.session.execute(stmt)).scalars().all())
