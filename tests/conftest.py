"""Shared pytest fixtures.

Pure unit tests need nothing here. Integration tests use the ``database``
fixture, which connects to ``TEST_DATABASE_URL`` and is skipped when that
variable is unset (so the default ``pytest`` run stays dependency-free).
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import text

from tgdigest.db import models  # noqa: F401  (populate metadata)
from tgdigest.db.base import Base, Database
from tgdigest.db.enums import ChatType
from tgdigest.db.models import Chat

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

_TABLES = (
    "chats",
    "chat_states",
    "messages",
    "digest_runs",
    "digests",
    "importance_events",
    "prompts",
    "processing_errors",
)


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


@pytest.fixture
def sample_chat() -> Chat:
    """A detached ORM Chat usable by pure tests (no session required)."""
    return Chat(
        id=1,
        telegram_chat_id=-1001234567890,
        title="Команда",
        chat_type=ChatType.supergroup,
        username=None,
        enabled=True,
        importance_threshold=0.5,
        min_messages_before_digest=10,
        max_messages_before_digest=300,
        summary_interval_minutes=60,
        send_empty_digest=False,
        chat_context_prompt=None,
        chat_summary_prompt=None,
    )


@pytest_asyncio.fixture
async def database() -> Database:
    if not TEST_DATABASE_URL:
        pytest.skip("set TEST_DATABASE_URL to run database integration tests")
    db = Database(TEST_DATABASE_URL)
    async with db.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with db.session() as session:
        await session.execute(text(f"TRUNCATE {', '.join(_TABLES)} RESTART IDENTITY CASCADE"))
    yield db
    await db.dispose()
