from __future__ import annotations

import asyncio
import os

import pytest
from sqlalchemy import text

from tgdigest.config.settings import get_settings
from tgdigest.db.base import Database
from tgdigest.db.enums import ChatType
from tgdigest.db.repositories import DialogRepository, DigestRequestRepository

pytestmark = pytest.mark.integration
_URL = os.environ.get("TEST_DATABASE_URL")


async def _setup(url: str) -> None:
    db = Database(url)
    async with db.session() as session:
        await session.execute(
            text(
                "TRUNCATE chats, chat_states, dialogs, digest_requests, prompts, "
                "digest_runs RESTART IDENTITY CASCADE"
            )
        )
        await DialogRepository(session).upsert_many([
            dict(telegram_chat_id=-1001160545779, title="Тестовая Команда",
                 chat_type=ChatType.supergroup, username="teamx", is_member=True)
        ])
    await db.dispose()


async def _pending(url: str) -> int:
    db = Database(url)
    async with db.session() as session:
        count = await DigestRequestRepository(session).pending_count()
    await db.dispose()
    return count


def test_cli_offline_flow():
    if not _URL:
        pytest.skip("set TEST_DATABASE_URL to run CLI integration tests")
    os.environ["DATABASE_URL"] = _URL
    get_settings.cache_clear()
    asyncio.run(_setup(_URL))

    from typer.testing import CliRunner

    from tgdigest.cli.main import app

    runner = CliRunner()

    added = runner.invoke(app, ["watch-chat", "--interval", "720", "--", "-1001160545779"])
    assert added.exit_code == 0 and "Тестовая Команда" in added.output, added.output

    assert "teamx" in runner.invoke(app, ["list-dialogs"]).output
    assert "Тестовая Команда" in runner.invoke(app, ["show-chat-config", "@teamx"]).output
    assert runner.invoke(app, ["set-chat-prompt", "@teamx", "--context", "важно"]).exit_code == 0

    queued = runner.invoke(app, ["run-digest", "@teamx"])
    assert queued.exit_code == 0 and "очереди" in queued.output
    assert asyncio.run(_pending(_URL)) == 1

    assert "отключён" in runner.invoke(app, ["unwatch-chat", "@teamx"]).output
