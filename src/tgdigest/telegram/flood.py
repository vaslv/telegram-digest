"""FloodWait / transient-error handling for Telethon RPC calls."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from telethon.errors import FloodWaitError, ServerError, TimedOutError

from tgdigest.logging import get_logger

_log = get_logger("telegram.flood")


async def with_flood_retry[T](
    factory: Callable[[], Awaitable[T]],
    *,
    retries: int = 3,
    base_delay: float = 2.0,
    max_delay: float = 30.0,
) -> T:
    """Await ``factory()``; sleep through FloodWait and retry transient errors.

    ``factory`` must return a *fresh* awaitable on each call because a coroutine
    can only be awaited once.
    """
    attempt = 0
    while True:
        try:
            return await factory()
        except FloodWaitError as exc:
            delay = exc.seconds + 2
            _log.warning("telegram_flood_wait", seconds=exc.seconds, sleeping=delay)
            await asyncio.sleep(delay)
        except (ServerError, TimedOutError) as exc:
            attempt += 1
            if attempt > retries:
                raise
            delay = min(base_delay * 2 ** (attempt - 1), max_delay)
            _log.warning("telegram_transient_error", error=type(exc).__name__, attempt=attempt)
            await asyncio.sleep(delay)
