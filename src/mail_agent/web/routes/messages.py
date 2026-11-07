"""A single message: its page and the actions on it (status, draft, answered, new analysis)."""

from dataclasses import replace
from datetime import date
from http import HTTPStatus
from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

from ...analysis import AnalysisUnavailableError
from ...briefing import deadline_items
from ...exports import deadlines_ics
from ...models import Status, Urgency
from ...parsing import MailParseError
from ...senders import SENDER_TYPE_LABELS, SENDER_TYPES, load_local_senders
from ...storage import MessageDetail
from ..context import MESSAGE_NOT_FOUND, Context, WebContext
from ..forms import DetailQuery
from ..responses import CALENDAR_TYPE, base_url, see_other, text_download
from ..views import ListView, message_url
from .senders import SenderSuggestion

router = APIRouter()

MAX_DRAFT_CHARS = 50_000
"""A draft longer than this is no reply any more; the limit protects the database from accidents."""


@router.get("/nachrichten/{message_id}", response_class=HTMLResponse)
def detail(
    request: Request, message_id: int, context: Context, query: Annotated[DetailQuery, Query()]
) -> Response:
    """Shows a message with analysis, draft, attachments and the forms around it."""
    page = DetailPage(context, context.message(message_id), ListView.from_query(query.von))
    return context.render(request, "detail.html", page.data(query.notice()))


@router.post("/nachrichten/{message_id}/status")
def change_status(
    message_id: int, context: Context, status: Annotated[str, Form()], von: Annotated[str, Form()] = ""
) -> RedirectResponse:
    """Marks a message as open or done; after "erledigt" the next open mail of the list follows."""
    new_status = next((s for s in Status if s.value == status), None)
    if new_status is None:
        raise HTTPException(HTTPStatus.BAD_REQUEST, "Ungültiger Status")
    view = ListView.from_query(von)
    # the next mail must be found before this one leaves the list of open mails
    following = view.next_open_message(context.repository, message_id) if new_status is Status.DONE else None
    if not context.repository.workflow.set_status(message_id, new_status):
        raise HTTPException(HTTPStatus.BAD_REQUEST, "Ungültiger Status")
    if new_status is not Status.DONE:
        return see_other(view.detail_url(message_id, "status"))
    if following is not None:
        return see_other(view.detail_url(following, "erledigt_weiter"))
    return see_other(f"/?{view.query()}&hinweis=alle_erledigt")


@router.post("/nachrichten/{message_id}/entwurf")
def save_draft(
    message_id: int, context: Context, entwurf: Annotated[str, Form(max_length=MAX_DRAFT_CHARS)] = ""
) -> RedirectResponse:
    """Stores the draft as edited by the office."""
    if not context.repository.analyses.save_draft(message_id, unix_newlines(entwurf)):
        raise HTTPException(HTTPStatus.NOT_FOUND, MESSAGE_NOT_FOUND)
    return see_other(message_url(message_id, "gespeichert"))


@router.post("/nachrichten/{message_id}/entwurf-formulieren")
def polish_draft(
    message_id: int, context: Context, entwurf: Annotated[str, Form(max_length=MAX_DRAFT_CHARS)] = ""
) -> RedirectResponse:
    """Lets the AI turn keywords or a rough draft into a polite reply; nothing is sent."""
    if context.service.drafter is None:
        raise HTTPException(HTTPStatus.CONFLICT, "Keine KI eingerichtet")
    if not entwurf.strip():
        return see_other(message_url(message_id, "diktat_leer"))
    context.repository.analyses.save_draft(
        message_id, unix_newlines(entwurf)
    )  # nothing typed is lost if the AI fails
    try:
        draft = context.service.draft_from_dictation(message_id, entwurf)
    except AnalysisUnavailableError:
        return see_other(message_url(message_id, "ki_fehler"))
    if draft is None:
        raise HTTPException(HTTPStatus.NOT_FOUND, MESSAGE_NOT_FOUND)
    return see_other(message_url(message_id, "formuliert"))


