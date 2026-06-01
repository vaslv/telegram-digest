from __future__ import annotations

import json

import httpx
import pytest
import respx

from tgdigest.config.settings import LLMProviderName, LLMSettings
from tgdigest.llm import build_provider
from tgdigest.llm.base import LLMMessage

_MSGS = [LLMMessage("system", "rules"), LLMMessage("user", "analyze")]


def _settings(provider, base_url, **kw):
    return LLMSettings(
        provider=provider, base_url=base_url, max_retries=3,
        request_timeout=5, temperature=0.2, max_tokens=128, **kw,
    )


@respx.mock
async def test_ollama_request_and_parse():
    route = respx.post("http://ollama:11434/api/chat").mock(
        return_value=httpx.Response(
            200, json={"message": {"content": '{"events": []}'},
            "prompt_eval_count": 11, "eval_count": 7},
        )
    )
    provider = build_provider(_settings(LLMProviderName.ollama, "http://ollama:11434", model="llama3.1"))
    response = await provider.complete(_MSGS, json_mode=True)
    body = json.loads(route.calls.last.request.read())
    assert body["format"] == "json" and body["stream"] is False
    assert response.text == '{"events": []}'
    assert response.prompt_tokens == 11 and response.completion_tokens == 7
    await provider.aclose()


@respx.mock
async def test_openai_compatible_auth_and_json_mode():
    route = respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=httpx.Response(
            200, json={"model": "gpt-4o-mini",
                       "choices": [{"message": {"content": '{"ok": 1}'}}],
                       "usage": {"prompt_tokens": 3, "completion_tokens": 2}},
        )
    )
    provider = build_provider(
        _settings(LLMProviderName.openai, "https://api.openai.com/v1", model="gpt-4o-mini", api_key="sk-x")
    )
    response = await provider.complete(_MSGS, json_mode=True)
    request = route.calls.last.request
    assert request.headers["authorization"] == "Bearer sk-x"
    assert json.loads(request.read())["response_format"] == {"type": "json_object"}
    assert response.text == '{"ok": 1}' and response.completion_tokens == 2
    await provider.aclose()


@respx.mock
async def test_claude_prefill_yields_valid_json():
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(
            200, json={"model": "claude-haiku-4-5-20251001",
                       "content": [{"type": "text", "text": '"events": []}'}],
                       "usage": {"input_tokens": 9, "output_tokens": 4}},
        )
    )
    provider = build_provider(
        _settings(LLMProviderName.claude, "https://api.anthropic.com",
                  model="claude-haiku-4-5-20251001", api_key="sk-ant")
    )
    response = await provider.complete(_MSGS, json_mode=True)
    assert response.text == '{"events": []}'  # prefilled "{" prepended
    await provider.aclose()


@respx.mock
async def test_retry_on_transient_5xx():
    respx.post("http://ollama:11434/api/chat").mock(
        side_effect=[
            httpx.Response(503, text="overloaded"),
            httpx.Response(200, json={"message": {"content": "ok"}}),
        ]
    )
    provider = build_provider(_settings(LLMProviderName.ollama, "http://ollama:11434"))
    response = await provider.complete(_MSGS)
    assert response.text == "ok"
    await provider.aclose()


@respx.mock
async def test_terminal_4xx_raises():
    from tgdigest.llm.errors import LLMError

    respx.post("http://ollama:11434/api/chat").mock(return_value=httpx.Response(400, text="bad"))
    provider = build_provider(_settings(LLMProviderName.ollama, "http://ollama:11434"))
    with pytest.raises(LLMError):
        await provider.complete(_MSGS)
    await provider.aclose()
