"""Protection of the local web interface against other websites.

The agent listens only on ``127.0.0.1``, but any website open in the same browser could still send
requests to it. Three layers keep that harmless:

* only ``localhost``/``127.0.0.1`` host headers are accepted, which blocks DNS rebinding;
* state-changing requests must come from the same origin, which blocks cross-site request forgery;
* a strict Content-Security-Policy without inline scripts; Jinja escapes all mail content anyway.
"""

from http import HTTPStatus

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

ALLOWED_HOSTS = ["127.0.0.1", "localhost"]
"""Host headers the agent answers to (``TrustedHostMiddleware``)."""
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
"""Methods that never change data and therefore need no origin check."""
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
        # blob: for the read-aloud audio that the page fetched from /vorlesen
        "media-src 'self' blob:; "
        "object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Permissions-Policy": "microphone=(self), camera=(), geolocation=()",
    # "no-referrer" would make browsers send "Origin: null" on our own form posts
    "Referrer-Policy": "same-origin",
    "Cache-Control": "no-store",
}
"""Headers added to every response unless a route set its own value."""
PDF_POLICY = (
    "default-src 'none'; object-src 'self'; frame-ancestors 'self'; base-uri 'none'; form-action 'none'"
)
"""Policy for PDF downloads: the browser's PDF viewer is a plugin and needs ``object-src``."""
FOREIGN_ORIGIN_TEXT = "Anfrage von fremder Herkunft abgelehnt."


def is_same_origin(request: Request) -> bool:
    """Checks whether a request comes from a page of the agent itself.

    Browsers send ``Sec-Fetch-Site``; older ones send only ``Origin``. Programs such as ``einrichten.ps1``
    send neither and are allowed, because a website cannot strip both headers.

    Args:
        request: The incoming request.

    Returns:
        ``True`` for the agent's own pages, typed addresses and non-browser clients.
    """
    fetch_site = request.headers.get("sec-fetch-site")
    if fetch_site is not None:
        return fetch_site in {"same-origin", "none"}  # "none": started by the user, e.g. a typed address
    origin = request.headers.get("origin")
    return origin is None or origin == f"{request.url.scheme}://{request.headers.get('host')}"


class SameOriginMiddleware(BaseHTTPMiddleware):
    """Rejects state-changing requests from other websites and adds the security headers."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        """Checks the origin, runs the route and completes the security headers of its response."""
        if request.method in SAFE_METHODS or is_same_origin(request):
            response = await call_next(request)
        else:
            response = Response(FOREIGN_ORIGIN_TEXT, status_code=HTTPStatus.FORBIDDEN)
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        return response
