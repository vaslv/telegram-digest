"""APScheduler-based digest scheduling.

Two mechanisms run together:
* **time** — one interval job per chat (``summary_interval_minutes``);
* **count** — checked on every stored message; fires immediately at
  ``max_messages_before_digest``.

A 60-second reconcile loop keeps interval jobs and the ingestor's watched set in
sync with the database, so config changes made via the CLI take effect on a
running daemon. Per-chat ``asyncio.Lock`` prevents in-process overlap;
``DigestService`` adds a cross-process advisory lock on top.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC

from apscheduler.jobstores.base import JobLookupError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from telethon import TelegramClient

from tgdigest.db.base import Database
from tgdigest.db.enums import RunTrigger
from tgdigest.db.models import Chat
from tgdigest.db.repositories import (
    ChatRepository,
    DialogRepository,
    DigestRepository,
    DigestRequestRepository,
    MessageRepository,
)
from tgdigest.logging import get_logger
from tgdigest.scheduling.triggers import evaluate_count_trigger, evaluate_time_trigger
from tgdigest.summarization.service import DigestService
from tgdigest.telegram.dialogs import list_dialogs
from tgdigest.telegram.ingest import MessageIngestor

_log = get_logger("scheduling")
_RECONCILE_SECONDS = 60


class DigestScheduler:
    def __init__(
        self,
        db: Database,
        service: DigestService,
        *,
        ingestor: MessageIngestor | None = None,
        client: TelegramClient | None = None,
        dialog_refresh_minutes: int = 10,
        request_poll_seconds: int = 8,
    ) -> None:
        self._db = db
        self._service = service
        self._ingestor = ingestor
        self._client = client
        self._dialog_refresh_minutes = dialog_refresh_minutes
        self._request_poll_seconds = request_poll_seconds
        self._scheduler = AsyncIOScheduler(timezone=UTC)
        self._locks: dict[int, asyncio.Lock] = {}
        self._job_intervals: dict[int, int] = {}
        self._tasks: set[asyncio.Task[None]] = set()

    async def start(self) -> None:
        async with self._db.session() as session:
            reaped_runs = await DigestRepository(session).reap_stale_runs()
            reaped_requests = await DigestRequestRepository(session).reap_running()
        if reaped_runs or reaped_requests:
            _log.warning("stale_reaped", runs=reaped_runs, requests=reaped_requests)

        self._scheduler.start()
        if self._client is not None:
            await self._refresh_dialogs()  # warm the cache + Telethon entity cache first
        await self.reconcile()
        self._scheduler.add_job(
            self.reconcile,
            IntervalTrigger(seconds=_RECONCILE_SECONDS),
            id="reconcile",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        if self._client is not None:
            self._scheduler.add_job(
                self._refresh_dialogs,
                IntervalTrigger(minutes=self._dialog_refresh_minutes),
                id="dialogs",
                replace_existing=True,
                max_instances=1,
                coalesce=True,
            )
        self._scheduler.add_job(
            self._poll_requests,
            IntervalTrigger(seconds=self._request_poll_seconds),
            id="requests",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        _log.info("scheduler_started")

    async def shutdown(self) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
        for task in list(self._tasks):
            task.cancel()

    def _lock(self, chat_id: int) -> asyncio.Lock:
        return self._locks.setdefault(chat_id, asyncio.Lock())

    async def reconcile(self) -> None:
        async with self._db.session() as session:
            chats = await ChatRepository(session).get_enabled()
        if self._ingestor is not None:
            self._ingestor.update_watched(chats)
        await self._backfill_titles(chats)

        desired = {c.id: c.summary_interval_minutes for c in chats}
        for chat_id in list(self._job_intervals):
            if desired.get(chat_id) in (None, 0) or chat_id not in desired:
                self._remove_job(chat_id)

        for chat in chats:
            interval = chat.summary_interval_minutes
            if not interval:
                continue
            if self._job_intervals.get(chat.id) != interval:
                self._scheduler.add_job(
                    self._run_scheduled,
                    IntervalTrigger(minutes=interval),
                    args=[chat.id],
                    id=f"time:{chat.id}",
                    replace_existing=True,
                    max_instances=1,
                    coalesce=True,
                )
                self._job_intervals[chat.id] = interval

    def _remove_job(self, chat_id: int) -> None:
        with contextlib.suppress(JobLookupError):
            self._scheduler.remove_job(f"time:{chat_id}")
        self._job_intervals.pop(chat_id, None)

    async def _run_scheduled(self, chat_id: int) -> None:
        async with self._lock(chat_id):
            decision = await self._evaluate(chat_id, count_trigger=False)
            if decision is None:
                return
            _, should_run = decision
            if not should_run:
                _log.debug("scheduled_skip_below_min", chat_id=chat_id)
                return
            await self._service.run(chat_id, trigger=RunTrigger.scheduled)

    async def on_message_stored(self, chat_id: int) -> None:
        """Count-trigger hook invoked by the ingestor after storing a message."""
        if self._lock(chat_id).locked():
            return  # a run is already in progress for this chat
        decision = await self._evaluate(chat_id, count_trigger=True)
        if decision is None:
            return
        _chat, should_run = decision
        if should_run:
            task = asyncio.create_task(self._run_count(chat_id))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def _run_count(self, chat_id: int) -> None:
        async with self._lock(chat_id):
            _log.info("count_trigger_fired", chat_id=chat_id)
            await self._service.run(chat_id, trigger=RunTrigger.count)

    # ── dialog cache + request queue (delegation for web/CLI) ────────────────
    async def _refresh_dialogs(self) -> None:
        if self._client is None:
            return
        try:
            dialogs = await list_dialogs(self._client)
        except Exception as exc:  # never let a refresh kill the loop
            _log.warning("dialog_refresh_failed", error=str(exc))
            return
        rows = [
            {
                "telegram_chat_id": d.telegram_chat_id,
                "title": d.title,
                "chat_type": d.chat_type,
                "username": d.username,
                "is_member": True,
            }
            for d in dialogs
        ]
        async with self._db.session() as session:
            await DialogRepository(session).upsert_many(rows)
        _log.info("dialogs_refreshed", count=len(rows))

    async def _poll_requests(self) -> None:
        async with self._db.session() as session:
            claimed = await DigestRequestRepository(session).claim_pending(limit=5)
            items = [(r.id, r.chat_id, r.dry_run) for r in claimed]
        for request_id, chat_id, dry_run in items:
            try:
                outcome = await self._service.run(
                    chat_id, trigger=RunTrigger.manual, dry_run=dry_run, send=not dry_run
                )
                async with self._db.session() as session:
                    await DigestRequestRepository(session).mark_done(
                        request_id, digest_run_id=outcome.run_id
                    )
                _log.info("request_done", request_id=request_id, status=outcome.status)
            except Exception as exc:  # record and move on
                _log.warning("request_failed", request_id=request_id, error=str(exc))
                async with self._db.session() as session:
                    await DigestRequestRepository(session).mark_failed(request_id, error=str(exc))

    async def _backfill_titles(self, chats: list[Chat]) -> None:
        placeholders = [c for c in chats if c.title == str(c.telegram_chat_id)]
        if not placeholders:
            return
        async with self._db.session() as session:
            dialog_repo = DialogRepository(session)
            chat_repo = ChatRepository(session)
            for chat in placeholders:
                dialog = await dialog_repo.get(chat.telegram_chat_id)
                if dialog is not None:
                    await chat_repo.set_identity(
                        chat.id,
                        title=dialog.title,
                        chat_type=dialog.chat_type,
                        username=dialog.username,
                    )
                    _log.info("chat_title_backfilled", chat_id=chat.id)

    async def _evaluate(self, chat_id: int, *, count_trigger: bool) -> tuple[Chat, bool] | None:
        async with self._db.session() as session:
            chat = await ChatRepository(session).get_by_id(chat_id)
            if chat is None or not chat.enabled:
                return None
            state = await ChatRepository(session).ensure_state(chat_id)
            unprocessed = await MessageRepository(session).count_after(
                chat_id, state.last_processed_message_id
            )
        if count_trigger:
            return chat, evaluate_count_trigger(unprocessed, chat.max_messages_before_digest)
        return chat, evaluate_time_trigger(
            unprocessed, chat.min_messages_before_digest, chat.send_empty_digest
        )
