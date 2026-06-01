"""Dialog listing and chat resolution helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from telethon import TelegramClient, utils
from telethon.tl.types import Channel, User

from tgdigest.db.enums import ChatType


@dataclass(slots=True)
class DialogInfo:
    telegram_chat_id: int
    title: str
    chat_type: ChatType
    username: str | None
    unread: int


@dataclass(slots=True)
class ChatInfo:
    telegram_chat_id: int
    title: str
    chat_type: ChatType
    username: str | None
    entity: Any


def entity_chat_type(entity: Any) -> ChatType:
    if isinstance(entity, User):
        return ChatType.private
    if isinstance(entity, Channel):
        return ChatType.channel if getattr(entity, "broadcast", False) else ChatType.supergroup
    return ChatType.group  # basic group (telethon.tl.types.Chat)


async def list_dialogs(client: TelegramClient, *, limit: int | None = None) -> list[DialogInfo]:
    dialogs: list[DialogInfo] = []
    async for dialog in client.iter_dialogs(limit=limit):
        entity = dialog.entity
        dialogs.append(
            DialogInfo(
                telegram_chat_id=utils.get_peer_id(entity),
                title=dialog.name or utils.get_display_name(entity) or str(dialog.id),
                chat_type=entity_chat_type(entity),
                username=getattr(entity, "username", None),
                unread=dialog.unread_count,
            )
        )
    return dialogs


async def resolve_chat(client: TelegramClient, ref: str | int) -> ChatInfo:
    """Resolve a chat reference (marked id like ``-100123`` or ``@username``)."""
    entity = await client.get_entity(ref)
    telegram_chat_id = utils.get_peer_id(entity)
    return ChatInfo(
        telegram_chat_id=telegram_chat_id,
        title=utils.get_display_name(entity) or str(telegram_chat_id),
        chat_type=entity_chat_type(entity),
        username=getattr(entity, "username", None),
        entity=entity,
    )
