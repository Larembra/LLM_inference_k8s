"""Приватный аудит-лог для vLLM.

Содержит:
  * RequestAuditMiddleware — чистый ASGI: пишет факт обращения (метод, путь без query,
    статус, TTFB, длительность, IP клиента). Тело запроса/ответа и заголовки НЕ читаются.
  * ContentRedactionFilter — подключается к логгерам vLLM через logging_config.json:
    вырезает содержимое RequestLogger vLLM (prompt / output) и трейсбеки,
    в которых могут оказаться пользовательские данные.
"""
from __future__ import annotations

import logging
import os
import sys
import time

_audit = logging.getLogger("request_audit")
if not _audit.handlers:
    _handler = logging.StreamHandler(sys.stdout)
    _handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    _audit.addHandler(_handler)
    _audit.setLevel(logging.INFO)
    _audit.propagate = False

_QUIET_PATHS = {"/health", "/ping", "/metrics"}  # пробы не засоряют лог (DEBUG)


class RequestAuditMiddleware:
    def __init__(self, app):
        self.app = app
        _audit.info("request-audit middleware initialized")

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        started = time.perf_counter()
        state = {"status": None, "ttfb": None}

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                state["status"] = message["status"]
                state["ttfb"] = (time.perf_counter() - started) * 1000
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            path = scope.get("path", "-")
            client = (scope.get("client") or ("-",))[0]
            ttfb = state["ttfb"]
            level = logging.DEBUG if path in _QUIET_PATHS else logging.INFO
            _audit.log(
                level,
                "request-audit method=%s path=%s status=%s ttfb_ms=%s duration_ms=%.1f client=%s",
                scope.get("method", "-"), path,
                state["status"] if state["status"] is not None else "aborted",
                f"{ttfb:.1f}" if ttfb is not None else "-",
                (time.perf_counter() - started) * 1000, client,
            )


_CONTENT_LOGGERS = ("vllm.entrypoints.logger",)
_KEEP_ERRORS = os.getenv("AUDIT_KEEP_ERRORS") == "1"  # только для отладки!


class ContentRedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if record.name.startswith(_CONTENT_LOGGERS):
            request_id = record.args[0] if isinstance(record.args, tuple) and record.args else "-"
            record.msg = "vLLM request/response details redacted request_id=%s"
            record.args = (request_id,)
        if record.exc_info and not _KEEP_ERRORS:
            etype = record.exc_info[0].__name__ if record.exc_info[0] else "Exception"
            record.msg = "redacted error log (%s) from %s:%s"
            record.args = (etype, record.name, record.lineno)
            record.exc_info = None
            record.exc_text = None
        return True
