"""The shared state of the web interface and how a route gets hold of it."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date
from http import HTTPStatus
from typing import Annotated, cast

from fastapi import Depends, HTTPException, Request, Response
from fastapi.templating import Jinja2Templates

from ..briefing import Briefing, build_briefing
from ..jobs import JobRunner, Scheduler
from ..models import Status
from ..service import MailAgentService
from ..storage import MailRepository, MessageDetail

MESSAGE_NOT_FOUND = "Nachricht nicht gefunden"


@dataclass(frozen=True)
class WebContext:
    """Everything the routes share; created once by ``create_app`` and kept on ``app.state``."""

    service: MailAgentService
    jobs: JobRunner
    schedule: Scheduler
    templates: Jinja2Templates
    on_quit: Callable[[], None] | None = None
    """Stops the web server; ``None`` when there is nothing to stop (tests, plain console start)."""

    @property
    def repository(self) -> MailRepository:
        """The database with messages, corrections and fetch runs."""
        return self.service.repository

    def render(self, request: Request, name: str, data: Mapping[str, object] | None = None) -> Response:
        """Renders the page template ``name`` with ``data``."""
        return self.templates.TemplateResponse(request, name, dict(data or {}))

    def message(self, message_id: int) -> MessageDetail:
        """Returns a stored message or ends the request with "404 Nachricht nicht gefunden"."""
        message = self.repository.messages.get(message_id)
        if message is None:
            raise HTTPException(HTTPStatus.NOT_FOUND, MESSAGE_NOT_FOUND)
        return message

    def briefing(self) -> Briefing:
        """Returns today's briefing, built from all open messages."""
        workflow = self.service.workflow
        return build_briefing(
            self.repository.messages.details(status=Status.OPEN),
            self.service.senders,
            date.today(),
            horizon_days=workflow.briefing_days,
            follow_up_days=workflow.follow_up_days,
        )


def get_context(request: Request) -> WebContext:
    """Returns the context that ``create_app`` stored on the application (FastAPI dependency)."""
    return cast(WebContext, request.app.state.context)


Context = Annotated[WebContext, Depends(get_context)]
"""Parameter type of a route that needs the shared :class:`WebContext`."""
