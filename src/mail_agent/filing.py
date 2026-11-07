"""Files attachments into a traceable folder structure.

Layout::

    <root>/<JJJJ>/<JJJJ-MM-TT>_<kategorie>_<absender>_<schlüssel>/
        <dateiname>            the attachment, never overwritten
        herkunft.json          origin: sender, subject, date, message key, SHA-256 per file

File names are sanitised (no path separators, no reserved Windows names). Executable and
macro-enabled formats, disk images and HTML pages (a common phishing carrier) are not stored.
Files are written exclusively and never overwritten. On Windows every stored file is marked as
coming from the internet ("Mark of the Web"), so Office opens it in Protected View and blocks macros.
A file that cannot be written (e.g. blocked by the virus scanner) is recorded as not filed; it never
stops the import.
"""

import hashlib
import json
import os
import re
import unicodedata
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .models import Analysis, Attachment, FiledDocument, MailMessage
from .security.redaction import get_logger

_log = get_logger("filing")

MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024
"""Larger attachments are not filed; 25 MB is the size limit of a whole mail at many providers."""
PROVENANCE_FILE = "herkunft.json"
"""The file next to the attachments that records where they came from."""
BLOCKED_EXTENSIONS = frozenset(
    {
        ".exe", ".com", ".bat", ".cmd", ".scr", ".pif", ".msi", ".msp", ".dll", ".cpl",
        ".js", ".jse", ".vbs", ".vbe", ".wsf", ".wsh", ".ps1", ".psm1", ".hta", ".jar",
        ".lnk", ".reg", ".iso", ".img", ".vhd", ".vhdx", ".docm", ".xlsm", ".pptm", ".dotm",
        ".xlam", ".xltm", ".potm", ".ppsm", ".ppam", ".sldm", ".xla", ".xll", ".one", ".chm",
        ".url", ".scf", ".msix", ".msixbundle", ".appx", ".appxbundle", ".application",
        ".html", ".htm", ".shtml", ".xhtml", ".mht", ".mhtml", ".svg",
    }
)  # fmt: skip
"""Programs, scripts, macro-enabled Office files, disk images and web pages: never stored on disk."""
MAX_SLUG_CHARS = 30
"""Folder-name fragments are cut here, so the whole path stays well below Windows' 260 characters."""
MAX_STEM_CHARS = 100
"""A file name without its extension is cut here, for the same reason."""
MAX_SUFFIX_CHARS = 10
"""Longer "extensions" are no real file types; cutting them keeps the name short."""
KEY_PREFIX_CHARS = 8
"""Part of the message key in the folder name: enough to tell two mails of one day and sender apart."""
MAX_NAME_VARIANTS = 999
"""Different files with the same name in one folder get ``-2``, ``-3`` …; beyond this filing gives up."""
WRITE_FAILED_REASON = "Ablage fehlgeschlagen (z. B. vom Virenscanner blockiert) – Original im Postfach"
"""Shown when a permitted attachment could not be written."""

_ZONE_IDENTIFIER = "[ZoneTransfer]\r\nZoneId=3\r\n"
"""Content of the "Mark of the Web" stream; zone 3 is the internet zone of Windows."""
_RESERVED_NAMES = frozenset(
    {"con", "prn", "aux", "nul"} | {f"com{i}" for i in range(1, 10)} | {f"lpt{i}" for i in range(1, 10)}
)
"""Device names Windows refuses as file names, whatever the extension."""
_UNSAFE_CHARS = re.compile(r"[^\w.\- ]+")
_SLUG_CHARS = re.compile(r"[^a-z0-9]+")
_UMLAUTS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})


def slugify(value: str, limit: int = MAX_SLUG_CHARS) -> str:
    """Returns an ASCII folder-name fragment; German umlauts are transliterated (ä -> ae).

    Args:
        value: Any text, e.g. a sender name.
        limit: The longest fragment returned.

    Returns:
        Lower-case letters, digits and dashes, or ``unbekannt`` if nothing usable is left.
    """
    text = value.lower().translate(_UMLAUTS)
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    slug = _SLUG_CHARS.sub("-", ascii_text.lower()).strip("-")
    return slug[:limit].rstrip("-") or "unbekannt"


