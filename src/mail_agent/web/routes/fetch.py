"""Starting a fetch or an AI catch-up by hand, and the progress that the inbox polls."""

from fastapi import APIRouter
from fastapi.responses import JSONResponse, RedirectResponse

from ...service.execution import KIND_AI, KIND_FETCH
from ..context import Context
from ..responses import see_other
from ..views import job_view

router = APIRouter()


@router.post("/einlesen")
def import_messages(context: Context) -> RedirectResponse:
    """Starts a fetch in the background ("Neue Nachrichten einlesen")."""
    if not context.service.fetch.enabled:
        return see_other("/?hinweis=abruf_aus")
    started = context.jobs.start(KIND_FETCH, "manuell")
    return see_other(f"/?hinweis={'abruf_gestartet' if started else 'abruf_laeuft'}")


@router.post("/ki-nachholen")
def catch_up_ai(context: Context) -> RedirectResponse:
    """Starts the AI analysis of all messages that were sorted by the rules only."""
    started = context.jobs.start(KIND_AI, "manuell")
    return see_other(f"/?hinweis={'ki_gestartet' if started else 'abruf_laeuft'}")


@router.get("/abruf/status")
def job_status(context: Context) -> JSONResponse:
    """Returns the progress of the running (or last) job; the inbox asks every few seconds."""
    view = job_view(context.jobs.status())
    return JSONResponse(view or {"running": False, "phase": None})
