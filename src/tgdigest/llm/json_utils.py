"""Robust JSON extraction and validation for LLM outputs.

Models frequently wrap JSON in code fences or add prose around it. The extractor
strips fences, then performs a string-aware balanced scan to isolate the first
complete JSON object/array before parsing.
"""

from __future__ import annotations

import json

from pydantic import BaseModel, ValidationError


class JSONExtractionError(ValueError):
    """Raised when no valid JSON can be recovered from text."""


def _strip_fences(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    stripped = stripped[3:]
    if stripped[:4].lower() == "json":
        stripped = stripped[4:]
    end = stripped.rfind("```")
    if end != -1:
        stripped = stripped[:end]
    return stripped.strip()


def extract_json(text: str) -> object:
    """Return the first valid JSON value found in ``text``."""
    candidate = _strip_fences(text)
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass

    start = _first_brace(candidate)
    if start < 0:
        raise JSONExtractionError("no JSON object/array found in model output")

    fragment = _balanced_fragment(candidate, start)
    if fragment is None:
        raise JSONExtractionError("unbalanced JSON in model output")
    try:
        return json.loads(fragment)
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise JSONExtractionError(f"invalid JSON fragment: {exc}") from exc


def _first_brace(text: str) -> int:
    for index, char in enumerate(text):
        if char in "{[":
            return index
    return -1


def _balanced_fragment(text: str, start: int) -> str | None:
    stack: list[str] = []
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "{[":
            stack.append(char)
        elif char in "}]":
            if not stack:
                return None
            stack.pop()
            if not stack:
                return text[start : index + 1]
    return None


def parse_model[ModelT: BaseModel](text: str, model_cls: type[ModelT]) -> ModelT:
    """Extract JSON and validate it against ``model_cls``."""
    data = extract_json(text)
    try:
        return model_cls.model_validate(data)
    except ValidationError as exc:
        raise JSONExtractionError(f"schema validation failed: {exc}") from exc
