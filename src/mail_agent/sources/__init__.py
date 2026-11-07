"""Mail sources. Every source is read-only: it never changes, moves or deletes messages.

A fetch has two steps so that any number of messages can be processed in portions:

1. ``plan`` looks up which messages are new (IMAP: everything after the stored position and not
   older than the start date) and returns their ids in arrival order;
2. ``load`` reads a portion of those ids. The IMAP source opens a fresh connection for every
   portion, so a long analysis never runs into a server timeout.
"""

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Protocol


@dataclass(frozen=True)
class RawMail:
    """Raw message bytes plus a human-readable origin (file name or IMAP UID)."""

    data: bytes
    origin: str
    item: str = ""
    """Id of the message within its source (IMAP UID or file name)."""


@dataclass(frozen=True)
class FetchCursor:
    """Position after the last message read. IMAP UIDs are only comparable within one ``validity``."""

    validity: str
    last_uid: int


@dataclass(frozen=True)
class FetchPlan:
    """The messages a fetch will read, oldest first."""

    items: tuple[str, ...]
    validity: str | None = None
    """IMAP UIDVALIDITY of the mailbox; ``None`` for sources without positions (``.eml`` folder)."""
    start: date | None = None
    """The start date the search used (stored, so that it stays fixed for later fetches)."""


class MailSource(Protocol):
    """What the fetch needs of a source: a stable name, a plan of new messages and loading in portions."""

    @property
    def identity(self) -> str:
        """Stable name of the source (e.g. ``imap:host/user/INBOX``) for its stored position."""
        ...

    def plan(self, cursor: FetchCursor | None, start: date | None) -> FetchPlan:
        """Returns the new messages after ``cursor`` that arrived on or after ``start``, oldest first.

        Raises:
            SourceError: The source could not be read.
        """
        ...

    def load(self, plan: FetchPlan, items: Sequence[str]) -> Iterator[RawMail]:
        """Yields the messages ``items`` of ``plan``; unreadable ones are left out.

        Raises:
            SourceError: The source could not be read.
        """
        ...


class SourceError(Exception):
    """A source could not be read. ``code`` is a fixed diagnostic code without content or secrets."""

    def __init__(self, code: str) -> None:
        """Keeps ``code``; the web page shows a German text for it."""
        super().__init__(code)
        self.code = code
