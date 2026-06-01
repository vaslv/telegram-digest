from __future__ import annotations

from tgdigest.db.enums import PromptScope
from tgdigest.summarization.prompts import PromptBuilder, default_prompts


def _builder() -> PromptBuilder:
    return PromptBuilder(default_prompts(), language="ru")


def test_default_prompts_complete():
    prompts = default_prompts()
    assert set(prompts) == set(PromptScope)
    assert all(text.strip() for text in prompts.values())


def test_stage1_messages_assembly(sample_chat):
    messages = _builder().stage1_messages(sample_chat, "transcript-here")
    assert messages[0].role == "system" and messages[1].role == "user"
    system = messages[0].content
    assert "аналитик" in system  # from global_system
    assert "ЭТАП 1" in system  # from stage1 instructions
    assert sample_chat.title in system  # chat context
    assert "русском" in system  # language note
    assert messages[1].content == "transcript-here"


def test_stage2_includes_chat_summary_prompt(sample_chat):
    sample_chat.chat_summary_prompt = "Особое правило для этого чата"
    messages = _builder().stage2_messages(sample_chat, "events", "период")
    assert "Особое правило для этого чата" in messages[0].content
    assert "период" in messages[1].content


def test_snapshot_has_all_layers(sample_chat):
    snap = _builder().snapshot(sample_chat)
    for scope in PromptScope:
        assert scope.value in snap
    assert snap["language"] == "ru"
