"""Structured logging setup based on structlog.

Production uses JSON output suitable for log shippers; development can switch to
a colourised console renderer via ``LOG_FORMAT=console``. Message bodies must
never be logged at INFO level — only metadata (chat id, run id, counts).
"""

from __future__ import annotations

import logging
import sys

import structlog

from tgdigest.config.settings import AppSettings

# Third-party loggers that are noisy and must be turned down.
_NOISY_LOGGERS = ("telethon", "httpx", "httpcore", "apscheduler", "asyncio")


def configure_logging(settings: AppSettings) -> None:
    level = getattr(logging, settings.log_level.upper(), logging.INFO)

    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)
    shared_processors: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
        timestamper,
    ]

    if settings.log_format == "console":
        renderer: structlog.typing.Processor = structlog.dev.ConsoleRenderer()
    else:
        renderer = structlog.processors.JSONRenderer()

    structlog.configure(
        processors=[*shared_processors, structlog.processors.format_exc_info, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )

    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level)
    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(max(level, logging.WARNING))


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
