"""Centralized logging configuration — one place to control all output.

Supports two modes via the ``AE_LOG_FORMAT`` environment variable:

    ``text`` (default)
        Human-readable output for local development and dashboard use.
        Includes timestamp, level, logger name, and message.

    ``json``
        Structured JSON-lines output for log aggregation services
        (Datadog, Splunk, CloudWatch, ELK).  Each line is a self-contained
        JSON object with ``timestamp``, ``level``, ``logger``, ``message``,
        and any ``extra`` fields passed via ``logger.info("msg", extra={...})``.

Usage
-----
Call ``configure_logging()`` once at startup (before any logger calls).
The flow entry point does this automatically.

``AE_LOG_LEVEL`` sets the root level (default: ``INFO``).

Why not use Python's dictConfig/fileConfig?
    Those require a static dict or INI file.  We need exactly two modes
    and one environment variable.  A tiny module is simpler than maintaining
    a logging config file that nobody will read.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone


class _JsonFormatter(logging.Formatter):
    """Emit each log record as a single JSON line.

    Merges ``record.extra`` (if present) into the top-level object so
    structured fields like ``run_id``, ``stage``, and ``cache_hit`` are
    queryable in log aggregation tools.
    """

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Merge any structured extra fields the caller passed
        if hasattr(record, "extra") and isinstance(record.extra, dict):
            entry.update(record.extra)

        # Include exception info if present
        if record.exc_info and record.exc_info[0] is not None:
            entry["exception"] = self.formatException(record.exc_info)

        return json.dumps(entry, default=str)


_TEXT_FORMAT = "%(asctime)s [%(levelname)-8s] %(name)s: %(message)s"
_TEXT_DATEFMT = "%Y-%m-%d %H:%M:%S"


def configure_logging() -> None:
    """Set up root logger based on ``AE_LOG_FORMAT`` and ``AE_LOG_LEVEL``.

    Safe to call multiple times — clears existing handlers first.
    """
    log_format = os.environ.get("AE_LOG_FORMAT", "text").strip().lower()
    log_level = os.environ.get("AE_LOG_LEVEL", "INFO").strip().upper()

    root = logging.getLogger()
    root.setLevel(getattr(logging, log_level, logging.INFO))

    # Remove any existing handlers (prevents duplicate output on re-call)
    root.handlers.clear()

    handler = logging.StreamHandler(sys.stderr)

    if log_format == "json":
        handler.setFormatter(_JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter(_TEXT_FORMAT, datefmt=_TEXT_DATEFMT))

    root.addHandler(handler)

    # Quiet down noisy third-party loggers
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("prefect").setLevel(logging.WARNING)
