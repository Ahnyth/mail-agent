"""The evaluation collection and its runner. Only the rules run here – no AI."""

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from mail_agent.analysis.rules import RuleBasedAnalyzer
from mail_agent.config import OrganizationSettings
from mail_agent.eval.__main__ import main
from mail_agent.eval.anonymize import anonymize_text
from mail_agent.eval.cases import EvalCaseError, load_cases, parse_case
from mail_agent.eval.runner import format_report, run
from mail_agent.models import Category
from mail_agent.senders import load_senders

EVAL = Path(__file__).parent / "eval"
# Regression floor for the rules alone; the AI is measured with `python -m mail_agent.eval`.
RULES_CATEGORY_FLOOR = 0.8


@pytest.fixture(scope="module")
def cases():
    return load_cases(EVAL / "faelle")


def test_collection_is_large_valid_and_fictitious(cases) -> None:  # type: ignore[no-untyped-def]
    assert len(cases) >= 50
    assert len({c.id for c in cases}) == len(cases)
    assert {c.expected.category for c in cases} == set(Category)
    for case in cases:
        assert case.message.sender_address.endswith(".example"), case.id
    for path in (EVAL / "faelle").glob("*.yaml"):
        assert path.read_text(encoding="utf-8").startswith("# Erfundener Testfall"), path.name


def test_rules_baseline(cases) -> None:  # type: ignore[no-untyped-def]
    senders = load_senders(EVAL / "bekannte_absender.yaml")
    report = run(cases, RuleBasedAnalyzer(OrganizationSettings(), senders), parallel=2)
    assert report.score("kategorie").rate >= RULES_CATEGORY_FLOOR
    assert not [r for r in report.unnecessary_drafts if "weiterleitung" in r.tags]
    text = format_report(report)
    assert "Trefferquote je Feld" in text
    assert "kategorie" in text


def _case(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": "x-01",
        "mail": {"von": "a@b.example", "betreff": "B", "datum": "2026-10-28T10:00:00+01:00", "text": "Hallo"},
        "soll": {"kategorie": "termin", "prioritaet": "normal", "entwurf": True},
    }
    data.update(overrides)
    return data


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ([], "keine Zuordnung"),
        (_case(soll={"kategorie": "x", "prioritaet": "normal", "entwurf": True}), "nicht erlaubt"),
        (_case(soll={"kategorie": "termin", "prioritaet": "normal"}), "entwurf"),
        (_case(mail={"von": "a@b.example", "betreff": "B", "datum": "gestern", "text": "x"}), "ISO-Datum"),
    ],
)
def test_invalid_cases_are_rejected(data: Any, message: str) -> None:
    with pytest.raises(EvalCaseError, match=message):
        parse_case(data, "x.yaml")


def test_cli_rules_mode_writes_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:  # type: ignore[no-untyped-def]
    cases = tmp_path / "faelle"
    cases.mkdir()
    (cases / "x-01.yaml").write_text(yaml.safe_dump(_case(), allow_unicode=True), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    code = main(
        ["--modus", "regeln", "--faelle", str(cases), "--bericht", str(tmp_path / "b"), "--mindestens", "0"]
    )
    assert code == 0
    report = json.loads(next((tmp_path / "b").glob("*.json")).read_text(encoding="utf-8"))
    assert report["modus"] == "regeln"
    assert report["felder"]["kategorie"]["total"] == 1
    assert "1 Fälle" in capsys.readouterr().out


def test_cli_refuses_ai_mode_without_ai(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "mail_agent.toml").write_text('[analysis]\nengine = "rules"\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert main(["--modus", "ki", "--faelle", str(EVAL / "faelle"), "--nur", "termin"]) == 2


def test_anonymize_text() -> None:
    text = (
        "Frau Erika Mustermann, geb. 03.04.1941, Versichertennr. K123456789, wohnt Lindenstraße 12, "
        "34567 Kassel, Tel. 0561 987654, mail erika@privat.example, https://x.example/a?token=1, "
        "IBAN DE44 5001 0517 5407 3249 31. Gruß Klaus Beispiel"
    )
    result = anonymize_text(text, ("Klaus", "Beispiel"))
    for secret in (
        "Mustermann",
        "1941",
        "K123456789",
        "Lindenstraße",
        "Kassel",
        "987654",
        "erika@",
        "token",
        "5407",
    ):
        assert secret not in result, secret
    assert "Klaus" not in result
    assert "Beispiel" not in result
    assert "Frau Muster" in result
