from __future__ import annotations

import json
import re
from datetime import timedelta

import pytest

from tgdigest.config.settings import Settings
from tgdigest.db.enums import ChatType, RunTrigger
from tgdigest.db.models import ChatState
from tgdigest.db.repositories import ChatRepository, MessageRepository, PromptRepository
from tgdigest.llm.base import LLMProvider, LLMResponse
from tgdigest.summarization import DigestService, seed_default_prompts

pytestmark = pytest.mark.integration


class FakeLLMProvider(LLMProvider):
    name = "fake"

    def __init__(self, settings):
        super().__init__(settings)
        self.calls: list[str] = []

    async def _request(self, messages, *, model, temperature, max_tokens, json_mode):
        system = messages[0].content
        if "ЭТАП 1" in system:
            self.calls.append("stage1")
            transcript = messages[1].content
            match = re.search(r"#(\d+)[^\n]*релиз", transcript) or re.search(r"#(\d+)", transcript)
            ref = int(match.group(1)) if match else 1
            payload = {
                "events": [
                    {"message_id": ref, "importance_type": "decision",
                     "summary": "Согласован перенос релиза на 14 июня",
                     "reason": "меняет план", "confidence": 0.9, "related_message_ids": []},
                    {"message_id": 10**9, "importance_type": "task",
                     "summary": "галлюцинация", "confidence": 0.95},
                ]
            }
        else:
            self.calls.append("stage2")
            payload = {
                "summary": "Главное: перенос релиза.",
                "key_events": ["Релиз перенесён на 14 июня"],
                "attention": ["Собрать сборку"], "open_questions": [],
                "links": [], "conclusion": "Следим за сроками.",
            }
        return LLMResponse(json.dumps(payload, ensure_ascii=False), model)


async def _setup_chat(database, now, *, with_messages=True):
    settings = Settings()
    async with database.session() as session:
        await seed_default_prompts(PromptRepository(session))
        chat = await ChatRepository(session).create_or_update(
            telegram_chat_id=-1001234567890, title="Команда",
            chat_type=ChatType.supergroup, importance_threshold=0.5,
        )
        cid = chat.id
    if with_messages:
        rows = [
            dict(chat_id=cid, telegram_message_id=1, date=now, text="Всем привет", is_service=False),
            dict(chat_id=cid, telegram_message_id=2, date=now + timedelta(minutes=1), text="ок", is_service=False),
            dict(chat_id=cid, telegram_message_id=3, date=now + timedelta(minutes=2), text="ок", is_service=False),
            dict(chat_id=cid, telegram_message_id=4, date=now + timedelta(minutes=3),
                 text="Предлагаю перенести релиз на 14 июня после тестов", sender_name="Ann", is_service=False),
            dict(chat_id=cid, telegram_message_id=5, date=now + timedelta(minutes=3, seconds=20),
                 text="Подготовьте сборку", sender_name="Ann", is_service=False),
            dict(chat_id=cid, telegram_message_id=6, date=now + timedelta(minutes=4), text="+", is_service=False),
        ]
        async with database.session() as session:
            await MessageRepository(session).insert_many(rows)
    return settings, cid


async def test_full_run_success(database, now):
    settings, cid = await _setup_chat(database, now)
    provider = FakeLLMProvider(settings.llm)
    service = DigestService(database, provider, settings, client=None)

    outcome = await service.run(cid, trigger=RunTrigger.manual)
    assert outcome.status == "success"
    assert outcome.important_count == 1  # hallucinated id dropped
    assert provider.calls == ["stage1", "stage2"]
    assert "https://t.me/c/1234567890/4" in outcome.body_markdown  # merged decision block ref

    async with database.session() as session:
        state = await session.get(ChatState, cid)
        assert state.last_processed_message_id == 6
    await provider.aclose()


async def test_dry_run_does_not_advance_state(database, now):
    settings, cid = await _setup_chat(database, now)
    provider = FakeLLMProvider(settings.llm)
    service = DigestService(database, provider, settings, client=None)

    outcome = await service.run(cid, trigger=RunTrigger.manual, dry_run=True)
    assert outcome.status == "success"
    async with database.session() as session:
        state = await session.get(ChatState, cid)
        assert state.last_processed_message_id == 0
    await provider.aclose()


async def test_empty_when_no_messages(database, now):
    settings, cid = await _setup_chat(database, now, with_messages=False)
    provider = FakeLLMProvider(settings.llm)
    service = DigestService(database, provider, settings, client=None)

    outcome = await service.run(cid, trigger=RunTrigger.scheduled)
    assert outcome.status == "empty"
    assert provider.calls == []
    await provider.aclose()
