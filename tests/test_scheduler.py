from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import update

from tgdigest.db.enums import ChatType, RunStatus, RunTrigger
from tgdigest.db.models import Chat
from tgdigest.db.repositories import ChatRepository, DigestRepository, MessageRepository
from tgdigest.scheduling import DigestScheduler

pytestmark = pytest.mark.integration


class FakeService:
    def __init__(self):
        self.runs: list[tuple[int, str]] = []

    async def run(self, chat_id, *, trigger, **_kw):
        self.runs.append((chat_id, trigger.value))


async def _make_chat(database):
    async with database.session() as session:
        chat = await ChatRepository(session).create_or_update(
            telegram_chat_id=-100999, title="C", chat_type=ChatType.supergroup,
            summary_interval_minutes=1, min_messages_before_digest=2,
            max_messages_before_digest=3,
        )
        await DigestRepository(session).create_run(chat_id=chat.id, trigger=RunTrigger.scheduled)
        return chat.id


async def _add_messages(database, cid, ids, now):
    async with database.session() as session:
        await MessageRepository(session).insert_many([
            dict(chat_id=cid, telegram_message_id=i, date=now + timedelta(seconds=i),
                 text=f"m{i}", is_service=False)
            for i in ids
        ])


async def test_scheduler_lifecycle(database, now):
    cid = await _make_chat(database)
    service = FakeService()
    scheduler = DigestScheduler(database, service)  # type: ignore[arg-type]
    await scheduler.start()
    try:
        assert scheduler._scheduler.running
        assert scheduler._scheduler.get_job(f"time:{cid}") is not None

        # stale run was reaped to failed on start
        async with database.session() as session:
            runs = await DigestRepository(session).recent_runs(cid)
            assert runs[0].status == RunStatus.failed

        # below min -> skip
        await scheduler._run_scheduled(cid)
        assert service.runs == []

        # reach min -> run
        await _add_messages(database, cid, (1, 2), now)
        await scheduler._run_scheduled(cid)
        assert service.runs == [(cid, "scheduled")]

        # count trigger fires at max
        await _add_messages(database, cid, (3,), now)
        await scheduler.on_message_stored(cid)
        await asyncio.sleep(0.1)
        assert (cid, "count") in service.runs

        # remove interval -> reconcile drops the job
        async with database.session() as session:
            await session.execute(
                update(Chat).where(Chat.id == cid).values(summary_interval_minutes=None)
            )
        await scheduler.reconcile()
        assert scheduler._scheduler.get_job(f"time:{cid}") is None
    finally:
        await scheduler.shutdown()
