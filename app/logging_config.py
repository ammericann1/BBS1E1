"""
Structured logging module.

Provides a consistent JSON-structured logger with sensitive-data redaction.
"""

from __future__ import annotations

import logging
import json
import re
import sys
from typing import Any


# Keys/patterns that must be redacted
_SENSITIVE_PATTERNS = re.compile(
    r"(access_token|api_secret|api_key|password|secret|private_key|credentials)",
    re.IGNORECASE,
)


def _redact(value: Any) -> Any:
    """Recursively redact sensitive fields from dicts."""
    if isinstance(value, dict):
        return {
            k: ("***REDACTED***" if _SENSITIVE_PATTERNS.search(k) else _redact(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


class StructuredFormatter(logging.Formatter):
    """Emit log records as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "ts": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        # Merge extra structured fields
        extras = getattr(record, "_structured", None)
        if extras and isinstance(extras, dict):
            entry.update(_redact(extras))

        if record.exc_info and record.exc_info[1]:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


class StructuredLogger(logging.LoggerAdapter):
    """Logger adapter that attaches structured fields to each record."""

    def process(self, msg: str, kwargs: Any) -> tuple[str, Any]:
        extra = kwargs.get("extra", {})
        extra["_structured"] = {**self.extra, **extra.get("_structured", {})}
        kwargs["extra"] = extra
        return msg, kwargs

    def with_fields(self, **fields: Any) -> "StructuredLogger":
        merged = {**self.extra, **fields}
        return StructuredLogger(self.logger, merged)


def get_logger(name: str, **initial_fields: Any) -> StructuredLogger:
    """Return a structured logger bound to the given name."""
    logger = logging.getLogger(name)
    return StructuredLogger(logger, initial_fields)


def setup_logging(level: str = "INFO") -> None:
    """Configure root logging with structured JSON output to stdout/stderr."""
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    # Remove any existing handlers to avoid duplicates
    root.handlers.clear()

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(StructuredFormatter())
    root.addHandler(handler)

    # Quieten noisy libraries
    for lib in ("urllib3", "googleapiclient", "google.auth"):
        logging.getLogger(lib).setLevel(logging.WARNING)
