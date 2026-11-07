"""Turn raw RFC 5322 bytes into a :class:`MailMessage`.

Header values are flattened to a single line (no header injection into drafts). HTML bodies are
converted to plain text; scripts and styles are dropped. Attachments are kept as bytes and never
opened or executed.
"""

import email.policy
import hashlib
import html
import mimetypes
import re
from dataclasses import dataclass
from datetime import datetime
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import parseaddr, parsedate_to_datetime
from typing import Self
from urllib.parse import unquote

from .contact_form import from_contact_form
from .models import Attachment, MailMessage

MAX_MESSAGE_BYTES = 30 * 1024 * 1024
"""Largest message read (30 MB); a bigger one is refused, so that one mail cannot fill the memory."""
MAX_HEADER_CHARS = 300
"""Longest header value kept, e.g. a subject; a longer one is cut."""
MAX_NAME_CHARS = 120
"""Longest display name of the sender kept."""
MAX_ADDRESS_CHARS = 254
"""Longest e-mail address there can be (RFC 5321: 256 characters for the path minus the angle brackets)."""
MAX_MESSAGE_ID_CHARS = 500
"""Longest Message-ID kept; it only serves as identity."""
MAX_FILENAME_CHARS = 200
"""Longest attachment file name kept; the filing adds folder names, and Windows paths have a limit."""

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]+")
"""Line breaks and other control characters; in a header they could inject further header lines."""
_SCRIPT_STYLE = re.compile(r"<(script|style)\b.*?</\1\s*>", re.IGNORECASE | re.DOTALL)
"""Script and style blocks of an HTML body; their content is no text for the reader."""
_BLOCK_END = re.compile(r"<\s*(br|/p|/div|/li|/tr|/h[1-6])\b[^>]*>", re.IGNORECASE)
"""HTML tags that end a line."""
_TAG = re.compile(r"<[^>]*>")
"""Any other HTML tag."""
_BLANK_LINES = re.compile(r"\n{3,}")
"""More than one blank line in a row."""
_CID_REFERENCE = re.compile(r"cid:([^\"'\s>)]+)", re.IGNORECASE)
"""A reference from the HTML body to an embedded part (``cid:logo123``)."""
_REPLY_PREFIX = re.compile(r"^\s*(re|aw|antw)\s*:", re.IGNORECASE)
"""Subject prefixes of a reply in German and English mail programs."""


class MailParseError(ValueError):
    """Raised when a message cannot be processed; ``code`` is a fixed diagnostic code without content."""

    def __init__(self, code: str) -> None:
        """Keeps the diagnostic ``code``."""
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class _Headers:
    """The header values a message needs, each flattened to a single line."""

    sender_name: str
    sender_address: str
    reply_to: str
    subject: str
    message_id: str | None

    @classmethod
    def read(cls, message: EmailMessage) -> Self:
        """Reads From, Reply-To, Subject and Message-ID of ``message``."""
        sender_name, sender_address = parseaddr(single_line(message.get("From")))
        _, reply_to = parseaddr(single_line(message.get("Reply-To")))
        return cls(
            sender_name=sender_name,
            sender_address=sender_address,
            reply_to=reply_to,
            subject=single_line(message.get("Subject")),
            message_id=single_line(message.get("Message-ID"), MAX_MESSAGE_ID_CHARS) or None,
        )


def single_line(value: object, limit: int = MAX_HEADER_CHARS) -> str:
    """Returns a header value without line breaks or control characters, trimmed to ``limit``."""
    return _CONTROL_CHARS.sub(" ", str(value or "")).strip()[:limit]


def html_to_text(markup: str) -> str:
    """Returns the readable text of an HTML body: one line per block, no tags, scripts or styles."""
    text = _BLOCK_END.sub("\n", _SCRIPT_STYLE.sub(" ", markup))
    text = html.unescape(_TAG.sub(" ", text))
    lines = (" ".join(line.split()) for line in text.splitlines())
    return _BLANK_LINES.sub("\n\n", "\n".join(lines)).strip()


def mail_text(raw: bytes) -> str:
    """Returns subject, sender and text of an attached (forwarded) mail; empty if unreadable."""
    try:
        message = BytesParser(policy=email.policy.default).parsebytes(raw)
        if not isinstance(message, EmailMessage):
            return ""
        sender, subject = single_line(message.get("From")), single_line(message.get("Subject"))
        return f"Weitergeleitete Mail – Von: {sender}, Betreff: {subject}\n{_body_text(message)}"
    except Exception:  # a broken attachment only loses its text
        return ""


def message_key(raw: bytes, message_id: str | None) -> str:
    """Returns a stable identity: the hash of the Message-ID, or of the raw bytes if there is none."""
    basis = message_id.encode("utf-8") if message_id else raw
    return hashlib.sha256(basis).hexdigest()


