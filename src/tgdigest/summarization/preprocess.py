"""Local message preprocessing — runs before any LLM call.

Collapses the raw window into a compact list of conversation blocks by:
dropping service/low-information media and trivial one-word noise,
deduplicating repeats, merging consecutive messages from the same author, and
segmenting into time-based threads. For busy chats this typically shrinks the
payload 5-20x, cutting cost and sharpening the analysis.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from tgdigest.config.settings import PreprocessSettings
from tgdigest.db.models import Message

_LINK_RE = re.compile(r"(https?://\S+|t\.me/\S+|@[\w]{4,})", re.IGNORECASE)
_NON_WORD_RE = re.compile(r"[^\w]+", re.UNICODE)

# Media we cannot read as text; without a caption it is noise for analysis.
_LOW_INFO_MEDIA = {"photo", "video", "sticker", "gif", "voice", "video_note", "audio"}


@dataclass(slots=True)
class Block:
    ref: int  # telegram_message_id of the first source message (citation handle)
    sender: str | None
    timestamp: datetime
    text: str
    segment: int
    reply_to: int | None
    links: list[str]
    reactions: str | None
    repeat_count: int
    media: str | None
    source_ids: list[int] = field(default_factory=list)


@dataclass(slots=True)
class PreprocessResult:
    blocks: list[Block]
    raw_count: int
    known_refs: set[int]

    @property
    def block_count(self) -> int:
        return len(self.blocks)


def normalize(text: str) -> str:
    return _NON_WORD_RE.sub("", text).lower().strip()


def extract_links(text: str) -> list[str]:
    return _LINK_RE.findall(text)


def summarize_reactions(reactions: list[dict[str, Any]] | None, *, top: int = 4) -> str | None:
    if not reactions:
        return None
    ordered = sorted(reactions, key=lambda r: r.get("count", 0), reverse=True)[:top]
    parts = [f"{r.get('emoji', '?')}{r.get('count', 0)}" for r in ordered]
    return " ".join(parts) or None


def _is_trivial(
    content: str, links: list[str], media: str | None, settings: PreprocessSettings
) -> bool:
    raw = content.strip().lower()
    norm = normalize(content)
    if not norm:
        return True  # emoji / punctuation only
    if raw in settings.trivial_token_set or norm in settings.trivial_token_set:
        return True
    return len(norm) < settings.min_meaningful_len and not links and media is None


def _proto_block(message: Message, settings: PreprocessSettings) -> Block | None:
    media = message.media_type.value if message.media_type else None
    has_caption = bool((message.text or message.media_caption or "").strip())
    if media in _LOW_INFO_MEDIA and not has_caption:
        return None

    content = (message.text or message.media_caption or "").strip()
    links = extract_links(content)
    if not content and media:
        content = f"[{media}]"
    if not content or _is_trivial(content, links, media, settings):
        return None

    return Block(
        ref=message.telegram_message_id,
        sender=message.sender_name,
        timestamp=message.date,
        text=content,
        segment=0,
        reply_to=message.reply_to_message_id,
        links=links,
        reactions=summarize_reactions(message.reactions),
        repeat_count=1,
        media=media,
        source_ids=[message.telegram_message_id],
    )


def preprocess(messages: list[Message], settings: PreprocessSettings) -> PreprocessResult:
    raw_count = len(messages)

    protos = [b for m in messages if not m.is_service if (b := _proto_block(m, settings))]

    # Deduplicate identical normalized text, counting repeats on the kept block.
    deduped: list[Block] = []
    seen: dict[str, Block] = {}
    for block in protos:
        key = normalize(block.text)
        if key and key in seen:
            seen[key].repeat_count += 1
            continue
        if key:
            seen[key] = block
        deduped.append(block)

    # Segment into time-based threads.
    thread_gap = timedelta(minutes=settings.thread_gap_minutes)
    segment = 0
    prev_ts: datetime | None = None
    for block in deduped:
        if prev_ts is not None and block.timestamp - prev_ts > thread_gap:
            segment += 1
        block.segment = segment
        prev_ts = block.timestamp

    # Merge consecutive messages from the same author within the merge gap.
    merge_gap = timedelta(seconds=settings.merge_gap_seconds)
    merged: list[Block] = []
    tail_last_ts: datetime | None = None
    for block in deduped:
        if merged and tail_last_ts is not None:
            tail = merged[-1]
            mergeable = (
                block.sender is not None
                and tail.sender == block.sender
                and tail.segment == block.segment
                and block.reply_to is None
                and block.timestamp - tail_last_ts <= merge_gap
            )
            if mergeable:
                tail.text = f"{tail.text}\n{block.text}"
                tail.source_ids.extend(block.source_ids)
                tail.links.extend(block.links)
                tail.repeat_count = max(tail.repeat_count, block.repeat_count)
                tail_last_ts = block.timestamp
                continue
        merged.append(block)
        tail_last_ts = block.timestamp

    for block in merged:
        block.links = list(dict.fromkeys(block.links))  # de-dupe, keep order

    return PreprocessResult(
        blocks=merged,
        raw_count=raw_count,
        known_refs={b.ref for b in merged},
    )


def serialize_blocks(blocks: list[Block]) -> str:
    """Render blocks into a compact transcript for the stage-1 prompt."""
    lines: list[str] = []
    current_segment: int | None = None
    for block in blocks:
        if block.segment != current_segment:
            current_segment = block.segment
            lines.append(f"--- тред {block.segment + 1} ---")
        time = block.timestamp.strftime("%H:%M")
        who = block.sender or "?"
        reply = f" ↪#{block.reply_to}" if block.reply_to else ""
        repeat = f" (×{block.repeat_count})" if block.repeat_count > 1 else ""
        reactions = f" [{block.reactions}]" if block.reactions else ""
        media = f" [{block.media}]" if block.media and not block.text.startswith("[") else ""
        text = block.text.replace("\n", " ⏎ ")
        lines.append(f"#{block.ref} {time} {who}{reply}: {text}{media}{repeat}{reactions}")
    return "\n".join(lines)