def safe_filename(name: str) -> str:
    """Returns a file name that cannot escape its folder or hit a reserved Windows name."""
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    base = _UNSAFE_CHARS.sub("_", unicodedata.normalize("NFC", base)).strip(" .")
    stem, suffix = _split_suffix(base)
    stem, suffix = stem[:MAX_STEM_CHARS].strip(" ."), suffix[:MAX_SUFFIX_CHARS]
    if not stem:
        stem = "anhang"
    if stem.lower() in _RESERVED_NAMES:
        stem = f"_{stem}"
    return f"{stem}.{suffix}" if suffix else stem


def _split_suffix(name: str) -> tuple[str, str]:
    """Splits ``name`` at its last dot into stem and extension; without a dot the extension is empty."""
    stem, dot, suffix = name.rpartition(".")
    return (stem, suffix) if dot else (name, "")


def blocked_reason(filename: str, size: int) -> str | None:
    """Returns why an attachment must not be filed, or ``None`` if it may be stored.

    Args:
        filename: The name given in the mail.
        size: The size in bytes.

    Returns:
        A German reason shown in the web interface, or ``None``.
    """
    lowered = safe_filename(filename).lower()  # normalised first: "x.exe." or "x.exe " are blocked too
    if any(lowered.endswith(ext) for ext in BLOCKED_EXTENSIONS):
        return "Ausführbare, makrofähige oder Web-Datei – aus Sicherheitsgründen nicht abgelegt"
    if size > MAX_ATTACHMENT_BYTES:
        return "Datei zu groß"
    if size == 0:
        return "Datei ist leer"
    return None


def _mark_from_internet(path: Path) -> None:
    """Sets the Windows "Mark of the Web" (NTFS stream Zone.Identifier); best effort, never fails."""
    if os.name != "nt":
        return
    try:
        with open(f"{path}:Zone.Identifier", "w", encoding="ascii") as stream:  # noqa: PTH123
            stream.write(_ZONE_IDENTIFIER)
    except OSError:
        _log.debug("Zone.Identifier nicht gesetzt")


def _sha256(data: bytes) -> str:
    """Returns the SHA-256 checksum of ``data`` as hexadecimal text."""
    return hashlib.sha256(data).hexdigest()


def _name_variants(name: str) -> Iterator[str]:
    """Yields ``name`` itself, then ``stem-2.ext``, ``stem-3.ext`` … up to :data:`MAX_NAME_VARIANTS`."""
    stem, dot, suffix = name.rpartition(".")
    if not dot:
        stem, suffix = name, ""
    yield name
    for counter in range(2, MAX_NAME_VARIANTS + 1):
        yield f"{stem}-{counter}{dot}{suffix}"


def _provenance_record(message: MailMessage, filed: list[FiledDocument]) -> dict[str, Any]:
    """Builds the content of :data:`PROVENANCE_FILE`: the message and every document with its checksum."""
    return {
        "nachricht": {
            "schluessel": message.key,
            "message_id": message.message_id,
            "absender": message.sender_display,
            "betreff": message.subject,
            "empfangen": message.received_at.isoformat() if message.received_at else None,
        },
        "dokumente": [
            {
                "originalname": doc.filename,
                "ablage": Path(doc.stored_path).name if doc.stored_path else None,
                "dokumenttyp": doc.document_type,
                "sha256": doc.sha256,
                "groesse": doc.size,
                "nicht_abgelegt": doc.rejected_reason,
            }
            for doc in filed
        ],
        "abgelegt_am": datetime.now(UTC).isoformat(timespec="seconds"),
    }


