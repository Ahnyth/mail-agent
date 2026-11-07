"""The web interface: assembles security, background work and the routes of all pages.

Each area of the page has its own module under ``routes/``; this module only puts the parts together.
The interface listens on ``127.0.0.1`` only and is protected as described in ``security.py``.
"""

from collections.abc import Callable
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .. import __version__
from ..jobs import JobRunner, Scheduler
from ..service import MailAgentService
from .change_tracking import ChangeTrackingMiddleware
from .context import WebContext
from .lifecycle import BackgroundWork, lifespan_for
from .routes import ROUTERS
from .security import ALLOWED_HOSTS, SameOriginMiddleware
from .templating import build_templates

STATIC_DIR = Path(__file__).parent / "static"
APP_TITLE = "MailAgent"


def create_app(
    service: MailAgentService,
    runner: JobRunner | None = None,
    scheduler: Scheduler | None = None,
    on_quit: Callable[[], None] | None = None,
) -> FastAPI:
    """Builds the web application.

    Args:
        service: The agent with database, analysis and settings.
        runner: Runs fetches and analyses in the background; tests pass one that runs inline.
        scheduler: Starts the daily fetches; built from ``runner`` when not given.
        on_quit: Stops the web server. Without it there is no "Beenden" button (tests, console start).

    Returns:
        The application, ready for uvicorn or the test client.
    """
    jobs = runner or JobRunner(service)
    schedule = scheduler or Scheduler(service, jobs)
    app = FastAPI(
        title=APP_TITLE,
        version=__version__,
        docs_url=None,  # no API documentation pages: nobody needs them, and they would only add surface
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan_for(BackgroundWork(service, jobs, schedule)),
    )
    app.state.context = WebContext(
        service=service,
        jobs=jobs,
        schedule=schedule,
        templates=build_templates(service, quit_enabled=on_quit is not None),
        on_quit=on_quit,
    )
    add_middleware(app, service)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    for router in ROUTERS:
        app.include_router(router)
    return app


def add_middleware(app: FastAPI, service: MailAgentService) -> None:
    """Installs the middleware; the one added last runs first for every request.

    Order of a request: origin check and security headers, then the host check, then the change tracking
    right around the route.
    """
    app.add_middleware(ChangeTrackingMiddleware, on_change=service.note_change)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS)
    app.add_middleware(SameOriginMiddleware)
