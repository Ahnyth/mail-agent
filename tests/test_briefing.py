"""Briefing, deadlines, invoices, follow-ups, applications and exports – all without AI."""

import csv
import io
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from mail_agent.analysis.rules import RuleBasedAnalyzer
from mail_agent.applications import checklist, confirmation, load_template
from mail_agent.attachments import read_attachments
from mail_agent.briefing import build_briefing, deadline_items, invoice_rows, spoken_date, unanswered
from mail_agent.config import OrganizationSettings
from mail_agent.exports import deadlines_ics, invoices_csv
from mail_agent.models import Category, Status
from mail_agent.parsing import parse_message
from mail_agent.sample_data import minimal_pdf
from mail_agent.senders import KnownSender, SenderDirectory
from mail_agent.storage import MailRepository, MessageDetail
from tests.helpers import make_mail

PROJECT = Path(__file__).parent.parent
IBAN = "DE89 3704 0044 0532 0130 00"
OTHER = "DE44 5001 0517 5407 3249 31"
SENDERS = SenderDirectory(
    senders=(
        KnownSender("Lindner", "lieferant", domains=("lindner.example",), ibans=("DE89370400440532013000",)),
    )
)
TODAY = date(2026, 11, 3)


def _store(repo: MailRepository, n: int, **mail: object) -> int:
    raw = make_mail(message_id=f"<b{n}@x>", **mail)  # type: ignore[arg-type]
    message = read_attachments(parse_message(raw), None)
    analysis = RuleBasedAnalyzer(OrganizationSettings(), SENDERS).analyze(message)
    return repo.messages.add(message, origin="test", raw=raw, analysis=analysis, documents=[])


@pytest.fixture
def details(tmp_path: Path) -> list[MessageDetail]:
    repo = MailRepository(tmp_path / "db.sqlite3")
    _store(
        repo, 1, sender="Petra <p@example.org>", subject="Sturz heute", body="Vater gestürzt, bitte anrufen?"
    )
    _store(
        repo,
        2,
        sender="AOK <leistung@aok.example>",
        subject="Unterlagen",
        body="Bitte senden Sie die Nachweise bis zum 10.11.2026.",
        date="Mon, 02 Nov 2026 09:00:00 +0100",
    )
    _store(
        repo,
        3,
        sender="r@lindner.example",
        subject="Rechnung 2026-07",
        body=f"Rechnung 2026-07 über 99,00 EUR. {IBAN}",
    )
    _store(
        repo,
        4,
        sender="r@lindner.example",
        subject="Rechnung 2026-08",
        body=f"Rechnung 2026-08 über 50,00 EUR auf {OTHER}",
    )
    done = _store(repo, 5, sender="Anna <a@example.org>", subject="Termin", body="Passt Montag?")
    repo.workflow.set_status(done, Status.DONE)
    return repo.messages.details()


def test_deadlines_invoices_and_follow_up(details: list[MessageDetail]) -> None:
    items = deadline_items(details, TODAY, 14)
    assert [(i.deadline.due, i.days_left) for i in items] == [(date(2026, 11, 10), 7)]
    assert deadline_items(details, TODAY, 3) == []

    rows = {r.payment.invoice_number: r for r in invoice_rows(details, SENDERS)}
    assert rows["2026-07"].iban_check == "passt"
    assert rows["2026-08"].iban_check == "Verdacht"  # changed IBAN makes it a suspicious message

    late = unanswered(details, TODAY, 3)
    assert [m.subject for m in late] == ["Sturz heute"]  # done and recent messages are not listed
    assert unanswered(details, date(2026, 10, 29), 3) == []


def test_briefing_and_speech_text(details: list[MessageDetail]) -> None:
    briefing = build_briefing(details, SENDERS, TODAY, horizon_days=14, follow_up_days=3)
    assert [m.subject for m in briefing.urgent] == ["Sturz heute"]
    assert [r.payment.invoice_number for r in briefing.payments] == ["2026-07"]
    text = briefing.speech_text()
    assert text.startswith("Guten Morgen. Heute ist Dienstag, der 3. November.")
    assert "Eine dringende Mail" in text
    assert "die nächste am 10. November" in text
    assert "anrufen" not in text  # the mail body is never read aloud, only sender and subject
    assert spoken_date(date(2026, 12, 24), weekday=False) == "24. Dezember"


