"""Morning briefing, deadlines, invoices and follow-ups – computed from the database, without AI."""

from dataclasses import dataclass
from datetime import date, timedelta

from .models import Category, Deadline, PaymentDetails, Status, Urgency
from .senders import KnownSender, SenderDirectory
from .storage import MessageDetail

IBAN_MATCHES = "passt"
"""The IBAN of the invoice is one of the IBANs stored for the sender."""
IBAN_DIFFERS = "weicht ab"
"""The sender has stored IBANs, but the invoice names another one: a classic fraud sign."""
IBAN_NOT_STORED = "nicht hinterlegt"
"""The sender is known, but no IBAN is stored for it."""
IBAN_SENDER_UNKNOWN = "Absender unbekannt"
"""The sender is not in the list of known senders."""
IBAN_MISSING = "keine IBAN"
"""The invoice names no IBAN."""
IBAN_SUSPICIOUS = "Verdacht"
"""The mail itself was classified as fraud or phishing."""
DEFAULT_HORIZON_DAYS = 14
"""Deadlines up to two weeks ahead appear in the briefing unless the configuration says otherwise."""
MAX_SPOKEN_URGENT = 5
"""At most this many urgent mails are read aloud one by one; more would make the briefing too long."""

_WEEKDAYS = ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag")
_MONTHS = (
    "Januar", "Februar", "März", "April", "Mai", "Juni",
    "Juli", "August", "September", "Oktober", "November", "Dezember",
)  # fmt: skip
_SENTENCE_ENDS = (".", "!", "?")


@dataclass(frozen=True)
class DeadlineItem:
    """One deadline of an open message.

    Attributes:
        message: The message that names the deadline.
        deadline: The day and what is due.
        days_left: Days until the deadline; negative when it has passed.
    """

    message: MessageDetail
    deadline: Deadline
    days_left: int


@dataclass(frozen=True)
class InvoiceRow:
    """One invoice with the check of its IBAN against the known senders.

    Attributes:
        message: The message that contains the invoice.
        payment: Amount, IBAN, due date and invoice number as read from the mail.
        supplier: The name of the known sender, if the sender is known.
        iban_check: The outcome of the IBAN check, see below.
    """

    message: MessageDetail
    payment: PaymentDetails
    supplier: str | None
    iban_check: str
    """passt | weicht ab | nicht hinterlegt | Absender unbekannt | keine IBAN | Verdacht"""


@dataclass(frozen=True)
class Briefing:
    """What matters today: urgent mails, coming deadlines, open invoices and mails still waiting for a reply.

    Attributes:
        today: The day of the briefing.
        urgent: Open urgent messages (suspicious ones are left out).
        deadlines: Deadlines within ``horizon_days``, overdue ones included.
        payments: Open invoices that are not suspected fraud.
        unanswered: Messages without a reply for more than ``follow_up_days``.
        follow_up_days: After how many days a message counts as waiting.
        horizon_days: How many days ahead deadlines are shown.
    """

    today: date
    urgent: list[MessageDetail]
    deadlines: list[DeadlineItem]
    payments: list[InvoiceRow]
    unanswered: list[MessageDetail]
    follow_up_days: int
    horizon_days: int = DEFAULT_HORIZON_DAYS

    def speech_text(self) -> str:
        """Returns the text to read aloud: counts and read-aloud sentences only, no names, no health data."""
        parts = [f"Guten Morgen. Heute ist {spoken_date(self.today)}.", *self._urgent_sentences()]
        if self.deadlines:
            parts.append(self._deadline_sentence())
        if self.payments:
            parts.append(f"{_count(len(self.payments), 'offene Rechnung', 'offene Rechnungen')}.")
        if self.unanswered:
            parts.append(
                f"Seit mehr als {self.follow_up_days} Tagen ohne Antwort: "
                f"{_count(len(self.unanswered), 'Mail', 'Mails')}."
            )
        return " ".join(parts)

    def _urgent_sentences(self) -> list[str]:
        """Returns the number of urgent mails and the read-aloud sentence of the first ones."""
        if not self.urgent:
            return ["Keine dringenden Mails."]
        count = f"{_count(len(self.urgent), 'dringende Mail', 'dringende Mails')}."
        return [count, *(_sentence(m) for m in self.urgent[:MAX_SPOKEN_URGENT])]

    def _deadline_sentence(self) -> str:
        """Returns the number of deadlines and the day of the next one."""
        first = self.deadlines[0]
        return (
            f"{_count(len(self.deadlines), 'Frist', 'Fristen')} in den nächsten Tagen, "
            f"die nächste am {spoken_date(first.deadline.due, weekday=False)}."
        )


