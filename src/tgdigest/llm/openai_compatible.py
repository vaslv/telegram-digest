"""OpenAI-compatible provider (/chat/completions).

Works with OpenAI, OpenRouter, LM Studio, vLLM, groq and Ollama's /v1 endpoint.
``LLM_BASE_URL`` must include the version suffix (e.g. ``.../v1``).
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from typing import ClassVar

from tgdigest.llm.base import LLMMessage, LLMProvider, LLMResponse, raise_for_llm_status


class OpenAICompatibleProvider(LLMProvider):
    name: ClassVar[str] = "openai"

    async def _request(
        self,
        messages: Sequence[LLMMessage],
        *,
        model: str,
        temperature: float,
        max_tokens: int,
        json_mode: bool,
    ) -> LLMResponse:
        url = f"{self._settings.base_url.rstrip('/')}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self._settings.api_key:
            headers["Authorization"] = f"Bearer {self._settings.api_key}"
        payload: dict[str, object] = {
            "model": model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        started = time.perf_counter()
        response = await self._http.post(url, json=payload, headers=headers)
        raise_for_llm_status(response)
        data = response.json()
        choice = (data.get("choices") or [{}])[0]
        usage = data.get("usage") or {}
        return LLMResponse(
            text=(choice.get("message") or {}).get("content", "") or "",
            model=data.get("model", model),
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            latency_s=time.perf_counter() - started,
            raw=data,
        )
