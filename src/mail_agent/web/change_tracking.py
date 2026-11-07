"""Noticing changes made in the web interface, so the hand-over package follows a minute later."""

from collections.abc import Callable
from http import HTTPStatus

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.types import ASGIApp

NO_CHANGE_PATHS = ("/uebergabe", "/vorlesen", "/einlesen", "/ki-nachholen", "/beenden")
"""Posts that change no data themselves; a background job reports its own changes when it is done."""


def is_data_change(request: Request, response: Response) -> bool:
    """Checks whether a request changed stored data: a successful post outside :data:`NO_CHANGE_PATHS`."""
    return (
        request.method == "POST"
        and response.status_code < HTTPStatus.BAD_REQUEST  # refused or failed requests changed nothing
        and not request.url.path.startswith(NO_CHANGE_PATHS)
    )


class ChangeTrackingMiddleware(BaseHTTPMiddleware):
    """Calls ``on_change`` after every request that changed stored data."""

    def __init__(self, app: ASGIApp, on_change: Callable[[], None]) -> None:
        """Wraps ``app``; ``on_change`` is usually ``MailAgentService.note_change``."""
        super().__init__(app)
        self._on_change = on_change

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        """Runs the route and reports a data change afterwards."""
        response = await call_next(request)
        if is_data_change(request, response):
            self._on_change()
        return response
