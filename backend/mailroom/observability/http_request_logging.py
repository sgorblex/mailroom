from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from mailroom.observability.request_context import get_or_create_request_id, request_id_ctx

if TYPE_CHECKING:
    from starlette.requests import Request
    from starlette.responses import Response
    from starlette.types import ASGIApp

REQUEST_ID_HEADER = "X-Request-ID"

logger = logging.getLogger("mailroom.http")


class CorrelationLoggingMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        raw = request.headers.get(REQUEST_ID_HEADER.lower())
        request_id = get_or_create_request_id(raw)
        token = request_id_ctx.set(request_id)
        start = time.perf_counter()
        try:
            response = await call_next(request)
            response.headers[REQUEST_ID_HEADER] = request_id
            duration_ms = round((time.perf_counter() - start) * 1000, 3)
            logger.info(
                "request_completed",
                extra={
                    "event": "http_request",
                    "http_method": request.method,
                    "http_path": request.url.path,
                    "http_status_code": response.status_code,
                    "duration_ms": duration_ms,
                },
            )
            return response
        except Exception:
            duration_ms = round((time.perf_counter() - start) * 1000, 3)
            logger.exception(
                "request_failed",
                extra={
                    "event": "http_request",
                    "http_method": request.method,
                    "http_path": request.url.path,
                    "http_status_code": 500,
                    "duration_ms": duration_ms,
                },
            )
            raise
        finally:
            request_id_ctx.reset(token)
