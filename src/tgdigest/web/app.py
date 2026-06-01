"""FastAPI application factory for the admin UI.

DB-only: the web app never opens a Telethon client. Telegram-dependent data
(dialog picker) comes from the daemon-maintained ``dialogs`` cache, and
on-demand digests are delegated to the daemon via ``digest_requests``.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from tgdigest.container import Container
from tgdigest.logging import get_logger
from tgdigest.web import routes

_TEMPLATES_DIR = Path(__file__).parent / "templates"
_log = get_logger("web")
_SESSION_MAX_AGE = 14 * 24 * 3600


def create_app(container: Container | None = None) -> FastAPI:
    container = container or Container()
    app = FastAPI(title="TelegramDigest Admin", docs_url=None, redoc_url=None)
    app.state.container = container
    app.state.templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
    app.add_middleware(
        SessionMiddleware,
        secret_key=container.settings.web.secret_key,
        same_site="lax",
        max_age=_SESSION_MAX_AGE,
    )
    routes.register(app)
    _log.info("web_app_created")
    return app
