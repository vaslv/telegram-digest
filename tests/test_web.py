from __future__ import annotations

import asyncio
import os

import pytest
from sqlalchemy import text

from tgdigest.config.settings import Settings, get_settings
from tgdigest.container import Container
from tgdigest.db.base import Database
from tgdigest.db.enums import ChatType
from tgdigest.db.repositories import DialogRepository

pytestmark = pytest.mark.integration
_URL = os.environ.get("TEST_DATABASE_URL")


async def _setup(url: str) -> None:
    db = Database(url)
    async with db.session() as session:
        await session.execute(
            text(
                "TRUNCATE chats, chat_states, dialogs, digest_requests, digest_runs, "
                "digests, prompts, processing_errors RESTART IDENTITY CASCADE"
            )
        )
        await DialogRepository(session).upsert_many([
            dict(telegram_chat_id=-1001160545779, title="Тестовая Команда",
                 chat_type=ChatType.supergroup, username="teamx", is_member=True)
        ])
    await db.dispose()


def test_web_flow():
    if not _URL:
        pytest.skip("set TEST_DATABASE_URL to run web integration tests")
    os.environ["DATABASE_URL"] = _URL
    os.environ["WEB_PASSWORD"] = "secret"
    os.environ["WEB_SECRET_KEY"] = "test-secret"
    get_settings.cache_clear()
    asyncio.run(_setup(_URL))

    from fastapi.testclient import TestClient

    from tgdigest.web.app import create_app

    app = create_app(Container(Settings()))
    with TestClient(app, follow_redirects=False) as client:
        assert client.get("/").status_code == 307  # auth gate → /login
        assert client.get("/healthz").json()["status"] == "ok"
        assert client.post("/login", data={"password": "wrong"}).status_code == 200
        assert client.post("/login", data={"password": "secret"}).status_code == 303
        assert "Чаты под мониторингом" in client.get("/").text
        assert "teamx" in client.get("/dialogs").text

        added = client.post("/chats", data={"telegram_chat_id": "-1001160545779"})
        assert added.status_code == 303
        url = added.headers["location"]
        chat_id = int(url.rsplit("/", 1)[1])
        assert "Тестовая Команда" in client.get(url).text

        saved = client.post(
            f"/chats/{chat_id}",
            data={"interval": "720", "min_messages": "5", "max_messages": "500",
                  "threshold": "0.6", "target": "", "send_empty": "true",
                  "context_prompt": "важно", "summary_prompt": ""},
        )
        assert saved.status_code == 303
        detail = client.get(url).text
        assert 'value="720"' in detail and "важно" in detail and "checked" in detail

        client.post(f"/chats/{chat_id}/run", data={})
        client.post(f"/chats/{chat_id}/run", data={"dry": "true"})
        assert "В очереди: 2" in client.get("/").text

        for path in ("/digests", "/runs", "/errors"):
            assert client.get(path).status_code == 200

        assert client.post(f"/chats/{chat_id}/delete").status_code == 303
        assert client.get("/logout").status_code == 303
        assert client.get("/").status_code == 307
