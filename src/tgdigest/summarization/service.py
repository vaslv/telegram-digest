"""DigestService — orchestrates a single digest run end to end.

Window selection → preprocess → stage 1 (importance) → persist events →
threshold filter → stage 2 (digest) → render → persist → send → advance state.
Guarded by a PostgreSQL advisory lock so a scheduled run and a manual
``run-digest`` cannot process the same chat concurrently.
"""

from __future__ import annotations

import traceback
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import text
from telethon import TelegramClient

from tgdigest.config.settings import Settings
from tgdigest.db.base import Database
from tgdigest.db.enums import ErrorStage, RunStatus, RunTrigger
from tgdigest.db.models import Chat, Message
from tgdigest.db.repositories import (
    AnalysisRepository,
    ChatRepository,
    DigestRepository,
    ErrorRepository,
    MessageRepository,
    PromptRepository,
)
from tgdigest.llm.base import LLMProvider
from tgdigest.logging import get_logger
from tgdigest.summarization.preprocess import preprocess
from tgdigest.summarization.prompts import PromptBuilder, load_prompt_builder
from tgdigest.summarization.render import render_digest, render_empty
from tgdigest.summarization.schemas import DigestContent
from tgdigest.summarization.stage1_importance import DetectedEvent, ImportanceDetector
from tgdigest.summarization.stage2_digest import DigestComposer
from tgdigest.telegram.sender import send_digest
from tgdigest.util import utcnow

_log = get_logger("summarization.service")


@dataclass(slots=True)
class RunOutcome:
    status: str  # "success" | "empty" | "failed" | "skipped"
    run_id: int | None
    digest_id: int | None
    important_count: int
    sent: bool
    body_markdown: str | None
    message: str


