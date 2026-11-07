"""Stopping the agent from the web interface ("⏻ Beenden")."""

import threading
from collections.abc import Callable
from http import HTTPStatus
from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse

from ...jobs import running_text
from ...security.redaction import get_logger
from ..context import Context

router = APIRouter()
_log = get_logger("web")

QUIT_DELAY_SECONDS = 1.0
"""After "Beenden" the server stops this much later, so the goodbye page and its stylesheet still load."""


@router.post("/beenden", response_class=HTMLResponse)
def quit_agent(request: Request, context: Context, trotzdem: Annotated[str, Form()] = "") -> Response:
    """Stops the agent; while a job runs, a page asks first ("Trotzdem beenden")."""
    if context.on_quit is None:
        raise HTTPException(HTTPStatus.NOT_FOUND)
    running = running_text(context.jobs.status())
    if running is not None and trotzdem != "1":
        return context.render(request, "beenden.html", {"running": running})
    _log.info("Beenden über die Weboberfläche")
    stop_soon(context.on_quit)
    return context.render(request, "beendet.html")


def stop_soon(on_quit: Callable[[], None]) -> None:
    """Calls ``on_quit`` after :data:`QUIT_DELAY_SECONDS` in a thread that never blocks the exit."""
    timer = threading.Timer(QUIT_DELAY_SECONDS, on_quit)
    timer.daemon = True
    timer.start()
