"""Structured logging via structlog.

Two output formats:
* human (console) - colourised, easy to scan during development.
* json (production) - machine-readable, suitable for log shippers.

Choose with the ``DOGESHTRADER_LOG_FORMAT`` environment variable
(``human`` | ``json``). Log level via ``DOGESHTRADER_LOG_LEVEL`` (default INFO).
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any

import structlog

_CONFIGURED = False


def configure_logging(
    level: str | None = None,
    fmt: str | None = None,
    log_file: str | None = None,
) -> None:
    """Idempotently configure structlog + stdlib logging."""

    global _CONFIGURED
    if _CONFIGURED:
        return

    level_name = (level or os.environ.get("DOGESHTRADER_LOG_LEVEL") or "INFO").upper()
    fmt = (fmt or os.environ.get("DOGESHTRADER_LOG_FORMAT") or "human").lower()

    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=False)

    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
        timestamper,
    ]

    renderers: list[Any]
    if fmt == "json":
        renderers = [structlog.processors.dict_tracebacks, structlog.processors.JSONRenderer()]
    else:
        renderers = [structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())]

    structlog.configure(
        processors=[*shared_processors, *renderers],
        wrapper_class=structlog.make_filtering_bound_logger(_lvl(level_name)),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )

    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if log_file:
        handlers.append(logging.FileHandler(log_file))

    logging.basicConfig(
        level=_lvl(level_name),
        format="%(message)s",
        handlers=handlers,
        force=True,
    )

    _CONFIGURED = True


def _lvl(name: str) -> int:
    return getattr(logging, name, logging.INFO)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    if not _CONFIGURED:
        configure_logging()
    return structlog.get_logger(name) if name else structlog.get_logger()


__all__ = ["configure_logging", "get_logger"]
