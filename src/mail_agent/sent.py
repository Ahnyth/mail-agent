"""Replies in the sent folder: mark the answered mail as "beantwortet" and "erledigt".

The sent folder is read like the inbox: only ``EXAMINE`` and ``BODY.PEEK[]``. Of a sent mail only the header
is used (``In-Reply-To``, ``References``, ``To``/``Cc``, ``Subject``, ``Date``); it is never stored and never
goes to the AI. A reply is matched to a stored mail

1. by the Message-ID it answers (Outlook's "Antworten" sets ``In-Reply-To`` and ``References``), otherwise
2. by recipient and subject: sent to the stored sender or reply address, same subject without "AW:"/"Re:", and
   not older than the mail (a new mail from "In Outlook öffnen" carries no reference).
"""

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from email import policy
from email.message import Message
from email.parser import BytesParser
from email.utils import getaddresses, parsedate_to_datetime

from .parsing import message_key, single_line

SENT_FOLDER_CANDIDATES = (
    "Sent",
    "Gesendet",
    "Gesendete Objekte",
    "Gesendete Elemente",
    "Sent Items",
    "Sent Messages",
    "INBOX.Sent",
    "INBOX/Sent",
    "INBOX.Gesendet",
)
"""Usual names of the sent folder (webmail, Outlook, Thunderbird, Apple Mail), tried in this order."""
NOT_FOUND = "-"
"""Stored in ``app_state`` when no candidate exists, so the folders are not tried again on every fetch."""
RECIPIENT_HEADERS = ("To", "Cc")
"""The headers whose addresses count as the recipients of a reply."""
_MESSAGE_ID = re.compile(r"<[^<>\s]+>")
"""One Message-ID in angle brackets, as ``In-Reply-To`` and ``References`` list them."""
_REPLY_PREFIX = re.compile(r"^\s*((re|aw|antw|wg|fw|fwd)\s*(\[\d+\])?\s*:\s*)+", re.IGNORECASE)
"""Reply and forward prefixes in English and German, also repeated or counted ("Re[2]:")."""


@dataclass(frozen=True)
class SentMail:
    """The header fields of a sent mail that matching needs; the text is never read."""

    references: tuple[str, ...]
    """Message-IDs this mail answers (``In-Reply-To`` first, then ``References``)."""
    recipients: tuple[str, ...]
    subject: str
    sent_at: datetime | None


@dataclass(frozen=True)
class StoredMail:
    """What matching needs of a stored mail (from ``MailRepository.reply_candidates``)."""

    id: int
    message_key: str
    sender_address: str
    reply_to: str
    subject: str
    received_at: datetime | None


def normalized_subject(subject: str) -> str:
    """Returns the subject without reply and forward prefixes, case and extra spaces.

    Example: "AW: Re: Termin" becomes "termin".
    """
    return " ".join(_REPLY_PREFIX.sub("", subject).split()).casefold()


def read_sent(raw: bytes) -> SentMail | None:
    """Reads the header fields of a sent mail; ``None`` if the header is malformed (the mail is skipped)."""
    try:
        message = BytesParser(policy=policy.default).parsebytes(raw, headersonly=True)
        return SentMail(
            references=_references(message),
            recipients=_recipients(message),
            subject=single_line(message.get("Subject")),
            sent_at=_sent_at(message),
        )
    except (TypeError, ValueError, IndexError):  # malformed header: skip this mail
        return None


def _references(message: Message) -> tuple[str, ...]:
    """Returns the Message-IDs the mail answers, each once, ``In-Reply-To`` first."""
    found = _MESSAGE_ID.findall(f"{message.get('In-Reply-To', '')} {message.get('References', '')}")
    return tuple(dict.fromkeys(found))


def _recipients(message: Message) -> tuple[str, ...]:
    """Returns the addresses in ``To`` and ``Cc``, in lower case."""
    headers = [str(message.get(name, "")) for name in RECIPIENT_HEADERS]
    return tuple(address.strip().lower() for _, address in getaddresses(headers) if "@" in address)


def _sent_at(message: Message) -> datetime | None:
    """Returns the time from the ``Date`` header; ``None`` without one."""
    date = message.get("Date")
    return parsedate_to_datetime(str(date)) if date else None


def answered_ids(sent: SentMail, stored: Iterable[StoredMail]) -> list[int]:
    """Returns the ids of the stored mails ``sent`` answers (see the module docstring for the two ways)."""
    mails = list(stored)
    return _answered_by_reference(sent, mails) or _answered_by_subject(sent, mails)


def _answered_by_reference(sent: SentMail, mails: Sequence[StoredMail]) -> list[int]:
    """Returns the mails whose Message-ID the reply names."""
    keys = {message_key(b"", reference) for reference in sent.references}
    return [mail.id for mail in mails if mail.message_key in keys]


def _answered_by_subject(sent: SentMail, mails: Sequence[StoredMail]) -> list[int]:
    """Returns the mails the reply went back to with the same subject, not before they arrived."""
    subject = normalized_subject(sent.subject)
    if not subject:
        return []
    recipients = set(sent.recipients)
    return [
        mail.id
        for mail in mails
        if {mail.sender_address.lower(), mail.reply_to.lower()} & recipients
        and normalized_subject(mail.subject) == subject
        and _not_before(sent.sent_at, mail.received_at)
    ]


def _not_before(sent_at: datetime | None, received_at: datetime | None) -> bool:
    """Checks that the reply was not sent before the mail arrived; unknown or mixed times pass."""
    if sent_at is None or received_at is None:
        return True
    if (sent_at.tzinfo is None) != (received_at.tzinfo is None):
        return True  # not comparable; recipient and subject already have to match
    return sent_at >= received_at
