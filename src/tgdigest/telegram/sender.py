"""Send rendered digests back to Telegram via the user account."""

from __future__ import annotations

from functools import partial

from telethon import TelegramClient

from tgdigest.telegram.flood import with_flood_retry

TELEGRAM_LIMIT = 4096
_CHUNK_LIMIT = 4000  # leave headroom for markdown entity expansion


def split_message(text: str, limit: int = _CHUNK_LIMIT) -> list[str]:
    """Split text into Telegram-sized chunks, preferring paragraph boundaries."""
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    current = ""

    def flush() -> None:
        nonlocal current
        if current:
            chunks.append(current)
            current = ""

    for paragraph in text.split("\n\n"):
        if len(paragraph) > limit:
            flush()
            for line in paragraph.split("\n"):
                while len(line) > limit:
                    chunks.append(line[:limit])
                    line = line[limit:]
                if current and len(current) + 1 + len(line) > limit:
                    flush()
                current = f"{current}\n{line}" if current else line
            continue
        sep = "\n\n" if current else ""
        if len(current) + len(sep) + len(paragraph) > limit:
            flush()
            current = paragraph
        else:
            current = f"{current}{sep}{paragraph}"

    flush()
    return chunks


async def send_digest(
    client: TelegramClient,
    target_chat_id: int | None,
    text: str,
    *,
    parse_mode: str = "md",
) -> int:
    """Send ``text`` to the target chat (or Saved Messages). Returns chunk count."""
    destination: int | str = target_chat_id if target_chat_id is not None else "me"
    chunks = split_message(text)
    for chunk in chunks:
        await with_flood_retry(
            partial(
                client.send_message,
                destination,
                chunk,
                parse_mode=parse_mode,
                link_preview=False,
            )
        )
    return len(chunks)
