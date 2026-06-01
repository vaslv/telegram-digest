from __future__ import annotations

import pytest

from tgdigest.db.enums import ChatType, ImportanceType, PromptScope, RunStatus, RunTrigger
from tgdigest.db.repositories import (
    AnalysisRepository,
    ChatRepository,
    DigestRepository,
    MessageRepository,
    PromptRepository,
)

pytestmark = pytest.mark.integration


async def test_chat_create_and_state(database, now):
    async with database.session() as session:
        repo = ChatRepository(session)
        chat = await repo.create_or_update(
            telegram_chat_id=-100777, title="Team", chat_type=ChatType.supergroup,
            summary_interval_minutes=60,
        )
        assert chat.id is not None
        state = await repo.get_state(chat.id)
        assert state is not None and state.last_seen_message_id == 0
        # update path keeps identity
        again = await repo.create_or_update(
            telegram_chat_id=-100777, title="Team v2", chat_type=ChatType.supergroup,
        )
        assert again.id == chat.id and again.title == "Team v2"


async def test_message_dedup_and_queries(database, now):
    async with database.session() as session:
        chat = await ChatRepository(session).create_or_update(
            telegram_chat_id=-100888, title="X", chat_type=ChatType.group,
        )
        cid = chat.id
    rows = [
        dict(chat_id=cid, telegram_message_id=i, date=now, text=f"m{i}", is_service=False)
        for i in (1, 2, 3)
    ]
    rows.append(dict(chat_id=cid, telegram_message_id=2, date=now, text="dup", is_service=False))
    async with database.session() as session:
        repo = MessageRepository(session)
        assert await repo.insert_many(rows) == 3  # duplicate skipped
        assert await repo.max_telegram_id(cid) == 3
        assert await repo.count_after(cid, 1) == 2
        await repo.apply_edit(cid, 1, text="edited")
        window = await repo.fetch_window(cid, after_message_id=0)
        assert [m.telegram_message_id for m in window] == [1, 2, 3]
        assert window[0].text == "edited"
        id_map = await repo.get_by_telegram_ids(cid, [2, 3])
        assert set(id_map) == {2, 3}


async def test_prompt_versioning_and_active(database):
    async with database.session() as session:
        repo = PromptRepository(session)
        assert await repo.seed_if_absent(PromptScope.global_system, "v1") is True
        assert await repo.seed_if_absent(PromptScope.global_system, "v1-again") is False
        new = await repo.set_active(PromptScope.global_system, "v2")
        assert new.version == 2
        assert await repo.get_active_content(PromptScope.global_system) == "v2"


async def test_digest_run_and_events(database, now):
    async with database.session() as session:
        chat = await ChatRepository(session).create_or_update(
            telegram_chat_id=-100999, title="Z", chat_type=ChatType.supergroup,
        )
        cid = chat.id
        runs = DigestRepository(session)
        run = await runs.create_run(chat_id=cid, trigger=RunTrigger.manual)
        await AnalysisRepository(session).add_events(
            run_id=run.id, chat_id=cid,
            events=[
                dict(telegram_message_id=4, importance_type=ImportanceType.decision,
                     summary="A", reason="r", confidence=0.8, related_message_ids=[2]),
                dict(telegram_message_id=5, importance_type=ImportanceType.task,
                     summary="B", confidence=0.4, related_message_ids=[]),
            ],
        )
        events = await AnalysisRepository(session).events_for_run(run.id)
        assert [e.confidence for e in events] == [0.8, 0.4]  # ordered desc
        digest = await runs.save_digest(
            run_id=run.id, chat_id=cid, title="Z", period_start=now, period_end=now,
            summary="s", structured={"key_events": ["A"]}, body_markdown="# Z",
            is_empty=False, target_chat_id=None,
        )
        await runs.update_run(run.id, status=RunStatus.success, important_count=1)
        await runs.mark_sent(digest.id, target_chat_id=None, when=now)
        recent = await runs.recent_runs(cid)
        assert recent[0].status == RunStatus.success


async def test_reap_stale_runs(database):
    async with database.session() as session:
        chat = await ChatRepository(session).create_or_update(
            telegram_chat_id=-100111, title="S", chat_type=ChatType.group,
        )
        await DigestRepository(session).create_run(chat_id=chat.id, trigger=RunTrigger.scheduled)
    async with database.session() as session:
        assert await DigestRepository(session).reap_stale_runs() == 1
