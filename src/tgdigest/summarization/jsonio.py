"""Call the LLM and parse a JSON schema, with a single bounded repair attempt."""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel

from tgdigest.llm.base import LLMMessage, LLMProvider, LLMResponse
from tgdigest.llm.json_utils import JSONExtractionError, parse_model

_REPAIR_INSTRUCTION = (
    "Твой предыдущий ответ не является корректным JSON. Верни ТОЛЬКО валидный "
    "JSON-объект нужной схемы, без markdown-обрамления и без пояснений."
)


async def complete_json[ModelT: BaseModel](
    provider: LLMProvider,
    messages: Sequence[LLMMessage],
    schema: type[ModelT],
    *,
    model: str,
    max_tokens: int,
) -> tuple[ModelT, list[LLMResponse]]:
    """Return a validated model plus every LLMResponse made (for token accounting).

    Raises :class:`JSONExtractionError` if the repair attempt also fails.
    """
    responses: list[LLMResponse] = []
    first = await provider.complete(messages, model=model, max_tokens=max_tokens, json_mode=True)
    responses.append(first)
    try:
        return parse_model(first.text, schema), responses
    except JSONExtractionError:
        repair = [
            *messages,
            LLMMessage("assistant", first.text[:1500]),
            LLMMessage("user", _REPAIR_INSTRUCTION),
        ]
        second = await provider.complete(repair, model=model, max_tokens=max_tokens, json_mode=True)
        responses.append(second)
        return parse_model(second.text, schema), responses


def sum_tokens(responses: Sequence[LLMResponse]) -> tuple[int, int]:
    prompt = sum(r.prompt_tokens or 0 for r in responses)
    completion = sum(r.completion_tokens or 0 for r in responses)
    return prompt, completion
