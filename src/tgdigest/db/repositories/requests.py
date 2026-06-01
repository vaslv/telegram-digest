"""On-demand digest request queue (web/CLI enqueue, daemon executes)."""

from __future__ import annotations

from sqlalchemy import func, select, update

from tgdigest.db.enums import RequestStatus
from tgdigest.db.models import DigestRequest
from tgdigest.db.repositories.base import BaseRepository
from tgdigest.util import utcnow


class DigestRequestRepository(BaseRepository):
    async def enqueue(self, chat_id: int, *, dry_run: bool = False) -> DigestRequest:
        request = DigestRequest(chat_id=chat_id, dry_run=dry_run, status=RequestStatus.pending)
        self.session.add(request)
        await self.session.flush()
        return request

    async def claim_pending(self, limit: int = 5) -> list[DigestRequest]:
        """Lock and mark the next pending requests as running (SKIP LOCKED)."""
        stmt = (
            select(DigestRequest)
            .where(DigestRequest.status == RequestStatus.pending)
            .order_by(DigestRequest.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        rows = list((await self.session.execute(stmt)).scalars().all())
        for row in rows:
            row.status = RequestStatus.running
        await self.session.flush()
        return rows

    async def mark_done(self, request_id: int, *, digest_run_id: int | None) -> None:
        await self.session.execute(
            update(DigestRequest)
            .where(DigestRequest.id == request_id)
            .values(status=RequestStatus.done, digest_run_id=digest_run_id, finished_at=utcnow())
        )

    async def mark_failed(self, request_id: int, *, error: str) -> None:
        await self.session.execute(
            update(DigestRequest)
            .where(DigestRequest.id == request_id)
            .values(status=RequestStatus.failed, error=error[:4000], finished_at=utcnow())
        )

    async def reap_running(self) -> int:
        stmt = (
            update(DigestRequest)
            .where(DigestRequest.status == RequestStatus.running)
            .values(
                status=RequestStatus.failed,
                error="stale request reaped on startup",
                finished_at=utcnow(),
            )
            .returning(DigestRequest.id)
        )
        return len((await self.session.execute(stmt)).fetchall())

    async def recent(self, limit: int = 20) -> list[DigestRequest]:
        stmt = select(DigestRequest).order_by(DigestRequest.id.desc()).limit(limit)
        return list((await self.session.execute(stmt)).scalars().all())

    async def pending_count(self) -> int:
        stmt = select(func.count()).where(DigestRequest.status == RequestStatus.pending)
        return (await self.session.execute(stmt)).scalar() or 0

    async def get(self, request_id: int) -> DigestRequest | None:
        return await self.session.get(DigestRequest, request_id)
