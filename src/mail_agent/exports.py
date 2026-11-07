"""Exports: deadlines as iCalendar (.ics) proposal, invoices as CSV for the accountant.

Nothing is written to a calendar or an accounting system; the files are offered for download.
"""

import csv
import io
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

from .briefing import IBAN_DIFFERS, IBAN_SUSPICIOUS, DeadlineItem, InvoiceRow

RISKY_CHECKS = frozenset({IBAN_SUSPICIOUS, IBAN_DIFFERS})
"""IBAN checks that put "NICHT ZAHLEN" into the first CSV column."""
MAX_LINE_OCTETS = 75
"""RFC 5545, section 3.1: content lines longer than 75 octets must be folded."""
MAX_DESCRIPTION_CHARS = 100
"""The deadline text in the event title and the reminder is cut here, so calendars show it in full."""
UTF8_BOM = "﻿"
"""Byte order mark: Excel then reads the CSV as UTF-8 and shows umlauts correctly."""
CSV_COLUMNS = (
    "Achtung",
    "Eingang",
    "Absender",
    "Lieferant",
    "Rechnungsnummer",
    "Betrag",
    "IBAN",
    "IBAN-Prüfung",
    "Fällig",
    "Status",
)
"""The header row of the invoice CSV."""
RISK_WARNING = "NICHT ZAHLEN – erst prüfen"
"""The first CSV column of a risky invoice; the warnings of the web page must not get lost in Excel."""

_ICS_ESCAPES = (("\\", "\\\\"), (";", "\\;"), (",", "\\,"), ("\n", "\\n"))
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")
_CALENDAR_START = (
    "BEGIN:VCALENDAR",
    "VERSION:2.0",
    "PRODID:-//Musterpflege GmbH//MailAgent//DE",
    "CALSCALE:GREGORIAN",
    "METHOD:PUBLISH",
)
_DATE_FORMAT = "%d.%m.%Y"


def _ics_text(value: str) -> str:
    """Escapes RFC 5545 TEXT: backslash, semicolon, comma and line breaks (line breaks stay visible)."""
    text = value.replace("\r\n", "\n").replace("\r", "\n")
    for old, new in _ICS_ESCAPES:
        text = text.replace(old, new)
    return text


def _fold(line: str) -> str:
    """Folds a line longer than :data:`MAX_LINE_OCTETS` (RFC 5545), never inside a UTF-8 character."""
    if len(line.encode("utf-8")) <= MAX_LINE_OCTETS:
        return line
    parts: list[str] = []
    current = b""
    for char in line:
        encoded = char.encode("utf-8")
        # A continuation line starts with a space, which counts towards its 75 octets.
        limit = MAX_LINE_OCTETS if not parts else MAX_LINE_OCTETS - 1
        if len(current) + len(encoded) > limit:
            parts.append(current.decode("utf-8"))
            current = b""
        current += encoded
    parts.append(current.decode("utf-8"))
    return "\r\n ".join(parts)


def _event_lines(index: int, item: DeadlineItem, base_url: str, stamp: str) -> list[str]:
    """Returns the lines of one all-day event with a reminder one day before."""
    message, deadline = item.message, item.deadline
    sender = message.sender_name or message.sender_address
    description = (
        f"Vorschlag des MailAgent – bitte prüfen.\nMail: {message.subject}\nVon: {sender}\n"
        f"{base_url}/nachrichten/{message.id}"
    )
    title = deadline.description[:MAX_DESCRIPTION_CHARS]
    return [
        "BEGIN:VEVENT",
        # two deadlines of one mail on the same day must not share an UID (Outlook keeps one)
        f"UID:frist-{message.id}-{deadline.due:%Y%m%d}-{index}@mail-agent.local",
        f"DTSTAMP:{stamp}",
        f"DTSTART;VALUE=DATE:{deadline.due:%Y%m%d}",
        f"DTEND;VALUE=DATE:{deadline.due + timedelta(days=1):%Y%m%d}",
        f"SUMMARY:{_ics_text('Frist: ' + title)}",
        f"DESCRIPTION:{_ics_text(description)}",
        "TRANSP:TRANSPARENT",
        "BEGIN:VALARM",
        "ACTION:DISPLAY",
        "TRIGGER:-P1D",
        f"DESCRIPTION:{_ics_text('Frist morgen: ' + title)}",
        "END:VALARM",
        "END:VEVENT",
    ]


def deadlines_ics(items: Iterable[DeadlineItem], base_url: str, now: datetime | None = None) -> str:
    """Returns the deadlines as an iCalendar file: all-day events with a reminder one day before.

    Only subject, sender and the deadline text go into the file, no mail content.

    Args:
        items: The deadlines to export.
        base_url: The address of the web interface, for the link back to each mail.
        now: The creation time written into every event; defaults to the current time.

    Returns:
        The calendar text with CRLF line ends, as RFC 5545 requires.
    """
    stamp = (now or datetime.now(UTC)).astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    lines = list(_CALENDAR_START)
    for index, item in enumerate(items):
        lines += _event_lines(index, item, base_url, stamp)
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"


def _cell(value: object) -> str:
    """Returns the text of a CSV cell; a leading = + - @ is neutralised, so no formula runs in Excel."""
    text = "" if value is None else str(value)
    return f"'{text}" if text.startswith(_FORMULA_START) else text


def _invoice_cells(row: InvoiceRow) -> list[str]:
    """Returns the CSV cells of one invoice in the order of :data:`CSV_COLUMNS`."""
    message, payment = row.message, row.payment
    risky = row.iban_check in RISKY_CHECKS or bool(message.analysis.warnings)
    values = (
        RISK_WARNING if risky else "",
        message.received_at.strftime(_DATE_FORMAT) if message.received_at else "",
        message.sender_name or message.sender_address,
        row.supplier or "",
        payment.invoice_number,
        payment.amount,
        payment.iban,
        row.iban_check,
        payment.due.strftime(_DATE_FORMAT) if payment.due else "",
        message.status.value,
    )
    return [_cell(value) for value in values]


def invoices_csv(rows: Iterable[InvoiceRow]) -> str:
    """Returns the invoices as CSV for Excel: semicolons and a BOM, so umlauts and columns come out right.

    Args:
        rows: The invoices to export.

    Returns:
        The CSV text with a header row; risky invoices say "NICHT ZAHLEN" in the first column.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";", lineterminator="\r\n")
    writer.writerow(CSV_COLUMNS)
    for row in rows:
        writer.writerow(_invoice_cells(row))
    return UTF8_BOM + buffer.getvalue()
