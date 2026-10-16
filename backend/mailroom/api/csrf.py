"""Cross-site request refusal for the cookie-authenticated API.

The SPA and the API share one origin and the session cookie is ``SameSite=Strict``; on top of
that, a state-changing request under ``/api/`` that a browser marks as cross-site is refused.
Browsers send ``Sec-Fetch-Site``; without it, ``Origin`` must name this host. A request with
neither (curl, a script) carries no ambient browser credentials and passes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse

if TYPE_CHECKING:
    from starlette.requests import Request
    from starlette.responses import Response

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def is_cross_site(request: Request) -> bool:
    fetch_site = request.headers.get("sec-fetch-site")
    if fetch_site is not None:
        return fetch_site != "same-origin"
    origin = request.headers.get("origin")
    if origin is None:
        return False
    return urlsplit(origin).netloc != request.headers.get("host", "")


class CrossSiteRequestMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if (
            request.method not in _SAFE_METHODS
            and request.url.path.startswith("/api/")
            and is_cross_site(request)
        ):
            return JSONResponse(status_code=403, content={"detail": "Cross-site request refused"})
        return await call_next(request)
