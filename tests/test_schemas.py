from __future__ import annotations

from tgdigest.summarization.schemas import DigestContent, RawEvent, Stage1Output


def test_raw_event_coerces_unknown_type():
    event = RawEvent(message_id=1, importance_type="totally-made-up", summary="x")
    assert event.importance_type == "other"


def test_raw_event_clamps_confidence():
    assert RawEvent(message_id=1, importance_type="task", summary="x", confidence=5).confidence == 1.0
    assert RawEvent(message_id=1, importance_type="task", summary="x", confidence=-1).confidence == 0.0


def test_raw_event_none_related_becomes_list():
    event = RawEvent(message_id=1, importance_type="task", summary="x", related_message_ids=None)
    assert event.related_message_ids == []


def test_stage1_output_defaults_empty():
    assert Stage1Output().events == []


def test_digest_content_meaningfulness():
    assert DigestContent(key_events=["a"]).is_meaningful() is True
    assert DigestContent().is_meaningful() is False


def test_digest_content_none_coercion():
    content = DigestContent(summary=None, key_events=None, conclusion=None)
    assert content.summary == "" and content.key_events == []
