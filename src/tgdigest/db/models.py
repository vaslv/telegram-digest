"""SQLAlchemy ORM models (PostgreSQL).

The schema separates raw ingest (``messages``), monitoring configuration
(``chats`` + ``chat_states``), analysis output (``importance_events`` /
``digest_runs`` / ``digests``), versioned prompts and an error journal.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy import (
    text as sa_text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from tgdigest.db.base import Base
from tgdigest.db.enums import (
    ChatType,
    ErrorStage,
    ImportanceType,
    MediaType,
    PromptScope,
    RunStatus,
    RunTrigger,
)


def _enum(enum_cls: type, name: str) -> SAEnum:
    return SAEnum(
        enum_cls,
        name=name,
        values_callable=lambda e: [m.value for m in e],
        native_enum=True,
    )


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Chat(TimestampMixin, Base):
    __tablename__ = "chats"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    telegram_chat_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    title: Mapped[str] = mapped_column(Text)
    chat_type: Mapped[ChatType] = mapped_column(_enum(ChatType, "chat_type"))
    username: Mapped[str | None] = mapped_column(Text, nullable=True)

    enabled: Mapped[bool] = mapped_column(Boolean, server_default=sa_text("true"))
    digest_target_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    summary_interval_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    min_messages_before_digest: Mapped[int] = mapped_column(Integer, server_default=sa_text("10"))
    max_messages_before_digest: Mapped[int] = mapped_column(Integer, server_default=sa_text("300"))
    importance_threshold: Mapped[float] = mapped_column(Float, server_default=sa_text("0.5"))
    chat_context_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    chat_summary_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    send_empty_digest: Mapped[bool] = mapped_column(Boolean, server_default=sa_text("false"))

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ChatState(Base):
    __tablename__ = "chat_states"

    chat_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("chats.id", ondelete="CASCADE"), primary_key=True
    )
    last_seen_message_id: Mapped[int] = mapped_column(BigInteger, server_default=sa_text("0"))
    last_processed_message_id: Mapped[int] = mapped_column(BigInteger, server_default=sa_text("0"))
    last_digest_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_catchup_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Message(TimestampMixin, Base):
    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("chat_id", "telegram_message_id", name="uq_messages_chat_msg"),
        Index("ix_messages_chat_date", "chat_id", "date"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("chats.id", ondelete="CASCADE"), index=True
    )
    telegram_message_id: Mapped[int] = mapped_column(BigInteger)
    sender_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    sender_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    reply_to_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    media_type: Mapped[MediaType | None] = mapped_column(
        _enum(MediaType, "media_type"), nullable=True
    )
    media_caption: Mapped[str | None] = mapped_column(Text, nullable=True)
    views: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reactions: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)
    forward_from: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_service: Mapped[bool] = mapped_column(Boolean, server_default=sa_text("false"))
    service_action: Mapped[str | None] = mapped_column(Text, nullable=True)
    edit_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    raw: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)


class DigestRun(TimestampMixin, Base):
    __tablename__ = "digest_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("chats.id", ondelete="CASCADE"), index=True
    )
    trigger: Mapped[RunTrigger] = mapped_column(_enum(RunTrigger, "run_trigger"))
    status: Mapped[RunStatus] = mapped_column(
        _enum(RunStatus, "run_status"), server_default=sa_text("'pending'")
    )
    period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    from_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    to_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    raw_message_count: Mapped[int] = mapped_column(Integer, server_default=sa_text("0"))
    preprocessed_block_count: Mapped[int] = mapped_column(Integer, server_default=sa_text("0"))
    important_count: Mapped[int] = mapped_column(Integer, server_default=sa_text("0"))
    llm_provider: Mapped[str | None] = mapped_column(Text, nullable=True)
    llm_model: Mapped[str | None] = mapped_column(Text, nullable=True)
    prompt_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    tokens_prompt: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_completion: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class ImportanceEvent(TimestampMixin, Base):
    __tablename__ = "importance_events"
    __table_args__ = (Index("ix_importance_chat_run", "chat_id", "digest_run_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("chats.id", ondelete="CASCADE"), index=True
    )
    digest_run_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("digest_runs.id", ondelete="SET NULL"), nullable=True
    )
    message_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("messages.id", ondelete="SET NULL"), nullable=True
    )
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    importance_type: Mapped[ImportanceType] = mapped_column(
        _enum(ImportanceType, "importance_type")
    )
    summary: Mapped[str] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, server_default=sa_text("0.0"))
    related_message_ids: Mapped[list[int] | None] = mapped_column(JSONB, nullable=True)


class Digest(TimestampMixin, Base):
    __tablename__ = "digests"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    digest_run_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("digest_runs.id", ondelete="CASCADE"), unique=True
    )
    chat_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("chats.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(Text)
    period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    structured: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    body_markdown: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_empty: Mapped[bool] = mapped_column(Boolean, server_default=sa_text("false"))
    sent: Mapped[bool] = mapped_column(Boolean, server_default=sa_text("false"))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    target_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class Prompt(TimestampMixin, Base):
    __tablename__ = "prompts"
    __table_args__ = (
        UniqueConstraint("scope", "version", name="uq_prompts_scope_version"),
        Index(
            "uq_prompts_active_scope",
            "scope",
            unique=True,
            postgresql_where=sa_text("is_active"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    scope: Mapped[PromptScope] = mapped_column(_enum(PromptScope, "prompt_scope"))
    version: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=sa_text("false"))


class ProcessingError(TimestampMixin, Base):
    __tablename__ = "processing_errors"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    chat_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("chats.id", ondelete="SET NULL"), nullable=True, index=True
    )
    digest_run_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("digest_runs.id", ondelete="SET NULL"), nullable=True
    )
    stage: Mapped[ErrorStage] = mapped_column(_enum(ErrorStage, "error_stage"))
    error_type: Mapped[str] = mapped_column(Text)
    message: Mapped[str] = mapped_column(Text)
    traceback: Mapped[str | None] = mapped_column(Text, nullable=True)
    context: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
