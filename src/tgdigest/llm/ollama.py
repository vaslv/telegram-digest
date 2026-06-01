"""Ollama provider (native /api/chat). Local, free — the default backend."""

from __future__ import annotations

import time
from collections.abc import Sequence
from typing import ClassVar

from tgdigest.llm.base import LLMMessage, LLMProvider, LLMResponse, raise_for_llm_status


class OllamaProvider(LLMProvider):
    name: ClassVar[str] = "ollama"

    async def _request(
        self,
        messages: Sequence[LLMMessage],
        *,
        model: str,
        temperature: float,
        max_tokens: int,
        json_mode: bool,
    ) -> LLMResponse:
        url = f"{self._settings.base_url.rstrip('/')}/api/chat"
        payload: dict[str, object] = {
            "model": model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens},
            "keep_alive": "10m",
        }
        if json_mode:
            payload["format"] = "json"

        started = time.perf_counter()
        response = await self._http.post(url, json=payload)
        raise_for_llm_status(response)
        data = response.json()
        return LLMResponse(
            text=data.get("message", {}).get("content", ""),
            model=model,
            prompt_tokens=data.get("prompt_eval_count"),
            completion_tokens=data.get("eval_count"),
            latency_s=time.perf_counter() - started,
            raw=data,
        )
