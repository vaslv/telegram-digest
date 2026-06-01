"""Telethon client lifecycle: session management, login and authorized connect."""

from __future__ import annotations

import getpass

from telethon import TelegramClient
from telethon.sessions import StringSession

from tgdigest.config.settings import TelegramSettings
from tgdigest.logging import get_logger

_log = get_logger("telegram.client")


def export_string_session(client: TelegramClient) -> str:
    """Serialize the current authorized session to a portable StringSession."""
    session = client.session
    string = StringSession()
    string.set_dc(session.dc_id, session.server_address, session.port)
    string.auth_key = session.auth_key
    return string.save()


class TelegramClientManager:
    """Owns a single Telethon client built from settings."""

    def __init__(self, settings: TelegramSettings, *, in_memory: bool = False) -> None:
        self._settings = settings
        session: StringSession | str
        if in_memory:
            session = StringSession()
        elif settings.string_session:
            session = StringSession(settings.string_session)
        else:
            session = settings.session_path
        self._client = TelegramClient(
            session,
            settings.api_id,
            settings.api_hash,
            flood_sleep_threshold=60,
            auto_reconnect=True,
            retry_delay=5,
        )

    @property
    def client(self) -> TelegramClient:
        return self._client

    async def connect_authorized(self) -> TelegramClient:
        """Connect for the daemon; fail fast if the session is not authorized."""
        await self._client.connect()
        if not await self._client.is_user_authorized():
            raise RuntimeError(
                "Telegram session is not authorized. Run `tgdigest login` first "
                "(or set TG_STRING_SESSION)."
            )
        me = await self._client.get_me()
        _log.info("telegram_connected", user_id=getattr(me, "id", None))
        return self._client

    async def login_interactive(self, *, print_string: bool = False) -> None:
        """Interactive authorization (phone code, optional 2FA)."""
        await self._client.start(
            phone=self._settings.phone or (lambda: input("Phone (international format): ")),
            password=self._settings.two_fa_password
            or (lambda: getpass.getpass("2FA password (cloud password): ")),
        )
        me = await self._client.get_me()
        _log.info("telegram_login_ok", user_id=getattr(me, "id", None))
        if print_string:
            print("\nStringSession (store as TG_STRING_SESSION to skip interactive login):")
            print(export_string_session(self._client))

    async def disconnect(self) -> None:
        coro = self._client.disconnect()
        if coro is not None:
            await coro
