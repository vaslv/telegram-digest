"""Pydantic schemas for the two LLM stages.

Both stages return JSON *objects* (never bare arrays) so the Claude prefill
trick works uniformly. Validators are lenient: unknown importance types fall
back to ``other`` and confidence is clamped, so a slightly-off model response
is still usable rather than discarded.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from tgdigest.db.enums import ImportanceType

_VALID_TYPES = {t.value for t in ImportanceType}


class RawEvent(BaseModel):
    message_id: int
    importance_type: str = ImportanceType.other.value
    summary: str
    reason: str | None = None
    confidence: float = 0.5
    related_message_ids: list[int] = Field(default_factory=list)

    @field_validator("importance_type", mode="before")
    @classmethod
    def _coerce_type(cls, value: object) -> str:
        text = str(value).strip().lower()
        return text if text in _VALID_TYPES else ImportanceType.other.value

    @field_validator("confidence", mode="before")
    @classmethod
    def _clamp_confidence(cls, value: object) -> float:
        try:
            number = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return 0.5
        return max(0.0, min(1.0, number))

    @field_validator("related_message_ids", mode="before")
    @classmethod
    def _none_to_list(cls, value: object) -> object:
        return value or []


class Stage1Output(BaseModel):
    events: list[RawEvent] = Field(default_factory=list)


class DigestContent(BaseModel):
    summary: str = ""
    key_events: list[str] = Field(default_factory=list)
    attention: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    links: list[str] = Field(default_factory=list)
    conclusion: str = ""

    @field_validator("key_events", "attention", "open_questions", "links", mode="before")
    @classmethod
    def _none_to_list(cls, value: object) -> object:
        return value or []

    @field_validator("summary", "conclusion", mode="before")
    @classmethod
    def _none_to_str(cls, value: object) -> object:
        return value or ""

    def is_meaningful(self) -> bool:
        return bool(
            self.summary or self.key_events or self.attention or self.open_questions
        )
