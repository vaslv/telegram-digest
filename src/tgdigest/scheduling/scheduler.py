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

from tgdigest.db.base import Database
from tgdigest.db.enums import RunTrigger
from tgdigest.db.models import Chat
from tgdigest.db.repositories import ChatRepository, DigestRepository, MessageRepository
from tgdigest.logging import get_logger
from tgdigest.scheduling.triggers import evaluate_count_trigger, evaluate_time_trigger
from tgdigest.summarization.service import DigestService
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
    ) -> None:
        self._db = db
        self._service = service
        self._ingestor = ingestor
        self._scheduler = AsyncIOScheduler(timezone=UTC)
        self._locks: dict[int, asyncio.Lock] = {}
        self._job_intervals: dict[int, int] = {}
        self._tasks: set[asyncio.Task[None]] = set()

    async def start(self) -> None:
        async with self._db.session() as session:
            reaped = await DigestRepository(session).reap_stale_runs()
        if reaped:
            _log.warning("stale_runs_reaped", count=reaped)

        self._scheduler.start()
        await self.reconcile()
        self._scheduler.add_job(
            self.reconcile,
            IntervalTrigger(seconds=_RECONCILE_SECONDS),
            id="reconcile",
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