class DocumentStore:
    """The filing folder: stores the attachments of each message in a folder of its own.

    Attributes:
        root: The top folder; stored paths are relative to it.
    """

    def __init__(self, root: Path) -> None:
        """Files below ``root``."""
        self.root = root

    def folder_for(self, message: MailMessage, analysis: Analysis) -> Path:
        """Returns the folder of one message: year, then date, category, sender and part of the key."""
        received = message.received_at or datetime.now(UTC)
        name = "_".join(
            (
                received.strftime("%Y-%m-%d"),
                analysis.category.value,
                slugify(message.sender_name or message.sender_address.split("@")[0]),
                message.key[:KEY_PREFIX_CHARS],
            )
        )
        return self.root / received.strftime("%Y") / name

    def file(self, message: MailMessage, analysis: Analysis) -> list[FiledDocument]:
        """Stores every permitted attachment of ``message`` and records the outcome of each one.

        Args:
            message: The message with its attachments.
            analysis: Gives the category for the folder and the document types.

        Returns:
            One entry per attachment, filed or not; empty for a message without attachments.
        """
        if not message.attachments:
            return []
        folder = self.folder_for(message, analysis)
        filed = [self._file_one(folder, attachment, analysis) for attachment in message.attachments]
        if any(doc.stored_path for doc in filed):
            try:
                self._write_provenance(folder, message, filed)
            except OSError as exc:
                _log.warning("herkunft.json nicht geschrieben (%s)", type(exc).__name__)
        return filed

    def resolve(self, stored_path: str) -> Path:
        """Returns the absolute path of a stored document.

        Raises:
            ValueError: ``stored_path`` points outside the filing root (``path_outside_filing_root``).
        """
        root = self.root.resolve()
        path = (root / stored_path).resolve()
        if not path.is_relative_to(root) or path == root:
            raise ValueError("path_outside_filing_root")
        return path

    def _file_one(self, folder: Path, attachment: Attachment, analysis: Analysis) -> FiledDocument:
        """Stores one attachment if permitted and describes the outcome."""
        digest = _sha256(attachment.payload)
        stored, reason = self._store(folder, attachment, digest)
        return FiledDocument(
            filename=attachment.filename,
            content_type=attachment.content_type,
            size=attachment.size,
            sha256=digest,
            document_type=analysis.document_type_for(attachment.filename),
            stored_path=stored.relative_to(self.root).as_posix() if stored else None,
            rejected_reason=reason,
            extracted_text=attachment.text,
            text_note=attachment.text_note,
        )

    def _store(self, folder: Path, attachment: Attachment, digest: str) -> tuple[Path | None, str | None]:
        """Returns where the attachment was written, or ``None`` and the reason why it was not."""
        reason = blocked_reason(attachment.filename, attachment.size)
        if reason is not None:
            return None, reason
        try:
            return self._write(folder, attachment.filename, attachment.payload, digest), None
        except OSError as exc:
            _log.warning("Anhang nicht abgelegt (%s)", type(exc).__name__)
            return None, WRITE_FAILED_REASON

    def _write(self, folder: Path, filename: str, payload: bytes, digest: str) -> Path:
        """Writes ``payload`` under a free variant of ``filename``; an identical file is reused.

        Raises:
            OSError: The file could not be written, or every name variant is taken (``no_free_filename``).
        """
        folder.mkdir(parents=True, exist_ok=True)
        for candidate_name in _name_variants(safe_filename(filename)):
            candidate = folder / candidate_name
            if candidate.exists():
                if _sha256(candidate.read_bytes()) == digest:
                    return candidate  # identical document already filed
                continue
            try:
                with candidate.open("xb") as handle:
                    handle.write(payload)
            except FileExistsError:
                continue  # written by someone else in the meantime: try the next name
            _mark_from_internet(candidate)
            return candidate
        raise OSError("no_free_filename")

    def _write_provenance(self, folder: Path, message: MailMessage, filed: list[FiledDocument]) -> None:
        """Writes :data:`PROVENANCE_FILE` into ``folder`` (replacing an older one)."""
        (folder / PROVENANCE_FILE).write_text(
            json.dumps(_provenance_record(message, filed), ensure_ascii=False, indent=2), encoding="utf-8"
        )
