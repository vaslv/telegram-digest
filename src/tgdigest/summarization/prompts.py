"""Multi-level prompt assembly.

Final prompts are composed automatically from four versioned global layers
(system / digest / stage instructions) plus per-chat context and instructions.
Active global versions come from the database, falling back to the packaged
defaults under ``tgdigest/prompts``.
"""

from __future__ import annotations

import hashlib
from functools import lru_cache
from importlib.resources import files

from tgdigest.db.enums import PromptScope
from tgdigest.db.models import Chat
from tgdigest.db.repositories import PromptRepository
from tgdigest.llm.base import LLMMessage

_PROMPT_FILES: dict[PromptScope, str] = {
    PromptScope.global_system: "global_system.md",
    PromptScope.global_digest: "global_digest.md",
    PromptScope.stage1_instructions: "stage1_instructions.md",
    PromptScope.stage2_instructions: "stage2_instructions.md",
}

_LANGUAGE_NAMES = {"ru": "русском", "en": "английском"}


@lru_cache(maxsize=1)
def default_prompts() -> dict[PromptScope, str]:
    anchor = files("tgdigest")
    return {
        scope: anchor.joinpath("prompts", filename).read_text(encoding="utf-8").strip()
        for scope, filename in _PROMPT_FILES.items()
    }


def _short_hash(text: str | None) -> str | None:
    if not text:
        return None
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


class PromptBuilder:
    def __init__(self, contents: dict[PromptScope, str], language: str) -> None:
        self._contents = contents
        self._language = language

    def _language_note(self) -> str:
        if self._language == "auto":
            return "Отвечай на языке, преобладающем в чате."
        name = _LANGUAGE_NAMES.get(self._language, self._language)
        return f"Все текстовые поля ответа пиши на {name} языке."

    def _chat_context(self, chat: Chat) -> str:
        lines = [f"Чат: «{chat.title}» (тип: {chat.chat_type.value})."]
        if chat.username:
            lines.append(f"Username: @{chat.username}")
        if chat.chat_context_prompt:
            lines.append(chat.chat_context_prompt.strip())
        return "Контекст чата:\n" + "\n".join(lines)

    def stage1_messages(self, chat: Chat, transcript: str) -> list[LLMMessage]:
        system = "\n\n".join(
            [
                self._contents[PromptScope.global_system],
                self._contents[PromptScope.stage1_instructions],
                self._chat_context(chat),
                self._language_note(),
            ]
        )
        return [LLMMessage("system", system), LLMMessage("user", transcript)]

    def stage2_messages(self, chat: Chat, events_block: str, period: str) -> list[LLMMessage]:
        parts = [
            self._contents[PromptScope.global_digest],
            self._contents[PromptScope.stage2_instructions],
            self._chat_context(chat),
        ]
        if chat.chat_summary_prompt:
            parts.append(
                "Дополнительные инструкции для этого чата:\n"
                + chat.chat_summary_prompt.strip()
            )
        parts.append(self._language_note())
        system = "\n\n".join(parts)
        user = (
            f"Анализируемый период: {period}\n\n"
            f"Значимые события:\n{events_block}\n\n"
            "Сформируй JSON дайджеста по заданной схеме."
        )
        return [LLMMessage("system", system), LLMMessage("user", user)]

    def snapshot(self, chat: Chat) -> dict[str, str | None]:
        snap: dict[str, str | None] = {
            scope.value: _short_hash(self._contents[scope]) for scope in PromptScope
        }
        snap["chat_context"] = _short_hash(chat.chat_context_prompt)
        snap["chat_summary"] = _short_hash(chat.chat_summary_prompt)
        snap["language"] = self._language
        return snap


async def load_prompt_builder(prompt_repo: PromptRepository, language: str) -> PromptBuilder:
    defaults = default_prompts()
    contents: dict[PromptScope, str] = {}
    for scope in PromptScope:
        active = await prompt_repo.get_active_content(scope)
        contents[scope] = active or defaults[scope]
    return PromptBuilder(contents, language)


async def seed_default_prompts(prompt_repo: PromptRepository) -> int:
    seeded = 0
    for scope, content in default_prompts().items():
        if await prompt_repo.seed_if_absent(scope, content):
            seeded += 1
    return seeded
