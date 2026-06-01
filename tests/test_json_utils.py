from __future__ import annotations

import pytest
from pydantic import BaseModel

from tgdigest.llm.json_utils import JSONExtractionError, extract_json, parse_model


def test_extract_plain():
    assert extract_json('{"a": 1}') == {"a": 1}


def test_extract_code_fenced():
    assert extract_json('```json\n{"a": 2}\n```') == {"a": 2}


def test_extract_wrapped_in_prose():
    assert extract_json('Результат:\n```json\n[{"x": 1}]\n```\nготово') == [{"x": 1}]


def test_extract_handles_braces_in_strings():
    assert extract_json('noise {"s": "has } brace", "n": [1, 2]} tail') == {
        "s": "has } brace",
        "n": [1, 2],
    }


def test_extract_raises_when_absent():
    with pytest.raises(JSONExtractionError):
        extract_json("no json here at all")


class _Model(BaseModel):
    a: int


def test_parse_model_valid_and_invalid():
    assert parse_model('{"a": 5}', _Model).a == 5
    with pytest.raises(JSONExtractionError):
        parse_model('{"a": "not-int"}', _Model)
