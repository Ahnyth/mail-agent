"""Reads the text of attachments: PDF text layer, OCR for scans and images, plain text.

Everything runs locally. PDFs are parsed with pdfium (pypdfium2); scans and images go to
Tesseract as a separate process with a time limit. Attachments are never executed. The text is
data from an external sender, like the mail body.
"""

import shutil
import subprocess
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass, replace
from io import BytesIO
from pathlib import Path

import pypdfium2 as pdfium

from .models import Attachment, MailMessage
from .parsing import mail_text
from .processes import NO_WINDOW
from .security.redaction import get_logger

MAX_TEXT_CHARS = 20_000
"""Longer texts are cut: enough for every letter or invoice, and the AI prompt stays small."""
MAX_PDF_PAGES = 20
"""Only the text layer of the first pages is read; later pages rarely change the meaning of a mail."""
MAX_OCR_PAGES = 5
"""Scans are recognised only on their first pages, because OCR takes seconds per page."""
MIN_TEXT_LAYER_CHARS = 20
"""A PDF with less text than this is treated as a scan without a text layer."""
OCR_TIMEOUT_SECONDS = 60
"""Upper bound for one Tesseract call (every wait has an upper bound)."""
RENDER_SCALE = 2.0
"""PDF pages are rendered at about 144 dpi for OCR, enough for Tesseract on letters."""
WINDOWS_TESSERACT = Path("C:/Program Files/Tesseract-OCR/tesseract.exe")
"""Where the Windows installer of Tesseract puts the program."""
DEFAULT_LANGUAGE = "deu"
"""Tesseract's German language pack."""
FALLBACK_LANGUAGE = "eng"
"""Used when the configured language pack is missing; part of every Tesseract installation."""

_IMAGE_TYPES = {"image/png", "image/jpeg", "image/tiff", "image/bmp"}
_TEXT_TYPES = {"text/plain", "text/csv"}

Runner = Callable[..., subprocess.CompletedProcess[bytes]]
"""Starts a program like ``subprocess.run``; tests pass a fake."""

_PDFIUM_LOCK = threading.Lock()
"""pdfium is not thread-safe: every call into it holds this lock (OCR itself runs outside)."""

_log = get_logger("attachments")


def find_tesseract(configured: str = "") -> str | None:
    """Returns the configured program, ``tesseract`` from PATH, or the usual Windows install location.

    Args:
        configured: A program name or path from the configuration; empty means search.

    Returns:
        The command to run, or ``None`` if Tesseract is not installed.
    """
    if configured:
        return configured if Path(configured).is_file() or shutil.which(configured) else None
    found = shutil.which("tesseract")
    if found:
        return found
    return str(WINDOWS_TESSERACT) if WINDOWS_TESSERACT.is_file() else None


@dataclass
class TesseractOcr:
    """Text recognition with the Tesseract program, started as a separate process without a window.

    Attributes:
        command: The Tesseract program.
        language: Language packs, e.g. ``deu`` or ``deu+eng``.
        runner: Starts the program; tests pass a fake.
        note: Shown with every recognised attachment, e.g. a hint that the German language pack is missing.
    """

    command: str
    language: str = DEFAULT_LANGUAGE
    runner: Runner = subprocess.run
    note: str = "per Texterkennung gelesen"

    def languages(self) -> set[str]:
        """Returns the installed language packs (``tesseract --list-langs``); empty if Tesseract fails."""
        try:
            completed = self.runner(
                [self.command, "--list-langs"],
                capture_output=True,
                timeout=OCR_TIMEOUT_SECONDS,
                check=False,
                **NO_WINDOW,
            )
        except (OSError, subprocess.TimeoutExpired):
            return set()
        lines = completed.stdout.decode("utf-8", errors="replace").splitlines()
        # The first line is a heading ("List of available languages …"), not a language.
        return {line.strip() for line in lines[1:] if line.strip()}

    def image_to_text(self, image: bytes, suffix: str = ".png") -> str:
        """Returns the text Tesseract recognises in one image.

        Args:
            image: The image file content.
            suffix: The file extension that tells Tesseract the image format.

        Returns:
            The recognised text, unchanged.

        Raises:
            OSError: Tesseract could not be started or reported an error (``ocr_failed``).
            subprocess.TimeoutExpired: Tesseract took longer than :data:`OCR_TIMEOUT_SECONDS`.
        """
        # Tesseract reads files only; the temporary folder is removed right after the call.
        with tempfile.TemporaryDirectory(prefix="mail-agent-ocr-") as root:
            path = Path(root, f"seite{suffix}")
            path.write_bytes(image)
            completed = self.runner(
                [self.command, str(path), "stdout", "-l", self.language],
                capture_output=True,
                timeout=OCR_TIMEOUT_SECONDS,
                check=False,
                **NO_WINDOW,
            )
        if completed.returncode != 0:
            raise OSError("ocr_failed")
        return completed.stdout.decode("utf-8", errors="replace")


