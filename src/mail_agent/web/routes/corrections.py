"""Corrections by hand: the office fixes category, priority, draft need or responsibility of a message."""

from dataclasses import replace
from http import HTTPStatus
from typing import Annotated

from fastapi import APIRouter, Form, HTTPException
from fastapi.responses import RedirectResponse

from ...storage import MessageDetail
from ..context import Context, WebContext
from ..forms import CorrectionForm
from ..responses import see_other
from ..views import ListView

router = APIRouter()


@router.post("/nachrichten/{message_id}/korrektur")
def correct(message_id: int, form: Annotated[CorrectionForm, Form()], context: Context) -> RedirectResponse:
    """Stores a correction; on request also for the other open mails of the same sender."""
    change = form.change()
    if change is None:
        raise HTTPException(HTTPStatus.BAD_REQUEST, "Ungültige Korrektur")
    message = context.message(message_id)
    others = uncorrected_mails_of_sender(context, message) if form.alle_absender == "ja" else []
    context.repository.corrections.add(message_id, change)
    for other in others:
        context.repository.corrections.add(
            other, replace(change, note=f"übernommen von Nachricht {message_id}")
        )
    view = ListView.from_query(form.von)
    if others:
        return see_other(view.detail_url(message_id, "korrigiert_absender", len(others)))
    return see_other(view.detail_url(message_id, "korrigiert"))


def uncorrected_mails_of_sender(context: WebContext, message: MessageDetail) -> list[int]:
    """Returns the other open mails of the sender that nobody corrected by hand yet."""
    ids = context.service.same_sender_ids(message.sender_address, message.reply_to)
    return [
        i
        for i in ids
        if i != message.id
        and (other := context.repository.messages.get(i)) is not None
        and other.correction is None
    ]
