import logging
import sys
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


logger = logging.getLogger("inference.audit")
logger.setLevel(logging.INFO)


def write_log(message: str) -> None:
    print(message, file=sys.stdout, flush=True)


class RequestAuditMiddleware(BaseHTTPMiddleware):
    def __init__(self, app):
        super().__init__(app)
        write_log("request-audit middleware initialized")

    async def dispatch(self, request: Request, call_next) -> Response:
        started = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            elapsed_ms = (time.perf_counter() - started) * 1000
            write_log(
                f"request-audit method={request.method} path={request.url.path} "
                f"status={status_code} duration_ms={elapsed_ms:.1f}"
            )