@dataclass(frozen=True)
class ReadResult:
    """The text of one attachment, or a note why there is none.

    Attributes:
        text: The cleaned text; ``None`` if nothing could be read.
        note: A German hint for the web interface, e.g. that OCR is not set up.
    """

    text: str | None
    note: str | None = None


def _clean(text: str) -> str:
    """Collapses white space, drops empty lines and cuts the text at :data:`MAX_TEXT_CHARS`."""
    lines = (" ".join(line.split()) for line in text.replace("\r", "").split("\n"))
    cleaned = "\n".join(line for line in lines if line)
    return cleaned[:MAX_TEXT_CHARS]


def _render_pages(document: pdfium.PdfDocument, count: int) -> list[bytes]:
    """Renders the first pages as PNG images for OCR; the caller holds :data:`_PDFIUM_LOCK`."""
    images: list[bytes] = []
    for index in range(min(count, MAX_OCR_PAGES)):
        buffer = BytesIO()
        document[index].render(scale=RENDER_SCALE).to_pil().save(buffer, format="PNG")
        images.append(buffer.getvalue())
    return images


def _pdf_text_or_pages(payload: bytes, render: bool) -> tuple[str, list[bytes]] | None:
    """Returns the text layer of the first pages and, if it is (almost) empty and ``render``, page images.

    Returns:
        The cleaned text and the PNG images for OCR, or ``None`` if the PDF cannot be opened.
    """
    with _PDFIUM_LOCK:
        try:
            document = pdfium.PdfDocument(payload)
        except pdfium.PdfiumError:
            return None
        try:
            count = len(document)
            pages = [document[i].get_textpage().get_text_range() for i in range(min(count, MAX_PDF_PAGES))]
            text = _clean("\n".join(pages))
            images = _render_pages(document, count) if len(text) < MIN_TEXT_LAYER_CHARS and render else []
            return text, images
        finally:
            document.close()


def _read_pdf(payload: bytes, ocr: TesseractOcr | None) -> ReadResult:
    """Reads a PDF: its text layer, or OCR on the first pages of a scan."""
    result = _pdf_text_or_pages(payload, render=ocr is not None)
    if result is None:
        return ReadResult(None, "PDF nicht lesbar (beschädigt oder verschlüsselt)")
    text, images = result
    if len(text) >= MIN_TEXT_LAYER_CHARS:
        return ReadResult(text)
    if ocr is None:
        return ReadResult(None, "Scan ohne Textebene – Texterkennung (Tesseract) nicht eingerichtet")
    recognised = _clean("\n".join(ocr.image_to_text(image) for image in images))
    return ReadResult(recognised or None, ocr.note)


