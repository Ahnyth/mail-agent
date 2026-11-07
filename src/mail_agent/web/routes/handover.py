"""The hand-over page: choose the shared folder, hand over now, or take over the other PC's data."""

from datetime import datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Form, Query, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

from ...handover import Handover, HandoverError, suggested_folders
from ..context import Context, WebContext
from ..forms import HandoverQuery
from ..responses import see_other
from ..texts import HANDOVER_ERRORS

router = APIRouter()

MAX_FOLDER_CHARS = 400
"""Windows paths are at most 260 characters long by default; the limit leaves room for long cloud paths."""


@router.get("/uebergabe", response_class=HTMLResponse)
def handover_page(request: Request, context: Context, query: Annotated[HandoverQuery, Query()]) -> Response:
    """Shows who holds the data, the shared folder and the buttons of the hand-over."""
    return context.render(request, "uebergabe.html", page_data(context, query))


@router.post("/uebergabe/ordner")
def handover_folder(
    context: Context, ordner: Annotated[str, Form(max_length=MAX_FOLDER_CHARS)] = ""
) -> RedirectResponse:
    """Stores the shared folder; the PC that holds the data puts the first package there right away."""
    handover, folder = context.service.handover, ordner.strip()
    if handover is None or not folder:
        return error_redirect("handover_not_configured")
    try:
        state = handover.configure(Path(folder), may_fetch_now=context.service.fetch.enabled)
        if state.active:
            header = context.service.export_handover()
            return see_other(f"/uebergabe?hinweis=uebergabe_geschrieben&anzahl={header.generation}")
    except HandoverError as exc:
        return error_redirect(exc.code)
    return see_other("/uebergabe?hinweis=uebergabe_ordner")


@router.post("/uebergabe/jetzt")
def handover_now(context: Context) -> RedirectResponse:
    """Writes the hand-over package now instead of waiting for the next automatic one."""
    try:
        header = context.service.export_handover()
    except HandoverError as exc:
        return error_redirect(exc.code)
    return see_other(f"/uebergabe?hinweis=uebergabe_geschrieben&anzahl={header.generation}")


@router.post("/uebergabe/uebernehmen")
def handover_take_over(context: Context) -> RedirectResponse:
    """Takes over the data of the other PC; this PC fetches from then on."""
    if context.jobs.running:
        return error_redirect("handover_busy")
    try:
        header = context.service.take_over()
    except HandoverError as exc:
        return error_redirect(exc.code)
    return see_other(f"/?hinweis=uebergabe_uebernommen&anzahl={header.generation}")


def error_redirect(code: str) -> RedirectResponse:
    """Returns to the hand-over page with the text for the error ``code``."""
    return see_other(f"/uebergabe?fehler={code}")


def page_data(context: WebContext, query: HandoverQuery) -> dict[str, object]:
    """Returns all values that ``uebergabe.html`` needs."""
    handover = context.service.handover
    data: dict[str, object] = {
        "available": handover is not None,
        "notice": query.notice(),
        "error": HANDOVER_ERRORS.get(query.fehler),
        "busy": context.jobs.running,
        "pc": handover.pc if handover else "",
        "suggestions": [str(p) for p in suggested_folders()] if handover else [],
    }
    if handover is not None:
        data.update(state_data(handover))
    return data


def state_data(handover: Handover) -> dict[str, object]:
    """Returns the state of the hand-over: folder, holder, last package and the package in the folder."""
    state = handover.state()
    remote = handover.remote_header()
    last_export = datetime.fromisoformat(state.last_export).astimezone() if state.last_export else None
    return {
        "state": state,
        "active": handover.is_active() if state.folder else False,
        "remote": remote,
        "remote_created": remote.created.astimezone() if remote else None,
        "last_export": last_export,
    }
