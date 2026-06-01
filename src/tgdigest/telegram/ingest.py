"""Message ingestion: startup catch-up and live event handlers.

State is durable in PostgreSQL (``chat_states.last_seen_message_id``), so a
restart resumes exactly where it left off and the unique constraint guarantees
no duplicates. Live handlers are registered once and filter by the in-memory
watched set, which is refreshed by the scheduler's reconcile loop.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from telethon import TelegramClient, events

from tgdigest.db.base import Database
from tgdigest.db.enums import ErrorStage
from tgdigest.db.models import Chat
from tgdigest.db.repositories import ChatRepository, ErrorRepository, MessageRepository
from tgdigest.logging import get_logger
from tgdigest.telegram.mapper import display_name, map_message
from tgdigest.util import utcnow

_log = get_logger("telegram.ingest")

_BATCH = 200
DEFAULT_INITIAL_BACKFILL = 100

OnStored = Callable[[int], Awaitable[None]]


class MessageIngestor:
    def __init__(
        self,
        client: TelegramClient,
        db: Database,
        *,
        on_stored: OnStored | None = None,
        initial_backfill: int = DEFAULT_INITIAL_BACKFILL,
    ) -> None:
        self._client = client
        self._db = db
        self._on_stored = on_stored
        self._initial_backfill = initial_backfill
        self._watched_tg: set[int] = set()
        self._tg_to_internal: dict[int, int] = {}

    def update_watched(self, chats: list[Chat]) -> None:
        self._watched_tg = {c.telegram_chat_id for c in chats}
        self._tg_to_internal = {c.telegram_chat_id: c.id for c in chats}

    def set_on_stored(self, callback: OnStored | None) -> None:
        self._on_stored = callback

    def register_handlers(self) -> None:
        self._client.add_event_handler(self._on_new, events.NewMessage())
        self._client.add_event_handler(self._on_edit, events.MessageEdited())

    # ── catch-up ────────────────────────────────────────────────────────────
    async def catchup_all(self, chats: list[Chat]) -> None:
        for chat in chats:
            try:
                inserted = await self.catchup_chat(chat)
                _log.info("catchup_done", chat_id=chat.id, inserted=inserted)
            except Exception as exc:
                _log.warning("catchup_failed", chat_id=chat.id, error=str(exc))
                await self._record_error(ErrorStage.catchup, exc, chat_id=chat.id)

    async def catchup_chat(self, chat: Chat) -> int:
        async with self._db.session() as session:
            state = await ChatRepository(session).ensure_state(chat.id)
            min_id = state.last_seen_message_id

        entity = await self._resolve_entity(chat.telegram_chat_id)
        batch: list[dict[str, Any]] = []
        inserted = 0
        max_id = min_id

        if min_id > 0:
            iterator = self._client.iter_messages(entity, min_id=min_id, reverse=True)
        else:
            iterator = self._client.iter_messages(entity, limit=self._initial_backfill)

        async for message in iterator:
            batch.append(map_message(message, chat.id, sender_name=display_name(message.sender)))
            max_id = max(max_id, message.id)
            if len(batch) >= _BATCH:
                inserted += await self._flush(batch)
                batch = []
        if batch:
            inserted += await self._flush(batch)

        if max_id > min_id:
            async with self._db.session() as session:
                await ChatRepository(session).update_state(
                    chat.id, last_seen_message_id=max_id, last_catchup_at=utcnow()
                )
        return inserted

    # ── live handlers ───────────────────────────────────────────────────────
    async def _on_new(self, event: events.NewMessage.Event) -> None:
        internal = self._tg_to_internal.get(event.chat_id)
        if internal is None:
            return
        try:
            name = display_name(event.message.sender)
            if name is None:
                name = display_name(await _safe_get_sender(event))
            row = map_message(event.message, internal, sender_name=name)
            inserted = await self._flush([row])
            await self._bump_last_seen(internal, event.message.id)
        except Exception as exc:
            _log.warning("ingest_new_failed", chat_id=internal, error=str(exc))
            await self._record_error(ErrorStage.ingest, exc, chat_id=internal)
            return
        if inserted and self._on_stored is not None:
            await self._on_stored(internal)

    async def _on_edit(self, event: events.MessageEdited.Event) -> None:
        internal = self._tg_to_internal.get(event.chat_id)
        if internal is None:
            return
        try:
            mapped = map_message(event.message, internal)
            async with self._db.session() as session:
                await MessageRepository(session).apply_edit(
                    internal,
                    event.message.id,
                    text=mapped["text"],
                    media_caption=mapped["media_caption"],
                    reactions=mapped["reactions"],
                    views=mapped["views"],
                    edit_date=mapped["edit_date"],
                )
        except Exception as exc:
            _log.warning("ingest_edit_failed", chat_id=internal, error=str(exc))

    # ── internals ───────────────────────────────────────────────────────────
    async def _flush(self, rows: list[dict[str, Any]]) -> int:
        async with self._db.session() as session:
            return await MessageRepository(session).insert_many(rows)

    async def _bump_last_seen(self, chat_id: int, message_id: int) -> None:
        async with self._db.session() as session:
            repo = ChatRepository(session)
            state = await repo.ensure_state(chat_id)
            if message_id > state.last_seen_message_id:
                await repo.update_state(chat_id, last_seen_message_id=message_id)

    async def _resolve_entity(self, telegram_chat_id: int) -> Any:
        try:
            return await self._client.get_input_entity(telegram_chat_id)
        except (ValueError, TypeError):
            return await self._client.get_entity(telegram_chat_id)

    async def _record_error(
        self, stage: ErrorStage, exc: Exception, *, chat_id: int | None = None
    ) -> None:
        try:
            async with self._db.session() as session:
                await ErrorRepository(session).record(
                    stage=stage,
                    error_type=type(exc).__name__,
                    message=str(exc),
                    chat_id=chat_id,
                )
        except Exception:
            _log.error("error_record_failed", stage=stage.value)


async def _safe_get_sender(event: events.NewMessage.Event) -> Any:
    try:
        return await event.get_sender()
    except Exception:
        return None
