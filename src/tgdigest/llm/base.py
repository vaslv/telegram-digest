"""LLM provider abstraction.

A provider takes a list of chat messages and returns an :class:`LLMResponse`.
Concrete providers implement :meth:`LLMProvider._request`; :meth:`complete`
wraps it with retry/backoff. Errors are split into retryable (429/5xx/timeouts)
and terminal so the retry layer knows what to do.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, ClassVar

import httpx

from tgdigest.config.settings import LLMSettings
from tgdigest.llm.errors import (
    LLMError,
    RetryableLLMError,
    raise_for_llm_status,
)
from tgdigest.llm.retry import call_with_retry
from tgdigest.llm.tokens import context_for, estimate_tokens
from tgdigest.logging import get_logger

__all__ = [
    "LLMError",
    "LLMMessage",
    "LLMProvider",
    "LLMResponse",
    "RetryableLLMError",
    "raise_for_llm_status",
]

_log = get_logger("llm")


@dataclass(slots=True)
class LLMMessage:
    role: str  # "system" | "user" | "assistant"
    content: str


@dataclass(slots=True)
class LLMResponse:
    text: str
    model: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    latency_s: float = 0.0
    raw: dict[str, Any] | None = None


class LLMProvider(ABC):
    name: ClassVar[str] = "base"

    def __init__(self, settings: LLMSettings) -> None:
        self._settings = settings
        self._http = httpx.AsyncClient(timeout=settings.request_timeout)

    @property
    def model(self) -> str:
        return self._settings.model

    @property
    def context_window(self) -> int:
        return context_for(self._settings.model, self._settings.context_window)

    def context_for(self, model: str | None = None) -> int:
        return context_for(model or self._settings.model, self._settings.context_window)

    def estimate_tokens(self, text: str, model: str | None = None) -> int:
        return estimate_tokens(text, model or self._settings.model)

    async def complete(
        self,
        messages: Sequence[LLMMessage],
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> LLMResponse:
        resolved_model = model or self._settings.model
        resolved_temp = self._settings.temperature if temperature is None else temperature
        resolved_max = max_tokens or self._settings.max_tokens

        async def _attempt() -> LLMResponse:
            return await self._request(
                messages,
                model=resolved_model,
                temperature=resolved_temp,
                max_tokens=resolved_max,
                json_mode=json_mode,
            )

        return await call_with_retry(
            _attempt, max_retries=self._settings.max_retries, logger=_log
        )

    @abstractmethod
    async def _request(
        self,
        messages: Sequence[LLMMessage],
        *,
        model: str,
        temperature: float,
        max_tokens: int,
        json_mode: bool,
    ) -> LLMResponse: ...

    async def aclose(self) -> None:
        await self._http.aclose()