def _read_image(payload: bytes, suffix: str, ocr: TesseractOcr | None) -> ReadResult:
    """Reads an image with OCR, or notes that OCR is not set up."""
    if ocr is None:
        return ReadResult(None, "Bild – Texterkennung (Tesseract) nicht eingerichtet")
    return ReadResult(_clean(ocr.image_to_text(payload, suffix)) or None, ocr.note)


def _read_by_type(attachment: Attachment, ocr: TesseractOcr | None) -> ReadResult:
    """Chooses the reader by content type or file name; unknown types give neither text nor note."""
    content_type = attachment.content_type.lower()
    name = attachment.filename.lower()
    if content_type == "application/pdf" or name.endswith(".pdf"):
        return _read_pdf(attachment.payload, ocr)
    if content_type in _IMAGE_TYPES:
        return _read_image(attachment.payload, Path(name).suffix or ".png", ocr)
    if content_type == "message/rfc822" or name.endswith(".eml"):
        return ReadResult(_clean(mail_text(attachment.payload)) or None)
    if content_type in _TEXT_TYPES:
        return ReadResult(_clean(attachment.payload.decode("utf-8", errors="replace")))
    return ReadResult(None, None)


def read_text(attachment: Attachment, ocr: TesseractOcr | None) -> ReadResult:
    """Returns the text of one attachment, or a note why there is none; never raises for broken input.

    Args:
        attachment: The attachment as it came with the mail.
        ocr: Text recognition for scans and images; ``None`` if Tesseract is not set up.

    Returns:
        The text and an optional German note.
    """
    try:
        return _read_by_type(attachment, ocr)
    except subprocess.TimeoutExpired:
        return ReadResult(None, "Texterkennung hat zu lange gedauert")
    except OSError as exc:
        _log.warning("Texterkennung fehlgeschlagen: %s", type(exc).__name__)
        return ReadResult(None, "Texterkennung fehlgeschlagen")
    except (ValueError, pdfium.PdfiumError) as exc:
        _log.warning("Anhang nicht lesbar: %s", type(exc).__name__)
        return ReadResult(None, "Anhang nicht lesbar")


def read_attachments(message: MailMessage, ocr: TesseractOcr | None, *, enabled: bool = True) -> MailMessage:
    """Returns the message with ``text`` and ``text_note`` set on every attachment.

    Args:
        message: The parsed message.
        ocr: Text recognition for scans and images; ``None`` if Tesseract is not set up.
        enabled: ``False`` returns the message unchanged (reading switched off in the configuration).

    Returns:
        A copy of the message; the original is never changed.
    """
    if not enabled or not message.attachments:
        return message
    attachments = []
    for attachment in message.attachments:
        result = read_text(attachment, ocr)
        attachments.append(replace(attachment, text=result.text, text_note=result.note))
    return replace(message, attachments=tuple(attachments))


def configured_ocr(
    command: str | None, language: str, runner: Runner = subprocess.run
) -> TesseractOcr | None:
    """Returns Tesseract with the configured language, falling back to English if a language pack is missing.

    Args:
        command: The Tesseract program from :func:`find_tesseract`; ``None`` switches OCR off.
        language: The configured language packs, e.g. ``deu``.
        runner: Starts the program; tests pass a fake.

    Returns:
        The text recognition to use, or ``None`` if Tesseract has no usable language.
    """
    if command is None:
        return None
    ocr = TesseractOcr(command, language, runner)
    installed = ocr.languages()
    missing = [lang for lang in language.split("+") if lang not in installed]
    if not missing:
        return ocr
    if FALLBACK_LANGUAGE in installed:
        _log.warning("Tesseract-Sprache fehlt (%s) – Texterkennung auf Englisch", ", ".join(missing))
        return TesseractOcr(
            command,
            FALLBACK_LANGUAGE,
            runner,
            note=f"per Texterkennung gelesen – Sprachpaket {', '.join(missing)} fehlt, Umlaute können fehlen",
        )
    _log.warning("Tesseract ohne nutzbare Sprache – Scans werden ohne Texterkennung abgelegt")
    return None
