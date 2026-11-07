"""Known senders from a message page: store a sender as known, or remove the entry again."""

from dataclasses import dataclass
from http import HTTPStatus
from typing import Annotated, cast

from fastapi import APIRouter, Form, HTTPException
from fastapi.responses import RedirectResponse

from ...analysis.signals import iban_is_valid
from ...models import Category
from ...senders import (
    SENDER_TYPES,
    KnownSender,
    SenderType,
    base_domain,
    identity_address,
    is_freemail,
    normalize_iban,
    sent_via_service,
)
from ...service.execution import KIND_REANALYZE
from ...storage import MessageDetail
from ..context import Context
from ..forms import SenderForm
from ..responses import see_other
from ..views import ListView

router = APIRouter()

MAX_STORED_NAME_CHARS = 80
"""Sender names longer than this do not fit into the lists of the web interface."""
SIGNATURE_CHARS = 800
"""The end of a mail where a signature with "Kanzlei" or "Rechtsanwalt" usually stands."""
LAWYER_HINTS = ("kanzlei", "rechtsanw", "anwalt", "notar")
TYPE_FOR_CATEGORY = {
    Category.BILLING: "lieferant",
    Category.INSURANCE: "kostentraeger",
    Category.AUTHORITY: "behoerde",
    Category.PHYSICIAN: "arzt",
    Category.ADVERTISING: "newsletter",
    Category.LEGAL: "anwalt",
}
"""The sender type suggested for a message of this category."""


@dataclass(frozen=True)
class SenderSuggestion:
    """Pre-filled values of "Absender als bekannt hinterlegen" for one message.

    The address or domain always comes from the stored message, never from the form, so a post can never
    mark an unrelated sender as known.
    """

    identity: str
    """The address that really speaks for the sender (``Reply-To`` for mails sent through a service)."""
    domain: str
    domain_allowed: bool
    """A whole domain only for a company's own domain, never gmail.com, web.de, lexware.de …"""
    via_service: bool
    name: str
    type: str

    @classmethod
    def for_message(cls, message: MessageDetail) -> "SenderSuggestion":
        """Returns the suggestion for the sender of ``message``."""
        identity = identity_address(message.sender_address, message.reply_to)
        via_service = sent_via_service(message.sender_address) and identity != message.sender_address.lower()
        return cls(
            identity=identity,
            domain=base_domain(identity),
            domain_allowed=not via_service and not is_freemail(identity),
            via_service=via_service,
            name=(message.sender_name or base_domain(identity))[:MAX_STORED_NAME_CHARS],
            type=suggested_type(message, identity),
        )


def suggested_type(message: MessageDetail, identity: str) -> str:
    """Suggests a sender type; a law firm is recognisable by domain, name or signature.

    It is only a suggestion; the office decides in the form.
    """
    hints = f"{identity} {message.sender_name} {message.body[-SIGNATURE_CHARS:]}".lower()
    if any(hint in hints for hint in LAWYER_HINTS):
        return "anwalt"
    return TYPE_FOR_CATEGORY.get(message.analysis.category, "sonstige")


@router.post("/nachrichten/{message_id}/absender")
def save_sender(message_id: int, form: Annotated[SenderForm, Form()], context: Context) -> RedirectResponse:
    """Stores the sender of a message as known; its open mails are analysed again."""
    message = context.message(message_id)
    if message.contact_form:
        raise HTTPException(
            HTTPStatus.BAD_REQUEST, "Absender aus dem Kontaktformular lassen sich nicht hinterlegen"
        )
    name = " ".join(form.name.split())
    if form.typ not in SENDER_TYPES or not name:
        raise HTTPException(HTTPStatus.BAD_REQUEST, "Name und Typ angeben")
    view = ListView.from_query(form.von)
    if form.iban.strip() and not iban_is_valid(form.iban):
        return see_other(view.detail_url(message_id, "iban_ungueltig"))
    sender = known_sender(SenderSuggestion.for_message(message), name, form)
    try:
        ids = context.service.save_known_sender(sender)
    except ValueError:
        raise HTTPException(HTTPStatus.CONFLICT, "Absenderliste nicht verfügbar") from None
    started = bool(ids) and context.jobs.start(KIND_REANALYZE, "absender", ids=ids)
    notice = "absender_gespeichert" if started or not ids else "absender_gespeichert_ohne"
    return see_other(view.detail_url(message_id, notice, len(ids)))


@router.post("/nachrichten/{message_id}/absender-entfernen")
def forget_sender(
    message_id: int, context: Context, kennung: Annotated[str, Form()] = "", von: Annotated[str, Form()] = ""
) -> RedirectResponse:
    """Removes a sender from the list that this PC maintains; its open mails are analysed again."""
    message = context.message(message_id)
    if not context.service.forget_known_sender(kennung.strip().lower()):
        raise HTTPException(HTTPStatus.NOT_FOUND, "Nicht in der hier gepflegten Absenderliste")
    ids = context.service.same_sender_ids(message.sender_address, message.reply_to)
    if ids:
        context.jobs.start(KIND_REANALYZE, "absender", ids=ids)
    return see_other(ListView.from_query(von).detail_url(message_id, "absender_entfernt"))


def known_sender(suggestion: SenderSuggestion, name: str, form: SenderForm) -> KnownSender:
    """Builds the new entry from the suggestion and the choices made in the form."""
    whole_domain = form.umfang == "domain" and suggestion.domain_allowed
    return KnownSender(
        name=name[:MAX_STORED_NAME_CHARS],
        type=cast(SenderType, form.typ),
        domains=(suggestion.domain,) if whole_domain else (),
        addresses=() if whole_domain else (suggestion.identity,),
        ibans=(normalize_iban(form.iban),) if form.iban.strip() else (),
    )