def test_ics_export(details: list[MessageDetail]) -> None:
    long = replace(details[1].analysis.deadlines[0], description="Nachweise; Pflegeplanung, Wunddoku " * 5)
    item = replace(deadline_items(details, TODAY, None)[0], deadline=long)
    ics = deadlines_ics([item], "http://127.0.0.1:8765", datetime(2026, 11, 3, 8, tzinfo=UTC))
    assert ics.startswith("BEGIN:VCALENDAR\r\n")
    assert "DTSTART;VALUE=DATE:20261110\r\n" in ics
    assert "DTSTAMP:20261103T080000Z" in ics
    assert "TRIGGER:-P1D" in ics
    assert "\\;" in ics and "\\," in ics  # noqa: PT018
    assert all(len(line.encode()) <= 75 for line in ics.split("\r\n"))
    assert "http://127.0.0.1:8765/nachrichten/" in ics.replace("\r\n ", "")


def test_csv_export_neutralises_formulas(details: list[MessageDetail]) -> None:
    rows = invoice_rows(details, SENDERS)
    evil = replace(rows[0], payment=replace(rows[0].payment, invoice_number="=HYPERLINK(1)"))
    content = invoices_csv([evil, *rows[1:]])
    assert content.startswith("﻿Achtung;Eingang;Absender;Lieferant")
    table = list(csv.reader(io.StringIO(content.lstrip("﻿")), delimiter=";"))
    assert table[1][4] == "'=HYPERLINK(1)"
    assert table[1][3] == "Lindner"
    # suspicious invoices keep their warning in Excel
    flagged = [row for row in table[1:] if row[7] in {"Verdacht", "weicht ab"}]
    assert flagged
    assert all(row[0] == "NICHT ZAHLEN – erst prüfen" for row in flagged)


def test_ics_keeps_line_breaks_and_unique_ids(details: list[MessageDetail]) -> None:
    item = deadline_items(details, TODAY, None)[0]
    ics = deadlines_ics([item, item], "http://127.0.0.1:8765", datetime(2026, 11, 3, 8, tzinfo=UTC))
    unfolded = ics.replace("\r\n ", "")
    assert "Vorschlag des MailAgent – bitte prüfen.\\nMail: " in unfolded
    uids = [line for line in unfolded.split("\r\n") if line.startswith("UID:")]
    assert len(set(uids)) == 2


def test_application_checklist_and_confirmation() -> None:
    items = checklist(
        "Bewerbung Pflegefachkraft",
        "Anbei meine Unterlagen.",
        [("Lebenslauf_Muster.pdf", None), ("scan.pdf", "Arbeitszeugnis Klinikum")],
    )
    assert items.present == ("Lebenslauf", "Zeugnisse")
    assert items.missing == ("Anschreiben", "Examen / Berufserlaubnis")
    helper = checklist("Bewerbung als Pflegehelfer", "x" * 500, [("cv.pdf", None), ("Zeugnis.pdf", None)])
    assert helper.complete
    assert not helper.exam_required

    template = load_template(PROJECT / "config" / "vorlagen" / "bewerbung_eingang.md")
    assert not template.startswith("#")
    text = confirmation(
        template, salutation="Guten Tag Jana Beispiel,", organization="Musterpflege GmbH", items=items
    )
    assert text.startswith("Guten Tag Jana Beispiel,")
    assert "Musterpflege GmbH" in text
    assert "schicken Sie uns bitte noch: Examen / Berufserlaubnis." in text  # no cover letter request
    complete = confirmation(template, salutation="Hallo,", organization="X", items=helper)
    assert "schicken Sie uns" not in complete
    assert "{" not in complete


def test_rules_use_fixed_application_template_and_attachment_text() -> None:
    pdf = minimal_pdf("Lebenslauf\nZeugnis der Pflegeschule")
    raw = make_mail(
        sender="Jana Beispiel <jana@example.org>",
        subject="Bewerbung Pflegehelferin",
        body="Hiermit bewerbe ich mich als Pflegehelferin.",
        attachments=(("unterlagen.pdf", "application/pdf", pdf),),
    )
    message = read_attachments(parse_message(raw), None)
    analysis = RuleBasedAnalyzer(OrganizationSettings(name="Musterpflege GmbH")).analyze(message)
    assert analysis.category is Category.APPLICATION
    assert analysis.reply_body.startswith("Guten Tag Jana Beispiel,")
    assert "schicken Sie uns" not in analysis.reply_body  # only the cover letter is missing
    assert "Sie ist bei uns eingegangen" in analysis.reply_body
    assert analysis.documents[0].document_type == "Lebenslauf"  # from the text, not the file name
    assert analysis.read_aloud.startswith("Bewerbung von Jana Beispiel")
