"""Web admin routes (server-rendered, HTMX-progressive).

All mutations use plain form POSTs that redirect (303) with a session flash, so
the UI works with or without HTMX. Only DB access here — no Telegram client.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import text

from tgdigest.container import Container
from tgdigest.db.repositories import (
    ChatRepository,
    DialogRepository,
    DigestRepository,
    DigestRequestRepository,
    ErrorRepository,
)
from tgdigest.util import infer_chat_type
from tgdigest.web.auth import require_login


def _c(request: Request) -> Container:
    return request.app.state.container


def _render(request: Request, name: str, **context: Any) -> HTMLResponse:
    templates = request.app.state.templates
    context.setdefault("flashes", request.session.pop("_flash", []))
    context["user"] = request.session.get("user")
    return templates.TemplateResponse(request, name, context)


def _flash(request: Request, message: str) -> None:
    request.session.setdefault("_flash", [])
    request.session["_flash"].append(message)


def _opt_int(value: str) -> int | None:
    value = value.strip()
    return int(value) if value else None


def _opt_float(value: str) -> float | None:
    value = value.strip()
    return float(value) if value else None


def register(app: FastAPI) -> None:
    public = APIRouter()
    admin = APIRouter(dependencies=[Depends(require_login)])

    # ── public ───────────────────────────────────────────────────────────────
    @public.get("/healthz")
    async def healthz(request: Request) -> JSONResponse:
        try:
            async with _c(request).db.session() as session:
                await session.execute(text("SELECT 1"))
        except Exception:  # any failure means unhealthy
            return JSONResponse({"status": "error"}, status_code=500)
        return JSONResponse({"status": "ok"})

    @public.get("/login")
    async def login_get(request: Request) -> HTMLResponse:
        return _render(request, "login.html")

    @public.post("/login")
    async def login_post(request: Request, password: str = Form("")) -> Any:
        cfg = _c(request).settings.web
        if cfg.password and password == cfg.password:
            request.session["user"] = "admin"
            return RedirectResponse("/", status_code=303)
        error = "WEB_PASSWORD не задан на сервере" if not cfg.password else "Неверный пароль"
        return _render(request, "login.html", error=error)

    @public.get("/logout")
    async def logout(request: Request) -> RedirectResponse:
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    # ── dashboard ──────────────────────────────────────────────────────────--
    @admin.get("/")
    async def dashboard(request: Request) -> HTMLResponse:
        container = _c(request)
        async with container.db.session() as session:
            chats = await ChatRepository(session).list_all()
            last_refresh = await DialogRepository(session).last_refresh()
            pending = await DigestRequestRepository(session).pending_count()
            requests = await DigestRequestRepository(session).recent(8)
            runs = await DigestRepository(session).recent_runs_all(8)
        return _render(
            request,
            "index.html",
            chats=chats,
            last_refresh=last_refresh,
            pending=pending,
            requests=requests,
            runs=runs,
        )

    # ── chats ──────────────────────────────────────────────────────────────--
    @admin.get("/chats/{chat_id}")
    async def chat_detail(request: Request, chat_id: int) -> HTMLResponse:
        container = _c(request)
        async with container.db.session() as session:
            repo = ChatRepository(session)
            chat = await repo.get_by_id(chat_id)
            if chat is None:
                raise HTTPException(status_code=404, detail="chat not found")
            state = await repo.get_state(chat_id)
            runs = await DigestRepository(session).recent_runs(chat_id, 10)
        return _render(request, "chat_detail.html", chat=chat, state=state, runs=runs)

    @admin.post("/chats/{chat_id}")
    async def chat_update(
        request: Request,
        chat_id: int,
        interval: str = Form(""),
        min_messages: str = Form(""),
        max_messages: str = Form(""),
        threshold: str = Form(""),
        target: str = Form(""),
        send_empty: bool = Form(False),
        context_prompt: str = Form(""),
        summary_prompt: str = Form(""),
    ) -> RedirectResponse:
        container = _c(request)
        async with container.db.session() as session:
            repo = ChatRepository(session)
            await repo.update_config(
                chat_id,
                summary_interval_minutes=_opt_int(interval),
                min_messages_before_digest=_opt_int(min_messages),
                max_messages_before_digest=_opt_int(max_messages),
                importance_threshold=_opt_float(threshold),
                digest_target_chat_id=_opt_int(target),
                send_empty_digest=send_empty,
            )
            await repo.set_prompts(
                chat_id,
                context=context_prompt or None,
                summary=summary_prompt or None,
            )
        _flash(request, "Настройки сохранены")
        return RedirectResponse(f"/chats/{chat_id}", status_code=303)

    @admin.post("/chats/{chat_id}/toggle")
    async def chat_toggle(request: Request, chat_id: int) -> RedirectResponse:
        container = _c(request)
        async with container.db.session() as session:
            repo = ChatRepository(session)
            chat = await repo.get_by_id(chat_id)
            if chat is not None:
                await repo.set_enabled(chat_id, not chat.enabled)
                _flash(request, f"«{chat.title}»: {'включён' if not chat.enabled else 'отключён'}")
        return RedirectResponse("/", status_code=303)

    @admin.post("/chats/{chat_id}/delete")
    async def chat_delete(request: Request, chat_id: int) -> RedirectResponse:
        container = _c(request)
        async with container.db.session() as session:
            await ChatRepository(session).delete(chat_id)
        _flash(request, "Чат удалён вместе с данными")
        return RedirectResponse("/", status_code=303)

    @admin.post("/chats/{chat_id}/run")
    async def chat_run(
        request: Request, chat_id: int, dry: bool = Form(False)
    ) -> RedirectResponse:
        container = _c(request)
        async with container.db.session() as session:
            req = await DigestRequestRepository(session).enqueue(chat_id, dry_run=dry)
        _flash(request, f"Запрос #{req.id} в очереди — daemon выполнит его в ближайшие секунды")
        return RedirectResponse("/", status_code=303)

    @admin.get("/dialogs")
    async def dialogs(request: Request, q: str = "") -> HTMLResponse:
        container = _c(request)
        async with container.db.session() as session:
            cached = await DialogRepository(session).list(query=q or None, limit=300)
            watched = {c.telegram_chat_id for c in await ChatRepository(session).list_all()}
        return _render(request, "dialogs.html", dialogs=cached, watched=watched, query=q)

    @admin.post("/chats")
    async def chat_add(request: Request, telegram_chat_id: str = Form(...)) -> RedirectResponse:
        container = _c(request)
        ref = telegram_chat_id.strip()
        defaults = container.settings.defaults
        async with container.db.session() as session:
            dialog_repo = DialogRepository(session)
            if ref.lstrip("-").isdigit():
                telegram_id: int | None = int(ref)
                dialog = await dialog_repo.get(int(ref))
            else:
                dialog = await dialog_repo.get_by_username(ref)
                telegram_id = dialog.telegram_chat_id if dialog else None
            if dialog is not None:
                telegram_id = dialog.telegram_chat_id
                title, chat_type, username = dialog.title, dialog.chat_type, dialog.username
            elif telegram_id is not None:
                title, chat_type, username = str(telegram_id), infer_chat_type(telegram_id), None
            else:
                _flash(request, f"«{ref}» не найден в кэше диалогов")
                return RedirectResponse("/dialogs", status_code=303)
            chat = await ChatRepository(session).create_or_update(
                telegram_chat_id=telegram_id,
                title=title,
                chat_type=chat_type,
                username=username,
                enabled=True,
                summary_interval_minutes=defaults.interval_minutes,
                min_messages_before_digest=defaults.min_msgs,
                max_messages_before_digest=defaults.max_msgs,
                importance_threshold=defaults.importance_threshold,
            )
        _flash(request, f"Добавлен «{chat.title}»")
        return RedirectResponse(f"/chats/{chat.id}", status_code=303)

    # ── analytics views ──────────────────────────────────────────────────────
    @admin.get("/digests")
    async def digests(request: Request) -> HTMLResponse:
        container = _c(request)
        async with container.db.session() as session:
            items = await DigestRepository(session).recent_digests(50)
        return _render(request, "digests.html", digests=items)

    @admin.get("/digests/{digest_id}")
    async def digest_detail(request: Request, digest_id: int) -> HTMLResponse:
        container = _c(request)
        async with container.db.session() as session:
            digest = await DigestRepository(session).get_digest(digest_id)
            if digest is None:
                raise HTTPException(status_code=404, detail="digest not found")
        return _render(request, "digest_detail.html", digest=digest)

    @admin.get("/runs")
    async def runs(request: Request) -> HTMLResponse:
        container = _c(request)
        async with container.db.session() as session:
            items = await DigestRepository(session).recent_runs_all(50)
        return _render(request, "runs.html", runs=items)

    @admin.get("/errors")
    async def errors(request: Request) -> HTMLResponse:
        container = _c(request)
        async with container.db.session() as session:
            items = await ErrorRepository(session).recent(50)
        return _render(request, "errors.html", errors=items)

    app.include_router(public)
    app.include_router(admin)
