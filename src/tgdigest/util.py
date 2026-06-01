"""Small cross-cutting helpers."""

from __future__ import annotations

from datetime import UTC, datetime


def utcnow() -> datetime:
    """Timezone-aware current time in UTC (DB columns are ``timestamptz``)."""
    return datetime.now(UTC)
