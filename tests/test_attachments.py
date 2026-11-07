"""Reading attachments. Tesseract is simulated; pdfium runs for real on generated PDFs."""

import subprocess
from pathlib import Path
from typing import Any

from mail_agent.attachments import (
    TesseractOcr,
    configured_ocr,
    find_tesseract,
    read_attachments,
    read_text,
)
from mail_agent.models import Attachment
from mail_agent.parsing import parse_message
from mail_agent.sample_data import minimal_pdf
from tests.helpers import make_mail

# A valid PDF page without text layer, like a scan.
EMPTY_PDF = minimal_pdf("")


class FakeTesseract:
    def __init__(self, text: str = "Erkannter Text aus dem Scan", returncode: int = 0) -> None:
        self.text = text
        self.returncode = returncode
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        self.calls.append(args)
        assert Path(args[1]).is_file()
        assert kwargs["timeout"] > 0
        return subprocess.CompletedProcess(args, self.returncode, self.text.encode(), b"")


def _ocr(fake: FakeTesseract) -> TesseractOcr:
    return TesseractOcr("tesseract-test", "deu", runner=fake)


def test_pdf_text_layer_with_umlauts() -> None:
    result = read_text(
        Attachment("r.pdf", "application/pdf", minimal_pdf("Rechnung 17\nFällig: 15.11.2026")), None
    )
    assert result.text == "Rechnung 17\nFällig: 15.11.2026"
    assert result.note is None


def test_scan_without_ocr_gets_a_note() -> None:
    result = read_text(Attachment("scan.pdf", "application/pdf", EMPTY_PDF), None)
    assert result.text is None
    assert "Tesseract" in (result.note or "")


def test_scan_with_ocr_renders_pages_for_tesseract() -> None:
    fake = FakeTesseract()
    result = read_text(Attachment("scan.pdf", "application/pdf", EMPTY_PDF), _ocr(fake))
    assert result.text == "Erkannter Text aus dem Scan"
    assert result.note == "per Texterkennung gelesen"
    args = fake.calls[0]
    assert args[0] == "tesseract-test"
    assert args[2:] == ["stdout", "-l", "deu"]


def test_image_and_text_files() -> None:
    fake = FakeTesseract("Foto eines Briefes")
    image = read_text(Attachment("brief.jpg", "image/jpeg", b"\xff\xd8 jpeg"), _ocr(fake))
    assert image.text == "Foto eines Briefes"
    assert fake.calls[0][1].endswith(".jpg")
    assert read_text(Attachment("brief.jpg", "image/jpeg", b"x"), None).text is None
    assert (
        read_text(Attachment("a.txt", "text/plain", b"Hallo  Welt\r\n\r\nZeile"), None).text
        == "Hallo Welt\nZeile"
    )


def test_broken_input_never_raises() -> None:
    assert read_text(Attachment("x.pdf", "application/pdf", b"%PDF-kaputt"), None).note is not None
    failing = FakeTesseract(returncode=1)
    assert (
        read_text(Attachment("b.png", "image/png", b"png"), _ocr(failing)).note
        == "Texterkennung fehlgeschlagen"
    )
    assert read_text(Attachment("a.docx", "application/octet-stream", b"zip"), None) == read_text(
        Attachment("a.docx", "application/octet-stream", b"zip"), None
    )


def test_read_attachments_sets_text_on_the_message() -> None:
    raw = make_mail(attachments=(("r.pdf", "application/pdf", minimal_pdf("Rechnung Nr. 5 über 10,00 EUR")),))
    message = read_attachments(parse_message(raw), None)
    assert message.attachments[0].text == "Rechnung Nr. 5 über 10,00 EUR"
    assert read_attachments(parse_message(raw), None, enabled=False).attachments[0].text is None


def test_find_tesseract(tmp_path: Path) -> None:
    program = tmp_path / "tesseract.exe"
    program.write_bytes(b"")
    assert find_tesseract(str(program)) == str(program)
    assert find_tesseract(str(tmp_path / "fehlt.exe")) is None


class LanguageList:
    def __init__(self, *languages: str) -> None:
        self.output = "List of available languages (n):\n" + "\n".join(languages)

    def __call__(self, args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(args, 0, self.output.encode(), b"")


def test_configured_ocr_checks_language_packs() -> None:
    full = configured_ocr("tesseract", "deu", LanguageList("deu", "eng", "osd"))
    assert full is not None
    assert (full.language, full.note) == ("deu", "per Texterkennung gelesen")
    fallback = configured_ocr("tesseract", "deu", LanguageList("eng", "osd"))
    assert fallback is not None
    assert fallback.language == "eng"
    assert "Sprachpaket deu fehlt" in fallback.note
    assert configured_ocr("tesseract", "deu", LanguageList("osd")) is None
    assert configured_ocr(None, "deu") is None