@router.post("/nachrichten/{message_id}/neu-analysieren")
def reanalyze(message_id: int, context: Context) -> RedirectResponse:
    """Analyses a message again with the current rules, senders and AI."""
    try:
        result = context.service.reanalyze(message_id)
    except (MailParseError, AnalysisUnavailableError):
        raise HTTPException(HTTPStatus.INTERNAL_SERVER_ERROR, "Analyse fehlgeschlagen") from None
    if result is None:
        raise HTTPException(HTTPStatus.NOT_FOUND, MESSAGE_NOT_FOUND)
    return see_other(message_url(message_id, "neu_analysiert"))


@router.post("/nachrichten/{message_id}/beantwortet")
def mark_answered(message_id: int, context: Context, beantwortet: Annotated[str, Form()]) -> RedirectResponse:
    """Sets or removes the mark "beantwortet" by hand."""
    answered = beantwortet == "ja"
    if beantwortet not in {"ja", "nein"} or not context.repository.workflow.set_answered(
        message_id, answered
    ):
        raise HTTPException(HTTPStatus.BAD_REQUEST, "Ungültige Anfrage")
    return see_other(message_url(message_id, "beantwortet" if answered else "unbeantwortet"))


@router.post("/nachrichten/{message_id}/testfall")
def export_case(message_id: int, context: Context) -> RedirectResponse:
    """Saves the correction of a message as an anonymised test case for the evaluation."""
    if context.service.export_correction(message_id) is None:
        raise HTTPException(HTTPStatus.NOT_FOUND, "Keine Korrektur zu dieser Nachricht")
    return see_other(message_url(message_id, "testfall"))


@router.get("/nachrichten/{message_id}/fristen.ics")
def message_deadlines(request: Request, message_id: int, context: Context) -> Response:
    """Exports the deadlines of one message as a calendar file."""
    message = context.repository.messages.get(message_id)
    if message is None or not message.analysis.deadlines:
        raise HTTPException(HTTPStatus.NOT_FOUND, "Keine Fristen")
    # also for a message already marked as done: export what is there
    items = deadline_items([replace(message, status=Status.OPEN)], date.today(), None)
    content = deadlines_ics(items, base_url(request))
    return text_download(content, CALENDAR_TYPE, f"fristen-nachricht-{message_id}.ics")


def unix_newlines(text: str) -> str:
    """Returns ``text`` with the line breaks that browsers send (CRLF) turned into plain ones."""
    return text.replace("\r\n", "\n")


class DetailPage:
    """Collects the template data of a message page; one small method per part of the page."""

    def __init__(self, context: WebContext, message: MessageDetail, view: ListView) -> None:
        """Prepares the page of ``message``, opened from the list ``view``."""
        self._context = context
        self._message = message
        self._view = view

    def data(self, notice: str | None) -> dict[str, object]:
        """Returns all values that ``detail.html`` needs."""
        message = self._message
        return {
            "m": message,
            "a": message.analysis,
            "notice": notice,
            "high": Urgency.HIGH,
            "list_query": self._view.query(),
            **self._known_sender(),
            **self._local_sender(),
            **self._sender_form(),
        }

    def _known_sender(self) -> dict[str, object]:
        """The entry of the sender in the list of known senders, if there is one."""
        message = self._message
        # a contact form address was typed by the visitor and is never a known sender
        known = (
            None
            if message.contact_form
            else self._context.service.senders.lookup_message(message.sender_address, message.reply_to)
        )
        return {
            "known": known,
            "known_type": SENDER_TYPE_LABELS.get(known.type, known.type) if known else None,
        }

    def _local_sender(self) -> dict[str, object]:
        """The entry that this PC maintains itself; only that one can be removed here."""
        message = self._message
        path = self._context.service.local_senders_path
        local = (
            load_local_senders(path).lookup_message(message.sender_address, message.reply_to)
            if path
            else None
        )
        return {
            "local_known": local,
            "local_key": next(iter((*local.addresses, *local.domains)), "") if local else "",
            "can_save_senders": path is not None,
        }

    def _sender_form(self) -> dict[str, object]:
        """The suggestions for "Absender als bekannt hinterlegen"."""
        message = self._message
        same_sender = self._context.service.same_sender_ids(message.sender_address, message.reply_to)
        return {
            "sender_form": SenderSuggestion.for_message(message),
            "sender_types": [(t, SENDER_TYPE_LABELS[t]) for t in SENDER_TYPES],
            "same_sender_count": len([i for i in same_sender if i != message.id]),
        }
