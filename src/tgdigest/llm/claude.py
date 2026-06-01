"""Anthropic Claude provider (/v1/messages).

Claude has no native JSON mode, so JSON output is coerced by prefilling the
assistant turn with ``{`` (and prepending it back to the completion). All
stage prompts therefore return JSON *objects*, never bare arrays.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from typing import ClassVar

from tgdigest.llm.base import LLMMessage, LLMProvider, LLMResponse, raise_for_llm_status

_ANTHROPIC_VERSION = "2023-06-01"


class ClaudeProvider(LLMProvider):
    name: ClassVar[str] = "claude"

    async def _request(
        self,
        messages: Sequence[LLMMessage],
        *,
        model: str,
        temperature: float,
        max_tokens: int,
        json_mode: bool,
    ) -> LLMResponse:
        url = f"{self._settings.base_url.rstrip('/')}/v1/messages"
        headers = {
            "x-api-key": self._settings.api_key or "",
            "anthropic-version": _ANTHROPIC_VERSION,
            "content-type": "application/json",
        }

        system = "\n\n".join(m.content for m in messages if m.role == "system")
        chat = [
            {"role": m.role, "content": m.content} for m in messages if m.role != "system"
        ]
        prefilled = False
        if json_mode:
            chat.append({"role": "assistant", "content": "{"})
            prefilled = True

        payload: dict[str, object] = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": chat,
        }
        if system:
            payload["system"] = system

        started = time.perf_counter()
        response = await self._http.post(url, json=payload, headers=headers)
        raise_for_llm_status(response)
        data = response.json()
        text = "".join(
            block.get("text", "")
            for block in data.get("content", [])
            if block.get("type") == "text"
        )
        if prefilled:
            text = "{" + text
        usage = data.get("usage") or {}
        return LLMResponse(
            text=text,
            model=data.get("model", model),
            prompt_tokens=usage.get("input_tokens"),
            completion_tokens=usage.get("output_tokens"),
            latency_s=time.perf_counter() - started,
            raw=data,
        )
