"""Stage 2 — compose the digest from the important events."""

from __future__ import annotations

from dataclasses import dataclass

from tgdigest.config.settings import LLMSettings
from tgdigest.db.models import Chat
from tgdigest.llm.base import LLMProvider
from tgdigest.llm.errors import LLMError
from tgdigest.llm.json_utils import JSONExtractionError
from tgdigest.logging import get_logger
from tgdigest.summarization.jsonio import complete_json, sum_tokens
from tgdigest.summarization.prompts import PromptBuilder
from tgdigest.summarization.schemas import DigestContent
from tgdigest.summarization.stage1_importance import DetectedEvent

_log = get_logger("summarization.stage2")
_SNIPPET = 200


@dataclass(slots=True)
class Stage2Result:
    content: DigestContent
    prompt_tokens: int
    completion_tokens: int
    ok: bool


class DigestComposer:
    def __init__(
        self, provider: LLMProvider, builder: PromptBuilder, settings: LLMSettings
    ) -> None:
        self._provider = provider
        self._builder = builder
        self._settings = settings

    async def compose(
        self,
        chat: Chat,
        events: list[DetectedEvent],
        text_by_ref: dict[int, str],
        period: str,
    ) -> Stage2Result:
        events_block = _serialize_events(events, text_by_ref)
        messages = self._builder.stage2_messages(chat, events_block, period)
        model = self._settings.model_for(2)
        try:
            content, responses = await complete_json(
                self._provider,
                messages,
                DigestContent,
                model=model,
                max_tokens=self._settings.max_tokens,
            )
        except (JSONExtractionError, LLMError) as exc:
            _log.warning("stage2_failed", chat_id=chat.id, error=str(exc))
            return Stage2Result(DigestContent(), 0, 0, False)

        prompt_tokens, completion_tokens = sum_tokens(responses)
        return Stage2Result(content, prompt_tokens, completion_tokens, True)


def _serialize_events(events: list[DetectedEvent], text_by_ref: dict[int, str]) -> str:
    lines: list[str] = []
    for event in events:
        snippet = (text_by_ref.get(event.telegram_message_id, "") or "").strip()[:_SNIPPET]
        related = ""
        if event.related_message_ids:
            joined = ", ".join(f"#{r}" for r in event.related_message_ids)
            related = f" | связанные: {joined}"
        lines.append(
            f"- [{event.importance_type.value}] {event.summary} "
            f"| причина: {event.reason or '—'} "
            f"| источник #{event.telegram_message_id}: {snippet}{related}"
        )
    return "\n".join(lines)
