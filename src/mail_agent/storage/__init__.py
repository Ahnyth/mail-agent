"""SQLite storage of messages, analyses, corrections and filed documents.

``MailRepository`` bundles one small store per area; the records are plain data classes.
"""

from .records import (
    Correction,
    CorrectionChange,
    FetchRun,
    FetchState,
    InboxFilter,
    MessageDetail,
    MessageSummary,
    RunResult,
    StoredDocument,
)
from .repository import MailRepository
from .schema import SCHEMA_VERSION

__all__ = [
    "SCHEMA_VERSION",
    "Correction",
    "CorrectionChange",
    "FetchRun",
    "FetchState",
    "InboxFilter",
    "MailRepository",
    "MessageDetail",
    "MessageSummary",
    "RunResult",
    "StoredDocument",
]