def parse_message(raw: bytes) -> MailMessage:
    """Reads one message from its raw bytes.

    A notification of the website's contact form is rewritten so that the visitor is the sender.

    Args:
        raw: The message as received (RFC 5322).

    Returns:
        The parsed message; attachments stay unopened bytes.

    Raises:
        MailParseError: The message is empty, too large, unreadable, has no sender or no content.
    """
    _check_size(raw)
    try:
        message = BytesParser(policy=email.policy.default).parsebytes(raw)
        if not isinstance(message, EmailMessage):  # policy.default always yields EmailMessage
            raise MailParseError("message_unreadable")
        headers = _Headers.read(message)
        body = _body_text(message)
        attachments = _attachments(message)
    except MailParseError:
        raise
    except Exception as exc:  # malformed input must fail closed without leaking content
        raise MailParseError("message_unreadable") from exc
    if not headers.sender_address or "@" not in headers.sender_address:
        raise MailParseError("sender_missing")
    if not headers.subject and not body and not attachments:
        raise MailParseError("message_without_content")
    sender_address = single_line(headers.sender_address, MAX_ADDRESS_CHARS)
    return from_contact_form(
        MailMessage(
            key=message_key(raw, headers.message_id),
            message_id=headers.message_id,
            sender_name=single_line(headers.sender_name, MAX_NAME_CHARS),
            sender_address=sender_address,
            reply_to=single_line(headers.reply_to, MAX_ADDRESS_CHARS) or sender_address,
            subject=headers.subject,
            received_at=_received_at(message),
            body=body,
            attachments=attachments,
        )
    )


def reply_subject(subject: str) -> str:
    """Returns the subject of a reply: unchanged if it already starts with "Re:", "AW:" or "Antw:"."""
    if _REPLY_PREFIX.match(subject):
        return subject
    return f"Re: {subject}" if subject else "Re: Ihre Nachricht"


def _check_size(raw: bytes) -> None:
    """Refuses an empty or oversized message before it is parsed."""
    if not raw:
        raise MailParseError("message_empty")
    if len(raw) > MAX_MESSAGE_BYTES:
        raise MailParseError("message_too_large")


def _body_text(message: EmailMessage) -> str:
    """Returns the text of the message: the plain part, else the HTML part as text."""
    part = message.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    try:
        content = part.get_content()
    except (LookupError, ValueError):  # unknown charset: decode bytes tolerantly
        payload = part.get_payload(decode=True)
        content = payload.decode("utf-8", errors="replace") if isinstance(payload, bytes) else ""
    if not isinstance(content, str):
        return ""
    text = html_to_text(content) if part.get_content_subtype() == "html" else content
    return text.replace("\r\n", "\n").strip()


def _received_at(message: EmailMessage) -> datetime | None:
    """Returns the Date header as a point in time, or ``None`` if it is missing or broken."""
    value = message.get("Date")
    if not value:
        return None
    try:
        return parsedate_to_datetime(str(value))
    except (TypeError, ValueError, IndexError):
        return None


# --- attachments --------------------------------------------------------------------------------------------


def _attachments(message: EmailMessage) -> tuple[Attachment, ...]:
    """Returns the real attachments; images that only belong to the HTML layout (logos) are skipped."""
    embedded = _embedded_ids(message)
    result = []
    for index, part in enumerate(message.iter_attachments(), start=1):
        if _is_layout_image(part, embedded):
            continue
        attachment = _attachment(part, index)
        if attachment is not None:
            result.append(attachment)
    return tuple(result)


def _embedded_ids(message: EmailMessage) -> set[str]:
    """Returns the Content-IDs the HTML body shows inline (``cid:…``), e.g. logos in a signature."""
    part = message.get_body(preferencelist=("html",))
    if part is None:
        return set()
    try:
        content = part.get_content()
    except (LookupError, ValueError):
        return set()
    if not isinstance(content, str):
        return set()
    return {unquote(cid).strip().lower() for cid in _CID_REFERENCE.findall(content)}


def _is_layout_image(part: EmailMessage, embedded: set[str]) -> bool:
    """Checks whether ``part`` is an image the HTML body shows inline."""
    content_id = single_line(part.get("Content-ID")).strip("<>").lower()
    return bool(content_id) and content_id in embedded and part.get_content_maintype() == "image"


def _attachment(part: EmailMessage, index: int) -> Attachment | None:
    """Returns attachment number ``index`` with a file name, or ``None`` if it has no content."""
    content_type = part.get_content_type()
    filename = single_line(part.get_filename(), MAX_FILENAME_CHARS)
    if content_type == "message/rfc822":
        # a mail forwarded "als Anlage": keep it as .eml instead of dropping it silently
        payload = _attached_mail(part)
        filename = filename or f"weitergeleitete-mail-{index}.eml"
    else:
        decoded = part.get_payload(decode=True)
        payload = decoded if isinstance(decoded, bytes) else None
    if payload is None:
        return None
    if not filename:
        extension = mimetypes.guess_extension(content_type) or ".bin"
        filename = f"anhang-{index}{extension}"
    return Attachment(filename=filename, content_type=content_type, payload=payload)


def _attached_mail(part: EmailMessage) -> bytes | None:
    """Returns the bytes of a mail attached as ``message/rfc822``, or ``None``."""
    inner = part.get_payload()
    first = inner[0] if isinstance(inner, list) and inner else None
    return first.as_bytes() if isinstance(first, EmailMessage) else None
