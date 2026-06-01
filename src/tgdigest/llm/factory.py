"""Construct an :class:`LLMProvider` from settings."""

from __future__ import annotations

from tgdigest.config.settings import LLMProviderName, LLMSettings
from tgdigest.llm.base import LLMProvider
from tgdigest.llm.claude import ClaudeProvider
from tgdigest.llm.ollama import OllamaProvider
from tgdigest.llm.openai_compatible import OpenAICompatibleProvider

_PROVIDERS: dict[LLMProviderName, type[LLMProvider]] = {
    LLMProviderName.ollama: OllamaProvider,
    LLMProviderName.openai: OpenAICompatibleProvider,
    LLMProviderName.claude: ClaudeProvider,
}


def build_provider(settings: LLMSettings) -> LLMProvider:
    try:
        provider_cls = _PROVIDERS[settings.provider]
    except KeyError as exc:  # pragma: no cover - guarded by enum
        raise ValueError(f"unknown LLM provider: {settings.provider}") from exc
    return provider_cls(settings)
