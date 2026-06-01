"""Base repository: a thin wrapper around an :class:`AsyncSession`.

Repositories never commit — the unit-of-work boundary is owned by the caller
via :meth:`tgdigest.db.base.Database.session`. Methods may ``flush`` to obtain
generated primary keys within the surrounding transaction.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession


class BaseRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
