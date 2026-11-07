import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from mail_agent.analysis.rules import RuleBasedAnalyzer
from mail_agent.config import OrganizationSettings
from mail_agent.models import Category, FiledDocument, Responsible, Status, Urgency
from mail_agent.parsing import parse_message
from mail_agent.storage import SCHEMA_VERSION, CorrectionChange, InboxFilter, MailRepository
from tests.helpers import make_mail


@pytest.fixture
def repo(tmp_path: Path) -> MailRepository:
    return MailRepository(tmp_path / "db" / "test.sqlite3")


def _add(
    repo: MailRepository, *, message_id: str, subject: str, body: str = "Text", date: str | None = None
) -> int:
    raw = make_mail(
        message_id=message_id, subject=subject, body=body, date=date or "Wed, 28 Oct 2026 10:00:00 +0100"
    )
    message = parse_message(raw)
    analysis = RuleBasedAnalyzer(OrganizationSettings()).analyze(message)
    document = FiledDocument("a.pdf", "application/pdf", 3, "ab" * 32, "Dokument", "2026/x/a.pdf")
    return repo.messages.add(message, origin="eml:test.eml", raw=raw, analysis=analysis, documents=[document])


def test_roundtrip(repo: MailRepository) -> None:
    message_id = _add(repo, message_id="<1@x>", subject="Rechnung 1")
    detail = repo.messages.get(message_id)
    assert detail is not None
    assert detail.subject == "Rechnung 1"
    assert detail.status is Status.OPEN
    assert detail.analysis.category is Category.BILLING
    assert detail.documents[0].stored_path == "2026/x/a.pdf"
    assert repo.messages.raw(message_id) is not None
    assert repo.documents.get(detail.documents[0].id) == detail.documents[0]


def test_contains_and_unique_key(repo: MailRepository) -> None:
    _add(repo, message_id="<1@x>", subject="A")
    key = parse_message(make_mail(message_id="<1@x>")).key
    assert repo.messages.contains(key)
    assert not repo.messages.contains("unbekannt")


def test_list_sorts_open_urgent_newest_first_and_filters(repo: MailRepository) -> None:
    normal_old = _add(repo, message_id="<1@x>", subject="Rechnung", date="Mon, 26 Oct 2026 10:00:00 +0100")
    normal_new = _add(repo, message_id="<2@x>", subject="Rechnung", date="Wed, 28 Oct 2026 10:00:00 +0100")
    urgent = _add(repo, message_id="<3@x>", subject="Dringend: Termin")
    done = _add(repo, message_id="<4@x>", subject="Dringend: Rechnung")
    repo.workflow.set_status(done, Status.DONE)

    assert [m.id for m in repo.inbox.messages()] == [urgent, normal_new, normal_old, done]
    assert [
        m.id for m in repo.inbox.messages(InboxFilter(status=Status.OPEN, category=Category.BILLING))
    ] == [
        normal_new,
        normal_old,
    ]
    counts = repo.inbox.counts()
    assert counts["offen"] == 3
    assert counts["erledigt"] == 1
    assert counts["dringend"] == 1
    assert counts[Category.BILLING.value] == 2


def test_save_draft_and_replace_analysis(repo: MailRepository) -> None:
    message_id = _add(repo, message_id="<1@x>", subject="Rechnung")
    assert repo.analyses.save_draft(message_id, "Neuer Text")
    detail = repo.messages.get(message_id)
    assert detail is not None
    assert detail.draft_edited
    assert detail.analysis.reply_body == "Neuer Text"

    analysis = replace(detail.analysis, urgency=Urgency.HIGH, reply_body="KI-Text")
    repo.analyses.replace(message_id, analysis)
    updated = repo.messages.get(message_id)
    assert updated is not None
    assert updated.analysis.urgency is Urgency.HIGH
    assert not updated.draft_edited


def test_unknown_ids(repo: MailRepository) -> None:
    assert repo.messages.get(999) is None
    assert repo.messages.raw(999) is None
    assert repo.documents.get(999) is None
    assert not repo.workflow.set_status(999, Status.DONE)
    assert not repo.analyses.save_draft(999, "x")


