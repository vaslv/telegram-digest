"""Composition root — builds shared infrastructure and wires services.

A deliberately small, explicit container (no DI framework). It owns the engine
and the LLM provider as singletons and constructs services on demand.
"""

from __future__ import annotations

from telethon import TelegramClient

from tgdigest.config.settings import Settings, get_settings
from tgdigest.db.base import Database
from tgdigest.llm.base import LLMProvider
from tgdigest.llm.factory import build_provider
from tgdigest.logging import configure_logging
from tgdigest.summarization.service import DigestService
from tgdigest.telegram.client import TelegramClientManager


class Container:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        configure_logging(self.settings.app)
        self.db = Database(self.settings.db.url)
        self._provider: LLMProvider | None = None

    @property
    def provider(self) -> LLMProvider:
        if self._provider is None:
            self._provider = build_provider(self.settings.llm)
        return self._provider

    def telegram_manager(self, *, in_memory: bool = False) -> TelegramClientManager:
        return TelegramClientManager(self.settings.telegram, in_memory=in_memory)

    def digest_service(self, client: TelegramClient | None = None) -> DigestService:
        return DigestService(self.db, self.provider, self.settings, client=client)

    async def aclose(self) -> None:
        if self._provider is not None:
            await self._provider.aclose()
        await self.db.dispose()
