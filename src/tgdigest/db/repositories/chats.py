"""Chat configuration and per-chat processing state."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import delete, select, update

from tgdigest.db.enums import ChatType
from tgdigest.db.models import Chat, ChatState
from tgdigest.db.repositories.base import BaseRepository

_CONFIG_FIELDS = (
    "enabled",
    "digest_target_chat_id",
    "summary_interval_minutes",
    "min_messages_before_digest",
    "max_messages_before_digest",
    "importance_threshold",
    "send_empty_digest",
)


class ChatRepository(BaseRepository):
    async def get_by_id(self, chat_id: int) -> Chat | None:
        return await self.session.get(Chat, chat_id)

    async def get_by_telegram_id(self, telegram_chat_id: int) -> Chat | None:
        stmt = select(Chat).where(Chat.telegram_chat_id == telegram_chat_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_all(self) -> list[Chat]:
        stmt = select(Chat).order_by(Chat.title)
        return list((await self.session.execute(stmt)).scalars().all())

    async def get_enabled(self) -> list[Chat]:
        stmt = select(Chat).where(Chat.enabled.is_(True)).order_by(Chat.id)
        return list((await self.session.execute(stmt)).scalars().all())

    async def create_or_update(
        self,
        *,
        telegram_chat_id: int,
        title: str,
        chat_type: ChatType,
        username: str | None = None,
        **config: Any,
    ) -> Chat:
        """Insert a new monitored chat or update identity + provided config."""
        provided = {k: v for k, v in config.items() if k in _CONFIG_FIELDS and v is not None}
        chat = await self.get_by_telegram_id(telegram_chat_id)
        if chat is None:
            chat = Chat(
                telegram_chat_id=telegram_chat_id,
                title=title,
                chat_type=chat_type,
                username=username,
                **provided,
            )
            self.session.add(chat)
        else:
            chat.title = title
            chat.chat_type = chat_type
            chat.username = username
            for key, value in provided.items():
                setattr(chat, key, value)
        await self.session.flush()
        await self.ensure_state(chat.id)
        return chat

    async def update_config(self, chat_id: int, **fields: Any) -> None:
        clean = {k: v for k, v in fields.items() if v is not None}
        if not clean:
            return
        await self.session.execute(update(Chat).where(Chat.id == chat_id).values(**clean))

    async def set_enabled(self, chat_id: int, enabled: bool) -> None:
        await self.session.execute(
            update(Chat).where(Chat.id == chat_id).values(enabled=enabled)
        )

    async def set_prompts(
        self, chat_id: int, *, context: str | None = None, summary: str | None = None
    ) -> None:
        values: dict[str, Any] = {}
        if context is not None:
            values["chat_context_prompt"] = context
        if summary is not None:
            values["chat_summary_prompt"] = summary
        if values:
            await self.session.execute(update(Chat).where(Chat.id == chat_id).values(**values))

    async def delete(self, chat_id: int) -> None:
        await self.session.execute(delete(Chat).where(Chat.id == chat_id))

    # ── processing state ────────────────────────────────────────────────────
    async def get_state(self, chat_id: int) -> ChatState | None:
        return await self.session.get(ChatState, chat_id)

    async def ensure_state(self, chat_id: int) -> ChatState:
        state = await self.session.get(ChatState, chat_id)
        if state is None:
            state = ChatState(chat_id=chat_id)
            self.session.add(state)
            await self.session.flush()
        return state

    async def update_state(self, chat_id: int, **fields: Any) -> None:
        clean = {k: v for k, v in fields.items() if v is not None}
        if not clean:
            return
        await self.ensure_state(chat_id)
        await self.session.execute(
            update(ChatState).where(ChatState.chat_id == chat_id).values(**clean)
        )

    async def touch_digest(self, chat_id: int, processed_up_to: int, when: datetime) -> None:
        await self.update_state(
            chat_id,
            last_processed_message_id=processed_up_to,
            last_digest_at=when,
        )
