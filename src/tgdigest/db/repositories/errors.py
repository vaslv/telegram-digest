"""Processing error journal."""

from __future__ import annotations

from typing import Any

from tgdigest.db.enums import ErrorStage
from tgdigest.db.models import ProcessingError
from tgdigest.db.repositories.base import BaseRepository


class ErrorRepository(BaseRepository):
    async def record(
        self,
        *,
        stage: ErrorStage,
        error_type: str,
        message: str,
        chat_id: int | None = None,
        digest_run_id: int | None = None,
        traceback: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> ProcessingError:
        error = ProcessingError(
            stage=stage,
            error_type=error_type,
            message=message[:4000],
            chat_id=chat_id,
            digest_run_id=digest_run_id,
            traceback=traceback,
            context=context,
        )
        self.session.add(error)
        await self.session.flush()
        return error
