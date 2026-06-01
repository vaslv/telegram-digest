from __future__ import annotations

import pytest

from tgdigest.db.enums import ChatType, RequestStatus
from tgdigest.db.repositories import ChatRepository, DialogRepository, DigestRequestRepository

pytestmark = pytest.mark.integration


async def test_dialog_upsert_and_lookup(database):
    async with database.session() as session:
        repo = DialogRepository(session)
        assert await repo.upsert_many([
            dict(telegram_chat_id=-100111, title="Команда", chat_type=ChatType.supergroup, username=None, is_member=True),
            dict(telegram_chat_id=-100222, title="Новости", chat_type=ChatType.channel, username="news", is_member=True),
        ]) == 2
        # update path
        await repo.upsert_many([
            dict(telegram_chat_id=-100111, title="Команда v2", chat_type=ChatType.supergroup, username=None, is_member=True)
        ])
        assert (await repo.get(-100111)).title == "Команда v2"
        assert (await repo.get_by_username("news")).telegram_chat_id == -100222
        assert [d.telegram_chat_id for d in await repo.list(query="нов")] == [-100222]
        assert await repo.last_refresh() is not None


async def test_request_queue_lifecycle(database):
    async with database.session() as session:
        chat = await ChatRepository(session).create_or_update(
            telegram_chat_id=-100111, title="X", chat_type=ChatType.supergroup
        )
        cid = chat.id
    async with database.session() as session:
        repo = DigestRequestRepository(session)
        await repo.enqueue(cid, dry_run=False)
        assert await repo.pending_count() == 1
    async with database.session() as session:
        claimed = await DigestRequestRepository(session).claim_pending()
        assert len(claimed) == 1 and claimed[0].status == RequestStatus.running
        request_id = claimed[0].id
    async with database.session() as session:
        repo = DigestRequestRepository(session)
        assert await repo.pending_count() == 0
        await repo.mark_done(request_id, digest_run_id=None)
    async with database.session() as session:
        assert (await DigestRequestRepository(session).get(request_id)).status == RequestStatus.done


async def test_reap_running_requests(database):
    async with database.session() as session:
        chat = await ChatRepository(session).create_or_update(
            telegram_chat_id=-100333, title="Y", chat_type=ChatType.group
        )
        await DigestRequestRepository(session).enqueue(chat.id)
    async with database.session() as session:
        await DigestRequestRepository(session).claim_pending()
    async with database.session() as session:
        assert await DigestRequestRepository(session).reap_running() == 1
