from __future__ import annotations

from tgdigest.db.enums import ChatType, ImportanceType
from tgdigest.db.models import Chat
from tgdigest.summarization.render import esc, message_link, render_digest, render_empty
from tgdigest.summarization.schemas import DigestContent
from tgdigest.summarization.stage1_importance import DetectedEvent


def _event(mid=4, summary="Решение по релизу", conf=0.9):
    return DetectedEvent(mid, ImportanceType.decision, summary, "reason", conf, [])


def test_message_link_variants():
    public = Chat(telegram_chat_id=123, title="t", chat_type=ChatType.channel, username="news")
    private = Chat(telegram_chat_id=-1001234567890, title="t", chat_type=ChatType.supergroup)
    basic = Chat(telegram_chat_id=-4567, title="t", chat_type=ChatType.group)
    assert message_link(public, 10) == "https://t.me/news/10"
    assert message_link(private, 10) == "https://t.me/c/1234567890/10"
    assert message_link(basic, 10) is None


def test_esc_escapes_markdown():
    assert esc("a*b_c[d]") == r"a\*b\_c\[d\]"


def test_render_digest_sections(sample_chat):
    content = DigestContent(
        summary="Кратко о важном",
        key_events=["Релиз 14 июня"],
        attention=["Собрать сборку"],
        open_questions=[],
        links=["https://wiki/doc"],
        conclusion="Следим",
    )
    body = render_digest(sample_chat, "01.06 12:00–13:00", content, [_event()])
    assert "Дайджест — Команда" in body
    assert "🔑 Ключевые события" in body and "Релиз 14 июня" in body
    assert "Вопросы без ответа" not in body  # empty section omitted
    assert "https://t.me/c/1234567890/4" in body  # message permalink
    assert body.rstrip().endswith("Следим")


def test_render_empty(sample_chat):
    body = render_empty(sample_chat, "01.06 12:00–13:00")
    assert "значимых событий не обнаружено" in body
