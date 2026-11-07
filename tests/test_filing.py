import json
import os
from pathlib import Path

import pytest

from mail_agent.analysis.rules import RuleBasedAnalyzer
from mail_agent.config import OrganizationSettings
from mail_agent.filing import PROVENANCE_FILE, DocumentStore, blocked_reason, safe_filename, slugify
from mail_agent.models import FiledDocument, MailMessage
from mail_agent.parsing import parse_message
from tests.helpers import make_mail


def _mail(*attachments: tuple[str, str, bytes], message_id: str = "<a@example.org>") -> MailMessage:
    return parse_message(make_mail(subject="Unterlagen", attachments=attachments, message_id=message_id))


def _file(store: DocumentStore, message: MailMessage) -> list[FiledDocument]:
    return store.file(message, RuleBasedAnalyzer(OrganizationSettings()).analyze(message))


def test_a_file_that_cannot_be_written_does_not_stop_the_import(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = DocumentStore(tmp_path)

    def blocked(*_args: object) -> Path:
        raise PermissionError("virus scanner")

    monkeypatch.setattr(store, "_write", blocked)
    [filed] = _file(store, _mail(("rechnung.pdf", "application/pdf", b"%PDF-1")))
    assert filed.stored_path is None
    assert filed.rejected_reason is not None
    assert "Ablage fehlgeschlagen" in filed.rejected_reason


@pytest.mark.parametrize("name", ["phish.html", "Rechnung.HTM", "logo.svg", "notiz.one", "x.xll", "a.url"])
def test_web_pages_and_risky_formats_are_not_filed(name: str) -> None:
    assert blocked_reason(name, 10) is not None


@pytest.mark.skipif(os.name != "nt", reason="Mark of the Web exists only on Windows/NTFS")
def test_stored_files_are_marked_as_coming_from_the_internet(tmp_path: Path) -> None:
    [filed] = _file(DocumentStore(tmp_path), _mail(("Vollmacht.pdf", "application/pdf", b"%PDF-1")))
    assert filed.stored_path is not None
    stream = Path(f"{tmp_path / filed.stored_path}:Zone.Identifier")
    assert "ZoneId=3" in stream.read_text(encoding="ascii")


def test_files_attachments_with_provenance(tmp_path: Path) -> None:
    store = DocumentStore(tmp_path)
    message = _mail(("Vollmacht.pdf", "application/pdf", b"%PDF-1"))
    [filed] = _file(store, message)
    assert filed.stored_path is not None
    assert filed.stored_path.startswith("2026/2026-10-28_unterlagen_anna-beispiel_")
    stored = tmp_path / filed.stored_path
    assert stored.read_bytes() == b"%PDF-1"
    provenance = json.loads((stored.parent / PROVENANCE_FILE).read_text(encoding="utf-8"))
    assert provenance["nachricht"]["schluessel"] == message.key
    assert provenance["dokumente"][0]["sha256"] == filed.sha256
    assert provenance["dokumente"][0]["dokumenttyp"] == "Vollmacht"


def test_never_overwrites_different_content(tmp_path: Path) -> None:
    store = DocumentStore(tmp_path)
    first = _file(store, _mail(("scan.pdf", "application/pdf", b"eins")))[0]
    second = _file(store, _mail(("scan.pdf", "application/pdf", b"zwei")))[0]
    assert first.stored_path != second.stored_path
    assert second.stored_path is not None
    assert second.stored_path.endswith("scan-2.pdf")
    assert first.stored_path is not None
    assert (tmp_path / first.stored_path).read_bytes() == b"eins"


def test_identical_content_is_not_duplicated(tmp_path: Path) -> None:
    store = DocumentStore(tmp_path)
    first = _file(store, _mail(("scan.pdf", "application/pdf", b"gleich")))[0]
    second = _file(store, _mail(("scan.pdf", "application/pdf", b"gleich")))[0]
    assert first.stored_path == second.stored_path


@pytest.mark.parametrize("name", ["rechnung.pdf.exe", "makro.docm", "x.EXE", "trick.exe. ", "skript.ps1"])
def test_dangerous_files_are_not_stored(tmp_path: Path, name: str) -> None:
    [filed] = _file(DocumentStore(tmp_path), _mail((name, "application/octet-stream", b"MZ")))
    assert filed.stored_path is None
    assert filed.rejected_reason is not None
    assert not any(tmp_path.rglob("*"))


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("../../etc/passwd", "passwd"),
        ("..\\..\\boot.ini", "boot.ini"),
        ("CON.pdf", "_CON.pdf"),
        ("a<b>c:d.pdf", "a_b_c_d.pdf"),
        ("...", "anhang"),
        ("Pflegegradbescheid Brandt.pdf", "Pflegegradbescheid Brandt.pdf"),
    ],
)
def test_safe_filename(name: str, expected: str) -> None:
    assert safe_filename(name) == expected


def test_resolve_refuses_paths_outside_root(tmp_path: Path) -> None:
    store = DocumentStore(tmp_path / "ablage")
    with pytest.raises(ValueError, match="outside"):
        store.resolve("../geheim.txt")


def test_empty_and_oversized_files_are_rejected() -> None:
    assert blocked_reason("leer.pdf", 0) == "Datei ist leer"
    assert blocked_reason("gross.pdf", 26 * 1024 * 1024) == "Datei zu groß"
    assert blocked_reason("ok.pdf", 10) is None


def test_slugify_transliterates_umlauts() -> None:
    assert slugify("Sanitätshaus Müller & Söhne GmbH") == "sanitaetshaus-mueller-soehne-g"  # 30 characters
    assert slugify("") == "unbekannt"


def test_message_without_attachments_creates_nothing(tmp_path: Path) -> None:
    assert _file(DocumentStore(tmp_path), _mail()) == []
    assert not any(tmp_path.iterdir())
