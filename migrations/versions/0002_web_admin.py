"""web admin: dialogs cache + digest_requests queue

Revision ID: 0002_web_admin
Revises: 0001_initial
Create Date: 2026-06-01

Creates the two tables that let the web UI / CLI operate without a second
Telethon client. Uses metadata create_all (checkfirst) so only the new tables
and the new ``request_status`` enum are created; the existing ``chat_type``
enum is reused.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from tgdigest.db import models  # noqa: F401  (populate metadata)
from tgdigest.db.base import Base

revision: str = "0002_web_admin"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    Base.metadata.create_all(
        bind=op.get_bind(),
        tables=[Base.metadata.tables["dialogs"], Base.metadata.tables["digest_requests"]],
    )


def downgrade() -> None:
    bind = op.get_bind()
    Base.metadata.tables["digest_requests"].drop(bind=bind)
    Base.metadata.tables["dialogs"].drop(bind=bind)
    op.execute("DROP TYPE IF EXISTS request_status")
