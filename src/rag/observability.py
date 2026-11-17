"""Structured JSON logging compatible with Cloud Logging.

Cloud Run forwards stdout lines; JSON lines with ``severity``, ``message`` and
``logging.googleapis.com/trace`` are parsed into structured entries correlated with Cloud Trace.
"""

from __future__ import annotations

import json
import logging
import sys
import time
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any, TextIO

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
trace_var: ContextVar[str | None] = ContextVar("trace", default=None)

_RESERVED = set(vars(logging.LogRecord("", 0, "", 0, "", (), None))) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "severity": record.levelname,
            "message": record.getMessage(),
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "logger": record.name,
        }
        rid = request_id_var.get()
        if rid:
            payload["request_id"] = rid
        trace = trace_var.get()
        if trace:
            payload["logging.googleapis.com/trace"] = trace
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


_configured = False


def configure_logging(level: str = "INFO", stream: TextIO | None = None) -> None:
    global _configured
    root = logging.getLogger()
    root.setLevel(level.upper())
    if _configured:
        return
    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.handlers = [handler]
    for noisy in ("httpx", "httpcore", "google", "urllib3", "grpc", "sqlglot"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    _configured = True


def trace_resource(header: str | None, project: str) -> str | None:
    """Convert ``X-Cloud-Trace-Context: TRACE_ID/SPAN;o=1`` into a Cloud Logging trace resource."""
    if not header or not project:
        return None
    trace_id = header.split("/", 1)[0].strip()
    return f"projects/{project}/traces/{trace_id}" if trace_id else None


class Timer:
    def __init__(self) -> None:
        self.start = time.perf_counter()

    @property
    def ms(self) -> float:
        return (time.perf_counter() - self.start) * 1000.0
