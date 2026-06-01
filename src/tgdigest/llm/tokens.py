"""Token estimation, context-window registry and budget-based chunking."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from functools import lru_cache
from math import ceil

DEFAULT_CONTEXT = 8192

# Approximate context windows. Keys are matched by longest prefix, so
# "llama3.1:8b" resolves via "llama3.1".
_MODEL_CONTEXT: dict[str, int] = {
    # OpenAI family
    "gpt-4o": 128_000,
    "gpt-4.1": 128_000,
    "gpt-4-turbo": 128_000,
    "o1": 128_000,
    "o3": 128_000,
    "gpt-3.5": 16_385,
    # Anthropic
    "claude-": 200_000,
    # Common Ollama models
    "llama3.1": 128_000,
    "llama3.2": 128_000,
    "llama3": 8_192,
    "qwen2.5": 32_768,
    "qwen2": 32_768,
    "qwen3": 32_768,
    "mistral": 32_768,
    "mixtral": 32_768,
    "gemma2": 8_192,
    "gemma3": 128_000,
    "phi3": 128_000,
    "phi4": 16_384,
    "deepseek": 32_768,
    "command-r": 128_000,
}

# Heuristic chars-per-token. Russian text is denser than English; ~3.3 is a
# conservative middle ground that errs toward over-estimating (safer chunks).
_CHARS_PER_TOKEN = 3.3


def context_for(model: str, override: int | None = None) -> int:
    if override:
        return override
    name = model.lower()
    best_key = ""
    for key in _MODEL_CONTEXT:
        if name.startswith(key) and len(key) > len(best_key):
            best_key = key
    return _MODEL_CONTEXT[best_key] if best_key else DEFAULT_CONTEXT


@lru_cache(maxsize=8)
def _tiktoken_encoding(model: str) -> object | None:
    if not model.lower().startswith(("gpt-", "o1", "o3", "text-")):
        return None
    try:
        import tiktoken
    except ImportError:
        return None
    try:
        return tiktoken.encoding_for_model(model)
    except KeyError:
        return tiktoken.get_encoding("cl100k_base")


def estimate_tokens(text: str, model: str | None = None) -> int:
    if not text:
        return 0
    if model:
        enc = _tiktoken_encoding(model)
        if enc is not None:
            return len(enc.encode(text))  # type: ignore[attr-defined]
    return max(1, ceil(len(text) / _CHARS_PER_TOKEN))


def chunk_by_budget[T](
    items: Sequence[T],
    *,
    budget: int,
    size_of: Callable[[T], int],
) -> list[list[T]]:
    """Greedily pack items into chunks whose total size stays within budget.

    An item larger than the budget is placed alone (never dropped).
    """
    chunks: list[list[T]] = []
    current: list[T] = []
    current_size = 0
    for item in items:
        size = size_of(item)
        if current and current_size + size > budget:
            chunks.append(current)
            current = []
            current_size = 0
        current.append(item)
        current_size += size
    if current:
        chunks.append(current)
    return chunks
