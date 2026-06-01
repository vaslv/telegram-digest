"""Retry/backoff loop for LLM requests."""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from tgdigest.llm.errors import RetryableLLMError

_MAX_BACKOFF = 20.0


async def call_with_retry[T](
    factory: Callable[[], Awaitable[T]],
    *,
    max_retries: int,
    logger: Any | None = None,
) -> T:
    """Call ``factory`` until it succeeds or attempts are exhausted.

    Retries on :class:`RetryableLLMError` and ``httpx`` transport errors,
    honouring ``retry_after`` when provided. Terminal errors propagate
    immediately.
    """
    attempt = 0
    while True:
        try:
            return await factory()
        except (RetryableLLMError, httpx.TransportError) as exc:
            attempt += 1
            if attempt > max_retries:
                raise
            retry_after = getattr(exc, "retry_after", None)
            delay = retry_after if retry_after is not None else _backoff(attempt)
            if logger is not None:
                logger.warning(
                    "llm_retry",
                    attempt=attempt,
                    max_retries=max_retries,
                    delay=round(delay, 2),
                    error=type(exc).__name__,
                )
            await asyncio.sleep(delay)


def _backoff(attempt: int) -> float:
    return min(2.0 ** (attempt - 1), _MAX_BACKOFF) + random.uniform(0, 0.5)
