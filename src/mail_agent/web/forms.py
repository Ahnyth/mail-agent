"""Typed query and form parameters of the routes.

Grouping the parameters of a page in one small class keeps the route functions short, and FastAPI checks
all of them in one place ("query and form models"). The field names are the German names in the address
or the HTML form.
"""

from pydantic import BaseModel, Field

from ..models import Category, Responsible, Urgency
from ..storage import CorrectionChange
from .texts import notice_text
from .views import ListView

MAX_NAME_CHARS = 120
"""Upper bound for a sender name typed in the form; the stored name is cut to 80 characters."""
MAX_IBAN_CHARS = 60
"""An IBAN has at most 34 characters; the limit leaves room for spaces typed in between."""
MAX_NOTE_CHARS = 500
"""A correction note is a short remark for the test collection, not a letter."""
FILTER_FIELDS = frozenset({"kategorie", "zustaendig", "status", "ki", "q", "werbung"})
"""The parameters of :class:`InboxQuery` that describe the list (the rest is the confirmation)."""


class NoticeQuery(BaseModel):
    """The confirmation after a redirect: ``?hinweis=<code>&anzahl=<number>``."""

    hinweis: str | None = None
    anzahl: int | None = None

    def notice(self) -> str | None:
        """Returns the German confirmation text, or ``None`` for a missing or unknown code."""
        return notice_text(self.hinweis, self.anzahl)


class InboxQuery(NoticeQuery):
    """The filters and the confirmation of the inbox list."""

    kategorie: str | None = None
    zustaendig: str | None = None
    status: str = "offen"
    ki: str | None = None
    q: str = ""
    werbung: str | None = None

    def view(self) -> ListView:
        """Returns the list filters described by these parameters."""
        return ListView.from_values(self.model_dump(include=set(FILTER_FIELDS)))


class DetailQuery(NoticeQuery):
    """The confirmation of a message page and the list it was opened from."""

    von: str = ""


class HandoverQuery(NoticeQuery):
    """The confirmation or the error code shown on the hand-over page."""

    fehler: str = ""


class SenderForm(BaseModel):
    """The form "Absender als bekannt hinterlegen" on a message page."""

    name: str = Field("", max_length=MAX_NAME_CHARS)
    typ: str = ""
    umfang: str = "adresse"
    iban: str = Field("", max_length=MAX_IBAN_CHARS)
    von: str = ""


class CorrectionForm(BaseModel):
    """The correction of category, priority, draft and responsibility on a message page."""

    kategorie: str
    prioritaet: str
    entwurf: str
    zustaendig: str = ""
    notiz: str = Field("", max_length=MAX_NOTE_CHARS)
    alle_absender: str = ""
    von: str = ""

    def change(self) -> CorrectionChange | None:
        """Returns the checked correction, or ``None`` if a field holds something the form never offers."""
        category = next((c for c in Category if c.value == self.kategorie), None)
        urgency = next((u for u in Urgency if u.value == self.prioritaet), None)
        if category is None or urgency is None or self.entwurf not in {"ja", "nein"}:
            return None
        return CorrectionChange(
            category=category,
            urgency=urgency,
            reply_needed=self.entwurf == "ja",
            responsible=next((r for r in Responsible if r.value == self.zustaendig), None),
            note=" ".join(self.notiz.split()),
        )
