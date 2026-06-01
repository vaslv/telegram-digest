"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-06-01

The initial schema is created directly from the ORM metadata so it always
matches the models exactly (including native enum types, indexes and the
partial unique index on active prompts). This requires an online (live
connection) migration run, which is how the container entrypoint applies it.
Subsequent migrations are ordinary, autogenerate-friendly Alembic revisions.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from tgdigest.db.base import Base
from tgdigest.db import models  # noqa: F401  (populate metadata)

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())
