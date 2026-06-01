"""LLM error types and HTTP status mapping (shared by base + retry)."""

from __future__ import annotations

import httpx


class LLMError(RuntimeError):
    """Terminal LLM failure (bad request, auth, unparseable, ...)."""


class RetryableLLMError(LLMError):
    """Transient failure that may succeed on retry (429 / 5xx / network)."""

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def raise_for_llm_status(response: httpx.Response) -> None:
    if response.is_success:
        return
    status = response.status_code
    body = response.text[:500]
    if status in (408, 409, 425, 429) or status >= 500:
        raise RetryableLLMError(
            f"HTTP {status}: {body}",
            retry_after=_parse_retry_after(response.headers.get("retry-after")),
        )
    raise LLMError(f"HTTP {status}: {body}")