def test_new_analysis_fields_round_trip(repo: MailRepository) -> None:
    raw = make_mail(
        message_id="<f@x>", subject="Rechnung", body="Rechnung 7 über 10,00 EUR bis zum 30.11.2026."
    )
    message = parse_message(raw)
    analysis = RuleBasedAnalyzer(OrganizationSettings()).analyze(message)
    analysis = replace(analysis, findings=("Anhang fehlt",), confidence=0.42, duration_ms=1234)
    message_id = repo.messages.add(message, origin="eml:f.eml", raw=raw, analysis=analysis, documents=[])
    stored = repo.messages.get(message_id)
    assert stored is not None
    assert stored.analysis == analysis
    assert stored.analysis.payment is not None
    assert stored.analysis.deadlines
    assert repo.inbox.messages()[0].responsible is analysis.responsible


def test_old_database_is_migrated(tmp_path: Path) -> None:
    path = tmp_path / "alt.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.executescript(
            "CREATE TABLE analyses (message_id INTEGER PRIMARY KEY, category TEXT NOT NULL, urgency TEXT NOT NULL,"
            " summary TEXT NOT NULL, reasons TEXT NOT NULL, tasks TEXT NOT NULL, reply_needed INTEGER NOT NULL,"
            " reply_subject TEXT NOT NULL, reply_body TEXT NOT NULL, documents TEXT NOT NULL,"
            " warnings TEXT NOT NULL, engine TEXT NOT NULL, draft_edited INTEGER NOT NULL DEFAULT 0,"
            " analysed_at TEXT NOT NULL);"
            "PRAGMA user_version = 1;"
        )
    repo = MailRepository(path)
    _add(repo, message_id="<m@x>", subject="Termin")
    with sqlite3.connect(path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(analyses)")}
        version = conn.execute("PRAGMA user_version").fetchone()[0]
    assert {
        "findings",
        "deadlines",
        "payment",
        "responsible",
        "confidence",
        "duration_ms",
        "ai_pending",
    } <= columns
    assert version == SCHEMA_VERSION
    assert repo.positions.get("imap:x") is None
    assert repo.runs.last() is None


def test_reanalysis_keeps_a_manual_correction(repo: MailRepository) -> None:
    message_id = _add(repo, message_id="<k@x>", subject="Termin")
    repo.corrections.add(
        message_id,
        CorrectionChange(
            category=Category.COMPLAINT,
            urgency=Urgency.HIGH,
            reply_needed=True,
            responsible=Responsible.MANAGEMENT,
        ),
    )
    detail = repo.messages.get(message_id)
    assert detail is not None
    assert repo.analyses.replace(message_id, replace(detail.analysis, category=Category.OTHER, summary="neu"))
    after = repo.messages.get(message_id)
    assert after is not None
    assert after.analysis.summary == "neu"
    assert (after.analysis.category, after.analysis.urgency) == (Category.COMPLAINT, Urgency.HIGH)
    assert after.analysis.responsible is Responsible.MANAGEMENT
    # the bulk AI catch-up leaves corrected messages alone
    assert not repo.analyses.replace(message_id, detail.analysis, only_if_untouched=True)


def test_details_do_not_load_the_raw_message(repo: MailRepository) -> None:
    message_id = _add(repo, message_id="<r@x>", subject="Termin")
    [detail] = repo.messages.details()
    assert detail.id == message_id
    assert detail.subject == "Termin"
    assert len(detail.documents) == 1


def test_correction_keeps_original_and_updates_analysis(repo: MailRepository) -> None:
    message_id = _add(repo, message_id="<k@x>", subject="Rechnung", body="Anbei")
    assert repo.corrections.add(
        message_id,
        CorrectionChange(
            category=Category.ADVERTISING,
            urgency=Urgency.LOW,
            reply_needed=False,
            responsible=Responsible.ADMINISTRATION,
            note="Newsletter",
        ),
    )
    detail = repo.messages.get(message_id)
    assert detail is not None
    assert detail.correction is not None
    assert detail.correction.original_category is Category.BILLING
    assert (detail.analysis.category, detail.analysis.urgency) == (Category.ADVERTISING, Urgency.LOW)
    assert not detail.analysis.reply_needed
    repo.corrections.mark_exported(detail.correction.id, "x.yaml")
    assert repo.corrections.all()[0].exported_to == "x.yaml"
    assert not repo.corrections.add(
        999,
        CorrectionChange(category=Category.OTHER, urgency=Urgency.LOW, reply_needed=False, responsible=None),
    )
