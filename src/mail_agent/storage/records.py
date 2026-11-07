"""The records that the stores read and write: plain, frozen data classes without database code."""

from dataclasses import dataclass
from datetime import date, datetime

from ..models import Analysis, Category, Responsible, Status, Urgency


@dataclass(frozen=True)
class Correction:
    """A correction made by hand; the original values are kept for the evaluation."""

    id: int
    message_id: int
    original_category: Category
    original_urgency: Urgency
    original_reply: bool
    original_responsible: Responsible | None
    category: Category
    urgency: Urgency
    reply_needed: bool
    responsible: Responsible | None
    note: str
    engine: str
    created_at: datetime
    exported_to: str | None


@dataclass(frozen=True)
class CorrectionChange:
    """The new values of a correction, as chosen in the form of a message page."""

    category: Category
    urgency: Urgency
    reply_needed: bool
    responsible: Responsible | None
    note: str = ""


@dataclass(frozen=True)
class MessageSummary:
    """One line of the inbox list."""

    id: int
    sender: str
    subject: str
    received_at: datetime | None
    status: Status
    category: Category
    urgency: Urgency
    summary: str
    document_count: int
    responsible: Responsible | None = None
    answered_at: datetime | None = None
    ai_pending: bool = False


@dataclass(frozen=True)
class InboxFilter:
    """Which messages the inbox list shows; every field left empty means "no restriction"."""

    category: Category | None = None
    status: Status | None = None
    responsible: Responsible | None = None
    ai_pending: bool = False
    search: str = ""
    """Every word must appear in sender, subject, summary or text, in any case and with umlauts."""
    exclude_category: Category | None = None


@dataclass(frozen=True)
class FetchState:
    """How far a source has been read; an IMAP ``last_uid`` is only valid with the same ``validity``."""

    source: str
    validity: str | None
    last_uid: int
    start_date: date | None


@dataclass(frozen=True)
class RunResult:
    """What a fetch or an AI catch-up achieved, for the run log."""

    found: int = 0
    imported: int = 0
    duplicates: int = 0
    failed: tuple[str, ...] = ()
    ai_pending: int = 0
    error: str | None = None


@dataclass(frozen=True)
class FetchRun:
    """One fetch or AI catch-up from the run log, for the inbox ("Letzter Abruf …")."""

    id: int
    kind: str
    trigger: str
    started_at: datetime
    finished_at: datetime | None
    found: int
    imported: int
    duplicates: int
    failed: tuple[str, ...]
    ai_pending: int
    error: str | None


@dataclass(frozen=True)
class StoredDocument:
    """An attachment of a stored message, filed or rejected."""

    id: int
    filename: str
    content_type: str
    size: int
    document_type: str | None
    stored_path: str | None
    rejected_reason: str | None
    extracted_text: str | None = None
    text_note: str | None = None


@dataclass(frozen=True)
class MessageDetail:
    """A stored message with analysis, documents and the latest correction, for its page and the lists."""

    id: int
    origin: str
    sender_name: str
    sender_address: str
    reply_to: str
    subject: str
    received_at: datetime | None
    body: str
    status: Status
    analysis: Analysis
    draft_edited: bool
    documents: tuple[StoredDocument, ...]
    correction: Correction | None = None
    answered_at: datetime | None = None
    contact_form: bool = False
    """Sender and reply address come from the website's contact form (typed by the visitor, unverified)."""
