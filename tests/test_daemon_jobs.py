from __future__ import annotations

import pytest

from tgdigest.db.enums import ChatType, RequestStatus
from tgdigest.db.repositories import ChatRepository, DialogRepository, DigestRequestRepository
from tgdigest.scheduling.scheduler import DigestScheduler

pytestmark = pytest.mark.integration


class _Outcome:
    def __init__(self) -> None:
        self.run_id = None
        self.status = "success"


class FakeService:
    def __init__(self) -> None:
        self.runs: list[tuple[int, bool]] = []

    async def run(self, chat_id, *, trigger, dry_run=False, send=True, **_kw):
        self.runs.append((chat_id, dry_run))
        return _Outcome()


async def test_poll_requests_runs_and_marks_done(database):
    async with database.session() as session:
        chat = await ChatRepository(session).create_or_update(
            telegram_chat_id=-100111, title="X", chat_type=ChatType.supergroup
        )
        cid = chat.id
        await DigestRequestRepository(session).enqueue(cid, dry_run=True)

    service = FakeService()
    scheduler = DigestScheduler(database, service)  # type: ignore[arg-type]
    await scheduler._poll_requests()

    assert service.runs == [(cid, True)]
    async with database.session() as session:
        recent = await DigestRequestRepository(session).recent()
        assert recent[0].status == RequestStatus.done


async def test_backfill_titles_from_dialog_cache(database):
    async with database.session() as session:
        placeholder = await ChatRepository(session).create_or_update(
            telegram_chat_id=-100777, title="-100777", chat_type=ChatType.group
        )
        await DialogRepository(session).upsert_many([
            dict(telegram_chat_id=-100777, title="Реальное имя", chat_type=ChatType.supergroup, username="real", is_member=True)
        ])
        pid = placeholder.id

    scheduler = DigestScheduler(database, FakeService())  # type: ignore[arg-type]
    async with database.session() as session:
        chats = await ChatRepository(session).get_enabled()
    await scheduler._backfill_titles(chats)

    async with database.session() as session:
        updated = await ChatRepository(session).get_by_id(pid)
        assert updated.title == "Реальное имя" and updated.chat_type == ChatType.supergroup
        assert updated.username == "real"
