from pathlib import Path

import pytest

from mail_agent.cli import main
from mail_agent.sample_data import SAMPLES, minimal_pdf, write_samples
from mail_agent.sources.eml_folder import EmlFolderSource


def _config(tmp_path: Path) -> Path:
    path = tmp_path / "mail_agent.toml"
    path.write_text('[analysis]\nengine = "rules"\n', encoding="utf-8")
    return path


def test_cli_samples_and_import(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config = _config(tmp_path)
    assert main(["--config", str(config), "testdaten"]) == 0
    assert main(["--config", str(config), "einlesen"]) == 0
    assert "13 neu eingelesen" in capsys.readouterr().out
    assert main(["--config", str(config), "einlesen"]) == 0
    assert "13 bereits vorhanden" in capsys.readouterr().out


def test_cli_reports_config_and_source_errors(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--config", str(tmp_path / "fehlt.toml"), "einlesen"]) == 2
    assert "Konfigurationsfehler" in capsys.readouterr().err
    assert main(["--config", str(_config(tmp_path)), "einlesen"]) == 3
    assert "eml_folder_missing" in capsys.readouterr().err


def test_write_samples_keeps_existing_files(tmp_path: Path) -> None:
    assert len(write_samples(tmp_path)) == len(SAMPLES)
    (tmp_path / SAMPLES[0].filename).write_bytes(b"eigene Datei")
    assert write_samples(tmp_path) == []
    assert (tmp_path / SAMPLES[0].filename).read_bytes() == b"eigene Datei"
    assert len(write_samples(tmp_path, overwrite=True)) == len(SAMPLES)


def test_minimal_pdf_structure() -> None:
    pdf = minimal_pdf("Test (Klammer)")
    assert pdf.startswith(b"%PDF-1.4")
    assert pdf.rstrip().endswith(b"%%EOF")
    xref_offset = int(pdf.rsplit(b"startxref\n", 1)[1].split(b"\n")[0])
    assert pdf[xref_offset:].startswith(b"xref")


def test_eml_folder_source_skips_oversized_files(tmp_path: Path) -> None:
    (tmp_path / "klein.eml").write_bytes(b"x")
    (tmp_path / "gross.eml").write_bytes(b"x" * 20)
    (tmp_path / "andere.txt").write_bytes(b"x")
    origins = [mail.origin for mail in EmlFolderSource(tmp_path, max_bytes=10).fetch()]
    assert origins == ["eml:klein.eml"]
