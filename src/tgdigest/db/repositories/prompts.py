"""Versioned global prompts (system / digest / stage instructions)."""

from __future__ import annotations

import hashlib

from sqlalchemy import func, select, update

from tgdigest.db.enums import PromptScope
from tgdigest.db.models import Prompt
from tgdigest.db.repositories.base import BaseRepository


def _hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


class PromptRepository(BaseRepository):
    async def get_active(self, scope: PromptScope) -> Prompt | None:
        stmt = select(Prompt).where(Prompt.scope == scope, Prompt.is_active.is_(True))
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_active_content(self, scope: PromptScope) -> str | None:
        prompt = await self.get_active(scope)
        return prompt.content if prompt else None

    async def _next_version(self, scope: PromptScope) -> int:
        stmt = select(func.max(Prompt.version)).where(Prompt.scope == scope)
        return ((await self.session.execute(stmt)).scalar() or 0) + 1

    async def set_active(self, scope: PromptScope, content: str) -> Prompt:
        """Create a new active version (deactivating the previous one)."""
        await self.session.execute(
            update(Prompt)
            .where(Prompt.scope == scope, Prompt.is_active.is_(True))
            .values(is_active=False)
        )
        prompt = Prompt(
            scope=scope,
            version=await self._next_version(scope),
            content=content,
            content_hash=_hash(content),
            is_active=True,
        )
        self.session.add(prompt)
        await self.session.flush()
        return prompt

    async def seed_if_absent(self, scope: PromptScope, content: str) -> bool:
        """Insert the default prompt only if the scope has no versions yet."""
        exists = (
            await self.session.execute(select(Prompt.id).where(Prompt.scope == scope).limit(1))
        ).first()
        if exists:
            return False
        await self.set_active(scope, content)
        return True
