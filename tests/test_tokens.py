from __future__ import annotations

from tgdigest.llm.tokens import chunk_by_budget, context_for, estimate_tokens


def test_context_for_prefix_matching():
    assert context_for("llama3.1:8b") == 128_000
    assert context_for("claude-haiku-4-5-20251001") == 200_000
    assert context_for("qwen2.5:14b") == 32_768
    assert context_for("totally-unknown") == 8_192


def test_context_for_override_wins():
    assert context_for("gpt-4o-mini", override=4096) == 4096


def test_estimate_tokens():
    assert estimate_tokens("") == 0
    assert estimate_tokens("привет мир, это тест") > 0


def test_chunk_by_budget_greedy():
    chunks = chunk_by_budget(list(range(10)), budget=3, size_of=lambda _: 1)
    assert [len(c) for c in chunks] == [3, 3, 3, 1]


def test_chunk_by_budget_oversize_item_alone():
    chunks = chunk_by_budget([1, 5, 1], budget=3, size_of=lambda x: x)
    assert chunks == [[1], [5], [1]]