def _count(n: int, singular: str, plural: str) -> str:
    """Returns e.g. "Eine Frist" or "3 Fristen"."""
    return f"{'Eine' if n == 1 else n} {singular if n == 1 else plural}"


def _sentence(message: MessageDetail) -> str:
    """Returns the read-aloud sentence of a message (else category and subject), ending with a stop."""
    text = message.analysis.read_aloud or f"{message.analysis.category.label}: {message.subject}"
    return text if text.endswith(_SENTENCE_ENDS) else f"{text}."


def spoken_date(value: date, *, weekday: bool = True) -> str:
    """Returns a date the way it is spoken in German.

    Args:
        value: The day.
        weekday: ``True`` gives "Montag, der 2. November", ``False`` gives "2. November".

    Returns:
        The date in words, without the year.
    """
    text = f"der {value.day}. {_MONTHS[value.month - 1]}"
    return f"{_WEEKDAYS[value.weekday()]}, {text}" if weekday else text.removeprefix("der ")


def deadline_items(details: list[MessageDetail], today: date, horizon_days: int | None) -> list[DeadlineItem]:
    """Returns the deadlines of open messages, overdue ones included, earliest first.

    Args:
        details: The messages to look at.
        today: Days left are counted from here.
        horizon_days: Only deadlines up to this many days ahead; ``None`` means all.

    Returns:
        The deadlines sorted by day, then by message.
    """
    items = []
    for message in details:
        if message.status is not Status.OPEN:
            continue
        for deadline in message.analysis.deadlines:
            days_left = (deadline.due - today).days
            if horizon_days is None or days_left <= horizon_days:
                items.append(DeadlineItem(message, deadline, days_left))
    return sorted(items, key=lambda i: (i.deadline.due, i.message.id))


def _iban_check(message: MessageDetail, payment: PaymentDetails, sender: KnownSender | None) -> str:
    """Compares the IBAN of an invoice with the IBANs stored for its sender; returns the check text."""
    if message.analysis.category is Category.SUSPICIOUS:
        return IBAN_SUSPICIOUS
    if not payment.iban:
        return IBAN_MISSING
    if sender is None:
        return IBAN_SENDER_UNKNOWN
    if not sender.ibans:
        return IBAN_NOT_STORED
    return IBAN_MATCHES if payment.iban in sender.ibans else IBAN_DIFFERS


def invoice_rows(details: list[MessageDetail], senders: SenderDirectory) -> list[InvoiceRow]:
    """Returns the invoices among billing and suspicious messages, each with its IBAN check.

    Args:
        details: The messages to look at.
        senders: The known senders with their stored IBANs.

    Returns:
        The invoices sorted by due date (without a date last), then by message.
    """
    rows = []
    for message in details:
        payment = message.analysis.payment
        if payment is None or message.analysis.category not in {Category.BILLING, Category.SUSPICIOUS}:
            continue
        sender = senders.lookup(message.sender_address)
        supplier = sender.name if sender else None
        rows.append(InvoiceRow(message, payment, supplier, _iban_check(message, payment, sender)))
    return sorted(rows, key=lambda r: (r.payment.due or date.max, r.message.id))


def unanswered(details: list[MessageDetail], today: date, days: int) -> list[MessageDetail]:
    """Returns open messages that need a reply, are not marked as answered and are older than ``days``."""
    limit = today - timedelta(days=days)
    result = [
        m
        for m in details
        if m.status is Status.OPEN
        and m.analysis.reply_needed
        and m.answered_at is None
        and m.received_at is not None
        and m.received_at.date() <= limit
    ]
    return sorted(result, key=lambda m: m.received_at.timestamp() if m.received_at else 0.0)


def build_briefing(
    details: list[MessageDetail],
    senders: SenderDirectory,
    today: date,
    *,
    horizon_days: int,
    follow_up_days: int,
) -> Briefing:
    """Builds the briefing of ``today`` from the open messages.

    Args:
        details: All messages; done ones are left out.
        senders: The known senders for the IBAN check.
        today: The day of the briefing.
        horizon_days: How many days ahead deadlines are shown.
        follow_up_days: After how many days a message without reply counts as waiting.

    Returns:
        The briefing; suspected fraud appears neither as urgent nor as an invoice to pay.
    """
    open_messages = [m for m in details if m.status is Status.OPEN]
    return Briefing(
        today=today,
        urgent=[
            m
            for m in open_messages
            if m.analysis.urgency is Urgency.HIGH and m.analysis.category is not Category.SUSPICIOUS
        ],
        deadlines=deadline_items(open_messages, today, horizon_days),
        payments=[r for r in invoice_rows(open_messages, senders) if r.iban_check != IBAN_SUSPICIOUS],
        unanswered=unanswered(open_messages, today, follow_up_days),
        follow_up_days=follow_up_days,
        horizon_days=horizon_days,
    )
