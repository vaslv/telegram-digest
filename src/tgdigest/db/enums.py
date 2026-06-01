"""Enumerations used across ORM models and business logic.

Stored in PostgreSQL as native enum types; the lowercase ``value`` is what is
persisted (see ``values_callable`` usage in :mod:`tgdigest.db.models`).
"""

from __future__ import annotations

import enum


class ChatType(enum.StrEnum):
    group = "group"
    supergroup = "supergroup"
    channel = "channel"
    private = "private"


class MediaType(enum.StrEnum):
    photo = "photo"
    video = "video"
    document = "document"
    audio = "audio"
    voice = "voice"
    video_note = "video_note"
    sticker = "sticker"
    gif = "gif"
    poll = "poll"
    geo = "geo"
    contact = "contact"
    webpage = "webpage"
    other = "other"


class ImportanceType(enum.StrEnum):
    news = "news"
    decision = "decision"
    agreement = "agreement"
    task = "task"
    assignment = "assignment"
    deadline = "deadline"
    warning = "warning"
    instruction = "instruction"
    document = "document"
    link = "link"
    open_question = "open_question"
    plan_change = "plan_change"
    attention = "attention"
    recurring_topic = "recurring_topic"
    other = "other"


class RunTrigger(enum.StrEnum):
    scheduled = "scheduled"
    count = "count"  # type: ignore[assignment]  # shadows str.count; intentional value
    manual = "manual"
    reprocess = "reprocess"


class RunStatus(enum.StrEnum):
    pending = "pending"
    running = "running"
    success = "success"
    empty = "empty"
    failed = "failed"


class PromptScope(enum.StrEnum):
    global_system = "global_system"
    global_digest = "global_digest"
    stage1_instructions = "stage1_instructions"
    stage2_instructions = "stage2_instructions"


class RequestStatus(enum.StrEnum):
    pending = "pending"
    running = "running"
    done = "done"
    failed = "failed"


class ErrorStage(enum.StrEnum):
    ingest = "ingest"
    catchup = "catchup"
    stage1 = "stage1"
    stage2 = "stage2"
    render = "render"
    send = "send"
    scheduler = "scheduler"
    llm = "llm"
    other = "other"
