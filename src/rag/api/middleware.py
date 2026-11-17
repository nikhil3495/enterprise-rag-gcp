"""Request-ID + Cloud Trace correlation middleware (pure ASGI, so contextvars propagate)."""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from rag.observability import Timer, request_id_var, trace_resource, trace_var

log = logging.getLogger("rag.http")
_RID_RE = re.compile(r"^[A-Za-z0-9._\-]{1,128}$")


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp, project: str = "") -> None:
        self.app = app
        self.project = project

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        incoming = headers.get("x-request-id", "")
        rid = incoming if _RID_RE.match(incoming) else uuid.uuid4().hex
        rid_token = request_id_var.set(rid)
        trace_token = trace_var.set(trace_resource(headers.get("x-cloud-trace-context"), self.project))
        scope.setdefault("state", {})["request_id"] = rid
        timer = Timer()
        status_code = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                hdrs: list[Any] = list(message.get("headers", []))
                hdrs.append((b"x-request-id", rid.encode("latin-1")))
                message["headers"] = hdrs
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            if scope.get("path") not in ("/healthz", "/readyz"):
                log.info(
                    "http_request",
                    extra={
                        "method": scope.get("method"),
                        "path": scope.get("path"),
                        "status": status_code,
                        "latency_ms": round(timer.ms, 1),
                    },
                )
            request_id_var.reset(rid_token)
            trace_var.reset(trace_token)
