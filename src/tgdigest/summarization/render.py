"""Render a :class:`DigestContent` into Telegram-markdown text.

Section titles use bold (Telegram has no headings). Dynamic text from the LLM
and chat metadata is markdown-escaped to avoid malformed entities. Message
permalinks are generated deterministically from the chat + message id, never
trusted from the model.
"""

from __future__ import annotations

import re

from tgdigest.db.models import Chat
from tgdigest.summarization.schemas import DigestContent
from tgdigest.summarization.stage1_importance import DetectedEvent

_MD_SPECIAL = re.compile(r"([_*\[\]`])")
_MAX_MESSAGE_LINKS = 15
_LABEL_LEN = 70
_EMPTY_TEXT = "За указанный период значимых событий не обнаружено."


def esc(text: str) -> str:
    return _MD_SPECIAL.sub(r"\\\1", text)


def message_link(chat: Chat, message_id: int) -> str | None:
    if chat.username:
        return f"https://t.me/{chat.username}/{message_id}"
    marked = str(chat.telegram_chat_id)
    if marked.startswith("-100"):
        return f"https://t.me/c/{marked[4:]}/{message_id}"
    return None  # basic group / private chat — no public permalink


def _truncate(text: str, limit: int) -> str:
    text = text.strip().replace("\n", " ")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def render_empty(chat: Chat, period: str) -> str:
    return f"**📋 Дайджест — {esc(chat.title)}**\n_{esc(period)}_\n\n{_EMPTY_TEXT}"


def render_digest(
    chat: Chat, period: str, content: DigestContent, events: list[DetectedEvent]
) -> str:
    lines: list[str] = [f"**📋 Дайджест — {esc(chat.title)}**", f"_{esc(period)}_"]

    if content.summary:
        lines += ["", esc(content.summary)]

    def section(title: str, items: list[str]) -> None:
        if items:
            lines.append("")
            lines.append(f"**{title}**")
            lines.extend(f"• {esc(item)}" for item in items)

    section("🔑 Ключевые события", content.key_events)
    section("⚠️ Требует внимания", content.attention)
    section("❓ Вопросы без ответа", content.open_questions)
    section("🔗 Полезные ссылки", content.links)

    message_links = _message_links(chat, events)
    if message_links:
        lines.append("")
        lines.append("**📌 Ссылки на сообщения**")
        lines.extend(message_links)

    if content.conclusion:
        lines += ["", f"**Вывод:** {esc(content.conclusion)}"]

    return "\n".join(lines)


def _message_links(chat: Chat, events: list[DetectedEvent]) -> list[str]:
    out: list[str] = []
    seen: set[int] = set()
    for event in sorted(events, key=lambda e: e.confidence, reverse=True):
        if event.telegram_message_id in seen:
            continue
        seen.add(event.telegram_message_id)
        label = esc(_truncate(event.summary, _LABEL_LEN))
        url = message_link(chat, event.telegram_message_id)
        out.append(f"• [{label}]({url})" if url else f"• {label} (#{event.telegram_message_id})")
        if len(out) >= _MAX_MESSAGE_LINKS:
            break
    return out
