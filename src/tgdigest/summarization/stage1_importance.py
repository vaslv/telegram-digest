"""Stage 1 — detect significant messages and return structured events."""

from __future__ import annotations

from dataclasses import dataclass

from tgdigest.config.settings import LLMSettings
from tgdigest.db.enums import ImportanceType
from tgdigest.db.models import Chat
from tgdigest.llm.base import LLMProvider
from tgdigest.llm.errors import LLMError
from tgdigest.llm.json_utils import JSONExtractionError
from tgdigest.llm.tokens import chunk_by_budget
from tgdigest.logging import get_logger
from tgdigest.summarization.jsonio import complete_json, sum_tokens
from tgdigest.summarization.preprocess import PreprocessResult, serialize_blocks
from tgdigest.summarization.prompts import PromptBuilder
from tgdigest.summarization.schemas import Stage1Output

_log = get_logger("summarization.stage1")
_OUTPUT_RESERVE = 512


@dataclass(slots=True)
class DetectedEvent:
    telegram_message_id: int
    importance_type: ImportanceType
    summary: str
    reason: str | None
    confidence: float
    related_message_ids: list[int]


@dataclass(slots=True)
class Stage1Result:
    events: list[DetectedEvent]
    chunks: int
    prompt_tokens: int
    completion_tokens: int
    failures: int


class ImportanceDetector:
    def __init__(
        self, provider: LLMProvider, builder: PromptBuilder, settings: LLMSettings
    ) -> None:
        self._provider = provider
        self._builder = builder
        self._settings = settings

    async def detect(self, chat: Chat, pre: PreprocessResult) -> Stage1Result:
        if not pre.blocks:
            return Stage1Result([], 0, 0, 0, 0)

        model = self._settings.model_for(1)
        budget = self._block_budget(chat, model)
        chunks = chunk_by_budget(
            pre.blocks,
            budget=budget,
            size_of=lambda b: self._provider.estimate_tokens(serialize_blocks([b]), model),
        )

        collected: dict[tuple[int, str], DetectedEvent] = {}
        prompt_tokens = completion_tokens = failures = 0

        for chunk in chunks:
            messages = self._builder.stage1_messages(chat, serialize_blocks(chunk))
            try:
                output, responses = await complete_json(
                    self._provider,
                    messages,
                    Stage1Output,
                    model=model,
                    max_tokens=self._settings.max_tokens,
                )
            except (JSONExtractionError, LLMError) as exc:
                failures += 1
                _log.warning("stage1_chunk_failed", chat_id=chat.id, error=str(exc))
                continue

            pt, ct = sum_tokens(responses)
            prompt_tokens += pt
            completion_tokens += ct

            for raw in output.events:
                if raw.message_id not in pre.known_refs:
                    continue  # drop hallucinated references
                event = DetectedEvent(
                    telegram_message_id=raw.message_id,
                    importance_type=ImportanceType(raw.importance_type),
                    summary=raw.summary.strip(),
                    reason=(raw.reason or None),
                    confidence=raw.confidence,
                    related_message_ids=[
                        r for r in raw.related_message_ids if r in pre.known_refs
                    ],
                )
                key = (event.telegram_message_id, event.importance_type.value)
                existing = collected.get(key)
                if existing is None or event.confidence > existing.confidence:
                    collected[key] = event

        events = sorted(collected.values(), key=lambda e: e.confidence, reverse=True)
        return Stage1Result(events, len(chunks), prompt_tokens, completion_tokens, failures)

    def _block_budget(self, chat: Chat, model: str) -> int:
        overhead_messages = self._builder.stage1_messages(chat, "")
        system_tokens = self._provider.estimate_tokens(overhead_messages[0].content, model)
        reserve = self._settings.max_tokens + _OUTPUT_RESERVE
        return max(512, self._provider.context_for(model) - system_tokens - reserve)
