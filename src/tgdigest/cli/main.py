"""Command-line interface (Typer).

Each command runs in its own event loop and owns a :class:`Container` that it
closes on exit. Commands that need Telegram open an authorized client via the
``_client`` / ``_maybe_client`` context managers.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import typer
from dateutil import parser as dateparser
from sqlalchemy import text

from tgdigest.app import run_daemon
from tgdigest.container import Container
from tgdigest.db.enums import PromptScope, RunTrigger
from tgdigest.db.models import Chat
from tgdigest.db.repositories import (
    ChatRepository,
    DigestRepository,
    MessageRepository,
    PromptRepository,
)
from tgdigest.summarization.prompts import seed_default_prompts
from tgdigest.telegram.dialogs import list_dialogs as tg_list_dialogs
from tgdigest.telegram.dialogs import resolve_chat

app = typer.Typer(add_completion=False, no_args_is_help=True, help="TelegramDigest CLI")


# ── helpers ──────────────────────────────────────────────────────────────────
@asynccontextmanager
async def _client(container: Container) -> AsyncIterator[object]:
    manager = container.telegram_manager()
    client = await manager.connect_authorized()
    try:
        yield client
    finally:
        await manager.disconnect()


@asynccontextmanager
async def _maybe_client(container: Container, *, needed: bool) -> AsyncIterator[object | None]:
    if not needed:
        yield None
        return
    async with _client(container) as client:
        yield client


def _is_numeric_ref(ref: str) -> bool:
    return ref.strip().lstrip("-").isdigit()


async def _telegram_id(ref: str, client: object | None) -> int:
    ref = ref.strip()
    if _is_numeric_ref(ref):
        return int(ref)
    if client is None:
        raise typer.BadParameter(f"для «{ref}» нужен доступ к Telegram (numeric id не задан)")
    info = await resolve_chat(client, ref)
    return info.telegram_chat_id


async def _chat_by_ref(container: Container, ref: str, client: object | None) -> Chat:
    telegram_id = await _telegram_id(ref, client)
    async with container.db.session() as session:
        chat = await ChatRepository(session).get_by_telegram_id(telegram_id)
    if chat is None:
        typer.secho(f"Чат {ref} не найден среди отслеживаемых. Сначала watch-chat.", fg="red")
        raise typer.Exit(1)
    return chat


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = dateparser.parse(value)
    except (ValueError, OverflowError) as exc:
        raise typer.BadParameter(f"не удалось разобрать дату: {value}") from exc
    if parsed is None:
        raise typer.BadParameter(f"не удалось разобрать дату: {value}")
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed


def _read_value(value: str | None) -> str | None:
    """Return the value, or the contents of a file if it starts with ``@``."""
    if value is None:
        return None
    if value.startswith("@"):
        return Path(value[1:]).read_text(encoding="utf-8")
    return value


def _run(coro: object) -> None:
    asyncio.run(coro)  # type: ignore[arg-type]


# ── auth / daemon ────────────────────────────────────────────────────────────
@app.command()
def login(
    string: bool = typer.Option(
        False, "--string", "-s", help="Авторизоваться во временную сессию и вывести StringSession."
    ),
) -> None:
    """Одноразовая авторизация в Telegram (код из приложения, при необходимости 2FA)."""

    async def _inner() -> None:
        container = Container()
        manager = container.telegram_manager(in_memory=string)
        try:
            await manager.login_interactive(print_string=string)
            typer.secho("Авторизация успешна.", fg="green")
        finally:
            await manager.disconnect()
            await container.aclose()

    _run(_inner())


@app.command()
def run() -> None:
    """Запустить daemon: ингест сообщений + планировщик дайджестов."""

    async def _inner() -> None:
        container = Container()
        try:
            await run_daemon(container)
        except RuntimeError as exc:
            typer.secho(str(exc), fg="red")
            raise typer.Exit(1) from exc
        finally:
            await container.aclose()

    _run(_inner())


# ── chat discovery & configuration ──────────────────────────────────────────
@app.command("list-dialogs")
def list_dialogs(
    limit: int = typer.Option(50, "--limit", "-n"),
    chat_type: str | None = typer.Option(None, "--type", help="group|supergroup|channel|private"),
) -> None:
    """Показать доступные диалоги Telegram."""

    async def _inner() -> None:
        container = Container()
        try:
            async with _client(container) as client:
                dialogs = await tg_list_dialogs(client, limit=limit)
        finally:
            await container.aclose()
        for dialog in dialogs:
            if chat_type and dialog.chat_type.value != chat_type:
                continue
            handle = f" @{dialog.username}" if dialog.username else ""
            typer.echo(
                f"{dialog.telegram_chat_id:>15}  {dialog.chat_type.value:<10} "
                f"unread={dialog.unread:<4} {dialog.title}{handle}"
            )

    _run(_inner())


@app.command("list-chats")
def list_chats() -> None:
    """Показать чаты под мониторингом."""

    async def _inner() -> None:
        container = Container()
        try:
            async with container.db.session() as session:
                chats = await ChatRepository(session).list_all()
        finally:
            await container.aclose()
        if not chats:
            typer.echo("Нет отслеживаемых чатов.")
            return
        for chat in chats:
            status = "on " if chat.enabled else "off"
            interval = chat.summary_interval_minutes or "—"
            typer.echo(
                f"[{status}] {chat.telegram_chat_id:>15}  {chat.title}  "
                f"(интервал={interval}м, min={chat.min_messages_before_digest}, "
                f"max={chat.max_messages_before_digest}, порог={chat.importance_threshold})"
            )

    _run(_inner())


@app.command("watch-chat")
def watch_chat(
    ref: str = typer.Argument(..., help="telegram_chat_id (например -100123…) или @username"),
    interval: int | None = typer.Option(
        None, "--interval", help="минуты; 0 = только по количеству"
    ),
    min_messages: int | None = typer.Option(None, "--min"),
    max_messages: int | None = typer.Option(None, "--max"),
    threshold: float | None = typer.Option(None, "--threshold"),
    target: int | None = typer.Option(None, "--target", help="куда слать дайджест (chat_id)"),
) -> None:
    """Добавить чат в мониторинг (или обновить его настройки)."""

    async def _inner() -> None:
        container = Container()
        defaults = container.settings.defaults
        try:
            async with _client(container) as client:
                resolved = ref if not _is_numeric_ref(ref) else int(ref)
                info = await resolve_chat(client, resolved)
            resolved_interval = defaults.interval_minutes if interval is None else interval
            async with container.db.session() as session:
                chat = await ChatRepository(session).create_or_update(
                    telegram_chat_id=info.telegram_chat_id,
                    title=info.title,
                    chat_type=info.chat_type,
                    username=info.username,
                    enabled=True,
                    summary_interval_minutes=resolved_interval or None,
                    min_messages_before_digest=min_messages or defaults.min_msgs,
                    max_messages_before_digest=max_messages or defaults.max_msgs,
                    importance_threshold=threshold or defaults.importance_threshold,
                    digest_target_chat_id=target,
                )
            typer.secho(
                f"Отслеживаю «{chat.title}» ({chat.telegram_chat_id}).", fg="green"
            )
        finally:
            await container.aclose()

    _run(_inner())


@app.command("unwatch-chat")
def unwatch_chat(
    ref: str = typer.Argument(...),
    purge: bool = typer.Option(False, "--purge", help="удалить чат и все его данные"),
) -> None:
    """Прекратить мониторинг чата (по умолчанию — отключить, сохранив данные)."""

    async def _inner() -> None:
        container = Container()
        try:
            async with _maybe_client(container, needed=not _is_numeric_ref(ref)) as client:
                chat = await _chat_by_ref(container, ref, client)
            async with container.db.session() as session:
                repo = ChatRepository(session)
                if purge:
                    await repo.delete(chat.id)
                    typer.secho(f"Удалён «{chat.title}» и все данные.", fg="yellow")
                else:
                    await repo.set_enabled(chat.id, False)
                    typer.secho(f"Мониторинг «{chat.title}» отключён.", fg="yellow")
        finally:
            await container.aclose()

    _run(_inner())


@app.command("show-chat-config")
def show_chat_config(ref: str = typer.Argument(...)) -> None:
    """Показать конфигурацию, состояние и последние запуски чата."""

    async def _inner() -> None:
        container = Container()
        try:
            async with _maybe_client(container, needed=not _is_numeric_ref(ref)) as client:
                chat = await _chat_by_ref(container, ref, client)
            async with container.db.session() as session:
                repo = ChatRepository(session)
                state = await repo.get_state(chat.id)
                runs = await DigestRepository(session).recent_runs(chat.id)
                unprocessed = await MessageRepository(session).count_after(
                    chat.id, state.last_processed_message_id if state else 0
                )
        finally:
            await container.aclose()

        typer.secho(f"# {chat.title} ({chat.telegram_chat_id})", bold=True)
        typer.echo(f"enabled: {chat.enabled}")
        typer.echo(f"type: {chat.chat_type.value}")
        typer.echo(f"digest_target_chat_id: {chat.digest_target_chat_id or 'Saved Messages'}")
        typer.echo(f"summary_interval_minutes: {chat.summary_interval_minutes}")
        typer.echo(
            f"min/max: {chat.min_messages_before_digest}/{chat.max_messages_before_digest}"
        )
        typer.echo(f"importance_threshold: {chat.importance_threshold}")
        typer.echo(f"send_empty_digest: {chat.send_empty_digest}")
        typer.echo(f"context_prompt: {'задан' if chat.chat_context_prompt else '—'}")
        typer.echo(f"summary_prompt: {'задан' if chat.chat_summary_prompt else '—'}")
        if state:
            typer.echo(
                f"state: seen={state.last_seen_message_id}, "
                f"processed={state.last_processed_message_id}, unprocessed={unprocessed}, "
                f"last_digest={state.last_digest_at}"
            )
        if runs:
            typer.echo("last runs:")
            for run_row in runs:
                typer.echo(
                    f"  #{run_row.id} {run_row.trigger.value:<9} {run_row.status.value:<7} "
                    f"important={run_row.important_count} at {run_row.created_at:%Y-%m-%d %H:%M}"
                )

    _run(_inner())


# ── prompts ──────────────────────────────────────────────────────────────────
@app.command("set-chat-prompt")
def set_chat_prompt(
    ref: str = typer.Argument(...),
    context: str | None = typer.Option(None, "--context", help="текст или @файл — контекст чата"),
    summary: str | None = typer.Option(
        None, "--summary", help="текст или @файл — инструкции дайджеста"
    ),
) -> None:
    """Задать индивидуальные промпты чата (контекст и/или инструкции дайджеста)."""
    if context is None and summary is None:
        raise typer.BadParameter("укажите --context и/или --summary")

    async def _inner() -> None:
        container = Container()
        try:
            async with _maybe_client(container, needed=not _is_numeric_ref(ref)) as client:
                chat = await _chat_by_ref(container, ref, client)
            async with container.db.session() as session:
                await ChatRepository(session).set_prompts(
                    chat.id, context=_read_value(context), summary=_read_value(summary)
                )
            typer.secho(f"Промпты чата «{chat.title}» обновлены.", fg="green")
        finally:
            await container.aclose()

    _run(_inner())


@app.command("set-global-prompt")
def set_global_prompt(
    system: str | None = typer.Option(None, "--system", help="текст или @файл"),
    digest: str | None = typer.Option(None, "--digest", help="текст или @файл"),
    stage1: str | None = typer.Option(None, "--stage1", help="текст или @файл"),
    stage2: str | None = typer.Option(None, "--stage2", help="текст или @файл"),
) -> None:
    """Создать новую активную версию глобального промпта."""
    mapping = {
        PromptScope.global_system: system,
        PromptScope.global_digest: digest,
        PromptScope.stage1_instructions: stage1,
        PromptScope.stage2_instructions: stage2,
    }
    provided = {scope: _read_value(value) for scope, value in mapping.items() if value is not None}
    if not provided:
        raise typer.BadParameter("укажите хотя бы один из --system/--digest/--stage1/--stage2")

    async def _inner() -> None:
        container = Container()
        try:
            async with container.db.session() as session:
                repo = PromptRepository(session)
                for scope, content in provided.items():
                    prompt = await repo.set_active(scope, content)  # type: ignore[arg-type]
                    typer.secho(f"{scope.value} → версия {prompt.version}", fg="green")
        finally:
            await container.aclose()

    _run(_inner())


@app.command("seed-prompts")
def seed_prompts() -> None:
    """Засеять дефолтные глобальные промпты (идемпотентно)."""

    async def _inner() -> None:
        container = Container()
        try:
            async with container.db.session() as session:
                seeded = await seed_default_prompts(PromptRepository(session))
            typer.echo(f"Засеяно промптов: {seeded}")
        finally:
            await container.aclose()

    _run(_inner())


# ── digests ──────────────────────────────────────────────────────────────────
@app.command("run-digest")
def run_digest(
    ref: str = typer.Argument(...),
    since: str | None = typer.Option(None, "--since", help="ISO-дата начала окна"),
    until: str | None = typer.Option(None, "--until", help="ISO-дата конца окна"),
    dry_run: bool = typer.Option(False, "--dry-run", help="не отправлять, только показать"),
) -> None:
    """Сформировать дайджест для чата немедленно."""

    async def _inner() -> None:
        container = Container()
        needs_client = (not dry_run) or (not _is_numeric_ref(ref))
        try:
            async with _maybe_client(container, needed=needs_client) as client:
                chat = await _chat_by_ref(container, ref, client)
                service = container.digest_service(client=client)
                outcome = await service.run(
                    chat.id,
                    trigger=RunTrigger.manual,
                    since=_parse_dt(since),
                    until=_parse_dt(until),
                    dry_run=dry_run,
                    send=not dry_run,
                )
            color = {"success": "green", "empty": "yellow"}.get(outcome.status, "red")
            typer.secho(
                f"[{outcome.status}] {outcome.message} "
                f"(важных: {outcome.important_count}, отправлено: {outcome.sent})",
                fg=color,
            )
            if dry_run and outcome.body_markdown:
                typer.echo("\n" + outcome.body_markdown)
        finally:
            await container.aclose()

    _run(_inner())


@app.command("reprocess-messages")
def reprocess_messages(
    ref: str = typer.Argument(...),
    since: str = typer.Option(..., "--since", help="ISO-дата начала окна"),
    until: str = typer.Option(..., "--until", help="ISO-дата конца окна"),
    no_send: bool = typer.Option(False, "--no-send", help="не отправлять результат"),
) -> None:
    """Повторно проанализировать сообщения за период (например, после смены промптов)."""

    async def _inner() -> None:
        container = Container()
        try:
            needs_client = not no_send or not _is_numeric_ref(ref)
            async with _maybe_client(container, needed=needs_client) as client:
                chat = await _chat_by_ref(container, ref, client)
                service = container.digest_service(client=client)
                outcome = await service.run(
                    chat.id,
                    trigger=RunTrigger.reprocess,
                    since=_parse_dt(since),
                    until=_parse_dt(until),
                    send=not no_send,
                )
            color = {"success": "green", "empty": "yellow"}.get(outcome.status, "red")
            typer.secho(f"[{outcome.status}] {outcome.message}", fg=color)
        finally:
            await container.aclose()

    _run(_inner())


# ── ops ──────────────────────────────────────────────────────────────────────
@app.command()
def healthcheck() -> None:
    """Проверка готовности: доступность БД и наличие сессии Telegram."""

    async def _inner() -> None:
        container = Container()
        ok = True
        try:
            async with container.db.session() as session:
                await session.execute(text("SELECT 1"))
            typer.echo("db: ok")
            tg = container.settings.telegram
            session_file = f"{tg.session_path}.session"
            has_session = bool(tg.string_session) or await asyncio.to_thread(
                os.path.exists, session_file
            )
            typer.echo(f"session: {'present' if has_session else 'missing'}")
            ok = has_session
        except Exception as exc:
            typer.secho(f"db: error: {exc}", fg="red")
            ok = False
        finally:
            await container.aclose()
        if not ok:
            raise typer.Exit(1)

    _run(_inner())


def main() -> None:
    app()


if __name__ == "__main__":
    main()
