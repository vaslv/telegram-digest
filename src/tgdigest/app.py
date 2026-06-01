"""Long-running daemon: Telegram ingest + digest scheduling.

Lifecycle: connect (authorized) → wire ingestor + scheduler → catch up missed
messages → start scheduler → wait for disconnect or a termination signal →
shut down cleanly.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal

from tgdigest.container import Container
from tgdigest.db.repositories import ChatRepository
from tgdigest.logging import get_logger
from tgdigest.scheduling.scheduler import DigestScheduler
from tgdigest.telegram.ingest import MessageIngestor

_log = get_logger("app")


class Daemon:
    def __init__(self, container: Container) -> None:
        self._c = container
        self._manager = container.telegram_manager()
        self._scheduler: DigestScheduler | None = None

    async def run(self) -> None:
        client = await self._manager.connect_authorized()
        service = self._c.digest_service(client=client)
        ingestor = MessageIngestor(client, self._c.db)
        scheduler = DigestScheduler(
            self._c.db,
            service,
            ingestor=ingestor,
            client=client,
            dialog_refresh_minutes=self._c.settings.web.dialog_refresh_minutes,
            request_poll_seconds=self._c.settings.web.request_poll_seconds,
        )
        ingestor.set_on_stored(scheduler.on_message_stored)
        ingestor.register_handlers()
        self._scheduler = scheduler

        async with self._c.db.session() as session:
            chats = await ChatRepository(session).get_enabled()
        ingestor.update_watched(chats)
        _log.info("daemon_catchup_start", chats=len(chats))
        await ingestor.catchup_all(chats)
        await scheduler.start()
        _log.info("daemon_running", chats=len(chats))

        await self._wait_for_stop(client)
        await self._shutdown()

    async def _wait_for_stop(self, client: object) -> None:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(NotImplementedError):
                loop.add_signal_handler(sig, stop.set)

        disconnected = asyncio.ensure_future(client.run_until_disconnected())  # type: ignore[attr-defined]
        stopper = asyncio.ensure_future(stop.wait())
        await asyncio.wait({disconnected, stopper}, return_when=asyncio.FIRST_COMPLETED)
        for task in (disconnected, stopper):
            task.cancel()

    async def _shutdown(self) -> None:
        _log.info("daemon_stopping")
        if self._scheduler is not None:
            await self._scheduler.shutdown()
        await self._manager.disconnect()


async def run_daemon(container: Container) -> None:
    await Daemon(container).run()
