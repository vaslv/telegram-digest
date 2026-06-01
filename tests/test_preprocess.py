from __future__ import annotations

from datetime import timedelta

from tgdigest.config.settings import PreprocessSettings
from tgdigest.db.enums import MediaType
from tgdigest.db.models import Message
from tgdigest.summarization.preprocess import normalize, preprocess, serialize_blocks


def _msg(mid, text, when, sender="Ann", **kw):
    return Message(
        chat_id=1,
        telegram_message_id=mid,
        sender_name=sender,
        date=when,
        text=text,
        is_service=kw.pop("is_service", False),
        **kw,
    )


def settings() -> PreprocessSettings:
    return PreprocessSettings(
        merge_gap_seconds=90, min_meaningful_len=8, thread_gap_minutes=30, dedup_window=50
    )


def test_normalize_strips_punctuation_and_emoji():
    assert normalize("Ок!!!") == "ок"
    assert normalize("👍👍") == ""
    assert normalize("+1") == "1"


def test_trivial_and_service_filtered(now):
    msgs = [
        _msg(1, "ок", now),
        _msg(2, "👍", now + timedelta(seconds=1)),
        _msg(3, None, now + timedelta(seconds=2), is_service=True, service_action="Pin"),
        _msg(4, "Это содержательное сообщение про задачу", now + timedelta(seconds=3)),
    ]
    result = preprocess(msgs, settings())
    assert result.block_count == 1
    assert result.blocks[0].ref == 4
    assert result.known_refs == {4}


def test_dedup_counts_repeats(now):
    msgs = [
        _msg(1, "повторяющийся важный текст", now, sender="A"),
        _msg(2, "повторяющийся важный текст", now + timedelta(seconds=5), sender="B"),
        _msg(3, "повторяющийся важный текст!", now + timedelta(seconds=6), sender="C"),
    ]
    result = preprocess(msgs, settings())
    assert result.block_count == 1
    assert result.blocks[0].repeat_count == 3


def test_merge_consecutive_same_author(now):
    msgs = [
        _msg(1, "Первая часть мысли про релиз", now, sender="Ann"),
        _msg(2, "вторая часть той же мысли", now + timedelta(seconds=30), sender="Ann"),
        _msg(3, "ответ от другого участника подробный", now + timedelta(seconds=40), sender="Bob"),
    ]
    result = preprocess(msgs, settings())
    assert result.block_count == 2
    merged = result.blocks[0]
    assert merged.source_ids == [1, 2]
    assert "Первая часть" in merged.text and "вторая часть" in merged.text


def test_thread_segmentation_by_gap(now):
    msgs = [
        _msg(1, "сообщение в первом обсуждении", now, sender="A"),
        _msg(2, "сообщение спустя час во втором", now + timedelta(hours=1), sender="B"),
    ]
    result = preprocess(msgs, settings())
    assert [b.segment for b in result.blocks] == [0, 1]


def test_low_info_media_dropped_but_document_kept(now):
    msgs = [
        _msg(1, None, now, media_type=MediaType.sticker),  # dropped
        _msg(2, None, now + timedelta(seconds=1), media_type=MediaType.document),  # kept
    ]
    result = preprocess(msgs, settings())
    assert result.block_count == 1
    assert result.blocks[0].media == "document"


def test_links_extracted_short_message_kept(now):
    msgs = [_msg(1, "см https://example.com/doc", now)]
    result = preprocess(msgs, settings())
    assert result.block_count == 1
    assert "https://example.com/doc" in result.blocks[0].links


def test_serialize_blocks_format(now):
    msgs = [_msg(1, "важная новость про сроки проекта", now, sender="Ann")]
    result = preprocess(msgs, settings())
    text = serialize_blocks(result.blocks)
    assert text.startswith("--- тред 1 ---")
    assert "#1" in text and "Ann" in text
