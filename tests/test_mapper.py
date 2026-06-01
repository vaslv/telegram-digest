from __future__ import annotations

from tgdigest.db.enums import MediaType
from tgdigest.telegram.mapper import display_name, map_message


class Fake:
    """Attribute bag returning None for anything unset (mimics a Telethon msg)."""

    def __init__(self, **kw):
        self.__dict__.update(kw)

    def __getattr__(self, _name):
        return None


def test_display_name_variants():
    assert display_name(Fake(first_name="Ann", last_name="Lee")) == "Ann Lee"
    assert display_name(Fake(title="Канал")) == "Канал"
    assert display_name(Fake(username="bob")) == "bob"
    assert display_name(None) is None


def test_plain_text_message(now):
    msg = Fake(id=10, sender_id=5, sender=Fake(first_name="Ann"), date=now, message="Текст")
    row = map_message(msg, chat_id=1)
    assert row["text"] == "Текст" and row["media_type"] is None
    assert row["sender_name"] == "Ann" and row["is_service"] is False


def test_photo_with_caption_reactions_reply(now):
    msg = Fake(
        id=11, date=now, message="подпись", photo=Fake(), media=Fake(),
        reply_to=Fake(reply_to_msg_id=10),
        reactions=Fake(results=[Fake(reaction=Fake(emoticon="👍"), count=3),
                                Fake(reaction=Fake(), count=1)]),
    )
    row = map_message(msg, chat_id=1, sender_name="Bob")
    assert row["media_type"] == MediaType.photo
    assert row["media_caption"] == "подпись" and row["text"] is None
    assert row["reply_to_message_id"] == 10
    assert row["reactions"] == [{"emoji": "👍", "count": 3}, {"emoji": "custom", "count": 1}]


def test_service_message(now):
    msg = Fake(id=12, date=now, action=Fake())
    row = map_message(msg, chat_id=1)
    assert row["is_service"] is True and row["service_action"] == "Fake"
    assert row["text"] is None


def test_web_preview_is_text(now):
    msg = Fake(id=13, date=now, message="https://x полезно", web_preview=Fake(), media=Fake())
    row = map_message(msg, chat_id=1)
    assert row["text"].startswith("https://x") and row["media_type"] == MediaType.webpage