class DigestService:
    def __init__(
        self,
        db: Database,
        provider: LLMProvider,
        settings: Settings,
        client: TelegramClient | None = None,
    ) -> None:
        self._db = db
        self._provider = provider
        self._settings = settings
        self._client = client

    async def run(
        self,
        chat_id: int,
        *,
        trigger: RunTrigger,
        since: datetime | None = None,
        until: datetime | None = None,
        dry_run: bool = False,
        send: bool = True,
    ) -> RunOutcome:
        async with self._advisory_lock(chat_id) as acquired:
            if not acquired:
                _log.info("digest_skipped_locked", chat_id=chat_id)
                return RunOutcome(
                    "skipped", None, None, 0, False, None, "другой запуск уже выполняется"
                )
            return await self._run_locked(
                chat_id, trigger=trigger, since=since, until=until, dry_run=dry_run, send=send
            )

    async def _run_locked(
        self,
        chat_id: int,
        *,
        trigger: RunTrigger,
        since: datetime | None,
        until: datetime | None,
        dry_run: bool,
        send: bool,
    ) -> RunOutcome:
        forward = since is None and until is None
        async with self._db.session() as session:
            chat = await ChatRepository(session).get_by_id(chat_id)
            if chat is None:
                return RunOutcome("failed", None, None, 0, False, None, "чат не найден")
            state = await ChatRepository(session).ensure_state(chat_id)
            builder = await load_prompt_builder(
                PromptRepository(session), self._settings.app.digest_language
            )
            after = state.last_processed_message_id if forward else 0
            messages = await MessageRepository(session).fetch_window(
                chat_id, after_message_id=after, since=since, until=until
            )

        period_start = messages[0].date if messages else None
        period_end = messages[-1].date if messages else None
        to_id = messages[-1].telegram_message_id if messages else state.last_processed_message_id
        period = self._period_str(period_start, period_end)

        async with self._db.session() as session:
            run = await DigestRepository(session).create_run(
                chat_id=chat_id,
                trigger=trigger,
                period_start=period_start,
                period_end=period_end,
                from_message_id=after,
                to_message_id=to_id,
                llm_provider=self._settings.llm.provider.value,
                llm_model=self._settings.llm.model,
            )
            run_id = run.id
            await DigestRepository(session).update_run(
                run_id,
                prompt_snapshot=builder.snapshot(chat),
                raw_message_count=len(messages),
            )

        try:
            return await self._process(
                chat=chat,
                run_id=run_id,
                messages=messages,
                builder=builder,
                period=period,
                period_start=period_start,
                period_end=period_end,
                to_id=to_id,
                forward=forward,
                dry_run=dry_run,
                send=send,
            )
        except Exception as exc:
            _log.error("digest_run_failed", chat_id=chat_id, run_id=run_id, error=str(exc))
            await self._fail_run(run_id, chat_id, exc)
            return RunOutcome("failed", run_id, None, 0, False, None, f"ошибка: {exc}")

    async def _process(
        self,
        *,
        chat: Chat,
        run_id: int,
        messages: list[Message],
        builder: PromptBuilder,
        period: str,
        period_start: datetime | None,
        period_end: datetime | None,
        to_id: int,
        forward: bool,
        dry_run: bool,
        send: bool,
    ) -> RunOutcome:
        if not messages:
            return await self._finish_empty(
                chat, run_id, period, period_start, period_end,
                forward=forward, advance_to=to_id, dry_run=dry_run, send=send,
                reason="нет новых сообщений",
            )

        pre = preprocess(messages, self._settings.preprocess)
        async with self._db.session() as session:
            await DigestRepository(session).update_run(
                run_id, preprocessed_block_count=pre.block_count
            )
        _log.info(
            "preprocessed",
            chat_id=chat.id,
            raw=pre.raw_count,
            blocks=pre.block_count,
        )
        if not pre.blocks:
            return await self._finish_empty(
                chat, run_id, period, period_start, period_end,
                forward=forward, advance_to=to_id, dry_run=dry_run, send=send,
                reason="после предобработки не осталось содержательных сообщений",
            )

        detector = ImportanceDetector(self._provider, builder, self._settings.llm)
        stage1 = await detector.detect(chat, pre)
        await self._persist_events(chat.id, run_id, stage1.events)

        important = [e for e in stage1.events if e.confidence >= chat.importance_threshold]
        prompt_tokens, completion_tokens = stage1.prompt_tokens, stage1.completion_tokens
        _log.info(
            "stage1_done",
            chat_id=chat.id,
            events=len(stage1.events),
            important=len(important),
            chunks=stage1.chunks,
        )

        if not important:
            return await self._finish_empty(
                chat, run_id, period, period_start, period_end,
                forward=forward, advance_to=to_id, dry_run=dry_run, send=send,
                reason="значимых событий выше порога не обнаружено",
                important_count=len(stage1.events),
                tokens=(prompt_tokens, completion_tokens),
            )

        text_by_ref = {
            m.telegram_message_id: (m.text or m.media_caption or "") for m in messages
        }
        composer = DigestComposer(self._provider, builder, self._settings.llm)
        stage2 = await composer.compose(chat, important, text_by_ref, period)
        prompt_tokens += stage2.prompt_tokens
        completion_tokens += stage2.completion_tokens

        content = stage2.content if stage2.ok else _content_from_events(important)
        if not content.is_meaningful():
            content = _content_from_events(important)
        body = render_digest(chat, period, content, important)

        async with self._db.session() as session:
            digest = await DigestRepository(session).save_digest(
                run_id=run_id,
                chat_id=chat.id,
                title=chat.title,
                period_start=period_start,
                period_end=period_end,
                summary=content.summary or None,
                structured=content.model_dump(),
                body_markdown=body,
                is_empty=False,
                target_chat_id=chat.digest_target_chat_id,
            )
            digest_id = digest.id
            await DigestRepository(session).update_run(
                run_id,
                status=RunStatus.success,
                important_count=len(important),
                tokens_prompt=prompt_tokens,
                tokens_completion=completion_tokens,
                finished_at=utcnow(),
            )

        sent = await self._maybe_send(chat, digest_id, body, dry_run=dry_run, send=send)
        if forward and not dry_run:
            await self._advance_state(chat.id, to_id)
        return RunOutcome(
            "success", run_id, digest_id, len(important), sent, body, "дайджест сформирован"
        )

    # ── empty / failure paths ────────────────────────────────────────────────
    async def _finish_empty(
        self,
        chat: Chat,
        run_id: int,
        period: str,
        period_start: datetime | None,
        period_end: datetime | None,
        *,
        forward: bool,
        advance_to: int,
        dry_run: bool,
        send: bool,
        reason: str,
        important_count: int = 0,
        tokens: tuple[int, int] = (0, 0),
    ) -> RunOutcome:
        body = render_empty(chat, period)
        async with self._db.session() as session:
            digest = await DigestRepository(session).save_digest(
                run_id=run_id,
                chat_id=chat.id,
                title=chat.title,
                period_start=period_start,
                period_end=period_end,
                summary=None,
                structured=None,
                body_markdown=body if chat.send_empty_digest else None,
                is_empty=True,
                target_chat_id=chat.digest_target_chat_id,
            )
            digest_id = digest.id
            await DigestRepository(session).update_run(
                run_id,
                status=RunStatus.empty,
                important_count=important_count,
                tokens_prompt=tokens[0],
                tokens_completion=tokens[1],
                finished_at=utcnow(),
            )

        sent = False
        if chat.send_empty_digest:
            sent = await self._maybe_send(chat, digest_id, body, dry_run=dry_run, send=send)
        if forward and not dry_run:
            await self._advance_state(chat.id, advance_to)
        return RunOutcome("empty", run_id, digest_id, important_count, sent, body, reason)

    async def _maybe_send(
        self, chat: Chat, digest_id: int, body: str, *, dry_run: bool, send: bool
    ) -> bool:
        if dry_run or not send or self._client is None:
            return False
        await send_digest(self._client, chat.digest_target_chat_id, body)
        async with self._db.session() as session:
            await DigestRepository(session).mark_sent(
                digest_id, target_chat_id=chat.digest_target_chat_id, when=utcnow()
            )
        return True

    async def _persist_events(
        self, chat_id: int, run_id: int, events: list[DetectedEvent]
    ) -> None:
        if not events:
            return
        async with self._db.session() as session:
            id_map = await MessageRepository(session).get_by_telegram_ids(
                chat_id, [e.telegram_message_id for e in events]
            )
            await AnalysisRepository(session).add_events(
                run_id=run_id,
                chat_id=chat_id,
                events=[
                    {
                        "message_id": (
                            id_map[e.telegram_message_id].id
                            if e.telegram_message_id in id_map
                            else None
                        ),
                        "telegram_message_id": e.telegram_message_id,
                        "importance_type": e.importance_type,
                        "summary": e.summary,
                        "reason": e.reason,
                        "confidence": e.confidence,
                        "related_message_ids": e.related_message_ids,
                    }
                    for e in events
                ],
            )

    async def _advance_state(self, chat_id: int, to_id: int) -> None:
        async with self._db.session() as session:
            await ChatRepository(session).touch_digest(chat_id, to_id, utcnow())

    async def _fail_run(self, run_id: int, chat_id: int, exc: Exception) -> None:
        async with self._db.session() as session:
            await DigestRepository(session).update_run(
                run_id, status=RunStatus.failed, error=str(exc)[:4000], finished_at=utcnow()
            )
            await ErrorRepository(session).record(
                stage=ErrorStage.stage2,
                error_type=type(exc).__name__,
                message=str(exc),
                chat_id=chat_id,
                digest_run_id=run_id,
                traceback=traceback.format_exc()[:8000],
            )

    # ── infrastructure ───────────────────────────────────────────────────────
    @asynccontextmanager
    async def _advisory_lock(self, key: int) -> AsyncIterator[bool]:
        session = self._db.sessionmaker()
        try:
            result = await session.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": key})
            acquired = bool(result.scalar())
            if not acquired:
                yield False
                return
            try:
                yield True
            finally:
                await session.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": key})
                await session.commit()
        finally:
            await session.close()

    def _period_str(self, start: datetime | None, end: datetime | None) -> str:
        if start is None or end is None:
            return "—"
        try:
            tz = ZoneInfo(self._settings.app.timezone)
        except Exception:
            tz = ZoneInfo("UTC")
        start_local = start.astimezone(tz)
        end_local = end.astimezone(tz)
        if start_local.date() == end_local.date():
            return f"{start_local:%d.%m.%Y %H:%M}–{end_local:%H:%M}"
        return f"{start_local:%d.%m.%Y %H:%M} – {end_local:%d.%m.%Y %H:%M}"


def _content_from_events(events: list[DetectedEvent]) -> DigestContent:
    """Deterministic fallback digest when stage 2 fails or returns nothing."""
    return DigestContent(
        summary="",
        key_events=[f"[{e.importance_type.value}] {e.summary}" for e in events[:20]],
        conclusion="",
    )
