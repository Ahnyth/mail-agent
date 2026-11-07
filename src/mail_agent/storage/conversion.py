"""Conversion between database values and Python values: times, payment data and simple records.

SQLite has no date type, so points in time are stored as ISO text in UTC and lists as JSON text.
"""

import json
import sqlite3
from datetime import UTC, date, datetime

from ..models import Category, PaymentDetails, Responsible, Urgency
from .records import Correction, FetchRun, StoredDocument


def now_text() -> str:
    """Returns the current time as ISO text in UTC, to the second."""
    return datetime.now(UTC).isoformat(timespec="seconds")


def datetime_text(value: datetime | None) -> str | None:
    """Returns ``value`` as ISO text, or ``None``."""
    return value.isoformat() if value else None


def parse_datetime(value: str | None) -> datetime | None:
    """Reads ISO text back into a point in time; empty values stay ``None``."""
    return datetime.fromisoformat(value) if value else None


def parse_date(value: str | None) -> date | None:
    """Reads an ISO date; empty values stay ``None``."""
    return date.fromisoformat(value) if value else None


def to_json(values: object) -> str:
    """Returns ``values`` as JSON text with umlauts kept readable."""
    return json.dumps(values, ensure_ascii=False)


def responsible_from(value: str | None) -> Responsible | None:
    """Reads a stored responsibility; empty means "nobody in particular"."""
    return Responsible(value) if value else None


def payment_to_json(payment: PaymentDetails | None) -> str | None:
    """Returns the payment data of an analysis as JSON text, or ``None`` without payment data."""
    if payment is None:
        return None
    return to_json(
        {
            "amount": payment.amount,
            "iban": payment.iban,
            "due": payment.due.isoformat() if payment.due else None,
            "invoice_number": payment.invoice_number,
        }
    )


def payment_from_json(value: str | None) -> PaymentDetails | None:
    """Reads stored payment data; empty values stay ``None``."""
    if not value:
        return None
    data = json.loads(value)
    return PaymentDetails(
        amount=data.get("amount"),
        iban=data.get("iban"),
        due=parse_date(data.get("due")),
        invoice_number=data.get("invoice_number"),
    )


def document_from_row(row: sqlite3.Row) -> StoredDocument:
    """Builds a document record from a row of the ``documents`` table."""
    return StoredDocument(
        id=row["id"],
        filename=row["filename"],
        content_type=row["content_type"],
        size=row["size"],
        document_type=row["document_type"],
        stored_path=row["stored_path"],
        rejected_reason=row["rejected_reason"],
        extracted_text=row["extracted_text"],
        text_note=row["text_note"],
    )


def correction_from_row(row: sqlite3.Row) -> Correction:
    """Builds a correction record from a row of the ``corrections`` table."""
    return Correction(
        id=row["id"],
        message_id=row["message_id"],
        original_category=Category(row["original_category"]),
        original_urgency=Urgency(row["original_urgency"]),
        original_reply=bool(row["original_reply"]),
        original_responsible=responsible_from(row["original_responsible"]),
        category=Category(row["category"]),
        urgency=Urgency(row["urgency"]),
        reply_needed=bool(row["reply_needed"]),
        responsible=responsible_from(row["responsible"]),
        note=row["note"],
        engine=row["engine"],
        created_at=datetime.fromisoformat(row["created_at"]),
        exported_to=row["exported_to"],
    )


def run_from_row(row: sqlite3.Row) -> FetchRun:
    """Builds a run record from a row of the ``fetch_runs`` table."""
    return FetchRun(
        id=row["id"],
        kind=row["kind"],
        trigger=row["trigger"],
        started_at=datetime.fromisoformat(row["started_at"]),
        finished_at=parse_datetime(row["finished_at"]),
        found=row["found"],
        imported=row["imported"],
        duplicates=row["duplicates"],
        failed=tuple(json.loads(row["failed"])),
        ai_pending=row["ai_pending"],
        error=row["error"],
    )
