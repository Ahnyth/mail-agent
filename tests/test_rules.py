import pytest

from mail_agent.analysis.rules import (
    MISSING_ATTACHMENT_TASK,
    RuleBasedAnalyzer,
    document_type,
    is_automated_sender,
    summarize,
)
from mail_agent.config import OrganizationSettings
from mail_agent.models import Category, Urgency
from mail_agent.parsing import parse_message
from tests.helpers import make_mail

EXPECTED = {
    "01_pflegeanfrage_entlassung.eml": (Category.CARE_REQUEST, Urgency.HIGH),
    "02_termin_verschieben.eml": (Category.APPOINTMENT, Urgency.NORMAL),
    "03_kostenzusage_pflegekasse.eml": (Category.INSURANCE, Urgency.NORMAL),
    "04_begutachtung_md.eml": (Category.INSURANCE, Urgency.NORMAL),
    "05_verordnung_hausarzt.eml": (Category.PHYSICIAN, Urgency.NORMAL),
    "06_rechnung_hilfsmittel.eml": (Category.BILLING, Urgency.NORMAL),
    "07_bewerbung_pflegefachkraft.eml": (Category.APPLICATION, Urgency.NORMAL),
    "08_beschwerde_verspaetung.eml": (Category.COMPLAINT, Urgency.HIGH),
    "09_sturz_angehoerige.eml": (Category.APPOINTMENT, Urgency.HIGH),
    "10_newsletter_fortbildung.eml": (Category.ADVERTISING, Urgency.LOW),
    "11_unterlagen_vollmacht.eml": (Category.DOCUMENTS, Urgency.NORMAL),
    "12_rechnungskorrektur_injection.eml": (Category.SUSPICIOUS, Urgency.HIGH),
    "13_paket_phishing.eml": (Category.SUSPICIOUS, Urgency.LOW),
}


@pytest.fixture
def analyzer(organization: OrganizationSettings) -> RuleBasedAnalyzer:
    return RuleBasedAnalyzer(organization)


@pytest.mark.parametrize(("filename", "expected"), EXPECTED.items())
def test_sample_mails_are_categorised(
    analyzer: RuleBasedAnalyzer,
    sample_raw: dict[str, bytes],
    filename: str,
    expected: tuple[Category, Urgency],
) -> None:
    analysis = analyzer.analyze(parse_message(sample_raw[filename]))
    assert (analysis.category, analysis.urgency) == expected
    assert analysis.reasons
    assert analysis.engine == "regeln"


def test_every_sample_is_covered(sample_raw: dict[str, bytes]) -> None:
    assert set(sample_raw) == set(EXPECTED)


def test_reply_contains_salutation_placeholder_and_signature(analyzer: RuleBasedAnalyzer) -> None:
    analysis = analyzer.analyze(parse_message(make_mail(subject="Rechnung 123", body="Stimmt der Betrag?")))
    assert analysis.reply_needed
    assert analysis.reply_subject == "Re: Rechnung 123"
    assert analysis.reply_body.startswith("Guten Tag Anna Beispiel,")
    assert "[" in analysis.reply_body  # placeholder for the team
    assert analysis.reply_body.endswith("Ihr Musterpflege-Team")


def test_injection_attempt_is_flagged_and_gets_no_draft(
    analyzer: RuleBasedAnalyzer, sample_raw: dict[str, bytes]
) -> None:
    analysis = analyzer.analyze(parse_message(sample_raw["12_rechnungskorrektur_injection.eml"]))
    assert any("Anweisungen an ein KI-System" in w for w in analysis.warnings)
    assert any("Betrugsversuch" in w for w in analysis.warnings)
    assert not analysis.reply_needed
    assert analysis.reply_body == ""


def test_advertising_needs_no_reply(analyzer: RuleBasedAnalyzer, sample_raw: dict[str, bytes]) -> None:
    analysis = analyzer.analyze(parse_message(sample_raw["10_newsletter_fortbildung.eml"]))
    assert not analysis.reply_needed
    assert analysis.tasks == ()


def test_urgency_is_not_written_into_the_reply(analyzer: RuleBasedAnalyzer) -> None:
    analysis = analyzer.analyze(parse_message(make_mail(subject="Dringend: Termin", body="Bitte sofort.")))
    assert analysis.urgency is Urgency.HIGH
    assert "dringend" not in analysis.reply_body.lower()


def test_specific_category_wins_over_generic_documents(analyzer: RuleBasedAnalyzer) -> None:
    mail = make_mail(
        subject="Bewerbungsunterlagen",
        body="Hiermit bewerbe ich mich als Pflegehelfer.\n\nAnbei meine Unterlagen.",
        attachments=(("Unterlagen.pdf", "application/pdf", b"%PDF-1.4"),),
    )
    assert analyzer.analyze(parse_message(mail)).category is Category.APPLICATION


def test_documents_category_remains_when_nothing_specific_matches(analyzer: RuleBasedAnalyzer) -> None:
    mail = make_mail(
        subject="Unterlagen",
        body="Anbei die Unterlagen.",
        attachments=(("scan.pdf", "application/pdf", b"%PDF-1.4"),),
    )
    analysis = analyzer.analyze(parse_message(mail))
    assert analysis.category is Category.DOCUMENTS
    assert MISSING_ATTACHMENT_TASK not in analysis.tasks


def test_announced_but_missing_attachment_becomes_first_task(analyzer: RuleBasedAnalyzer) -> None:
    analysis = analyzer.analyze(parse_message(make_mail(subject="Rechnung", body="Anbei die Rechnung.")))
    assert analysis.category is Category.BILLING
    assert analysis.tasks[0] == MISSING_ATTACHMENT_TASK
    assert any("nicht vorhanden" in reason for reason in analysis.reasons)


def test_documents_are_typed_by_filename(analyzer: RuleBasedAnalyzer, sample_raw: dict[str, bytes]) -> None:
    analysis = analyzer.analyze(parse_message(sample_raw["07_bewerbung_pflegefachkraft.eml"]))
    assert [d.document_type for d in analysis.documents] == ["Lebenslauf", "Zeugnis"]


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("Verordnung_HKP.pdf", "Ärztliche Verordnung"),
        ("scan_0001.pdf", "Dokument"),
        ("KOSTENZUSAGE.PDF", "Kostenzusage"),
    ],
)
def test_document_type(filename: str, expected: str) -> None:
    assert document_type(filename) == expected


def test_summary_skips_greeting_quotes_and_signature() -> None:
    body = "Sehr geehrte Damen und Herren,\n\n> alter Text\nNeuer Inhalt.\n--\nSignatur"
    assert summarize(body, "Betreff") == "Neuer Inhalt."


@pytest.mark.parametrize("greeting", ["Sehr geehrter Kunde,", "Sehr geehrtes Team,", "Lieber Herr Wagner,"])
def test_summary_skips_greeting_variants(greeting: str) -> None:
    assert summarize(f"{greeting}\nInhalt.", "") == "Inhalt."


def test_summary_is_shortened() -> None:
    summary = summarize("Wort " * 200, "")
    assert len(summary) <= 280
    assert summary.endswith("…")


def test_unknown_sender_name_uses_formal_salutation(analyzer: RuleBasedAnalyzer) -> None:
    analysis = analyzer.analyze(parse_message(make_mail(sender="info@example.org", body="Haben Sie Zeit?")))
    assert analysis.reply_body.startswith("Sehr geehrte Damen und Herren,")


@pytest.mark.parametrize("closing", ["Mit freundlichen Grüßen.", "Viele Grüße", "LG", "Beste Grüsse"])
def test_summary_stops_at_closing_formula(closing: str) -> None:
    assert summarize(f"Bitte überweisen.\nDanke\n{closing}\nErika M.", "") == "Bitte überweisen. Danke"


def test_summary_skips_forward_header_and_image_placeholders() -> None:
    body = (
        "---------- Forwarded message ---------\n"
        "Von: Kasse <info@kasse.example>\n"
        "Date: Do., 29. Okt. 2026\n"
        "Subject: Ein sehr langer Betreff, der umbricht\n"
        "und weitergeht\n"
        "To: <team@example.org>\n"
        "\n"
        "[image: Logo]\n"
        "<https://example.org/tracking>\n"
        "Ihr Antrag wurde bewilligt.\n"
    )
    assert summarize(body, "Fwd: Bescheid") == "Ihr Antrag wurde bewilligt."


def test_summary_handles_outlook_forward_marker() -> None:
    body = "Zur Info.\n\n-----Ursprüngliche Nachricht-----\nVon: A\nBetreff: B\n\nTermin am Montag."
    assert summarize(body, "") == "Zur Info. Termin am Montag."


def test_summary_of_quote_only_reply_uses_the_quote() -> None:
    body = "Am Di., 27. Okt. 2026 um 10:15 Uhr schrieb Praxis <info@praxis.example>:\n\n> Hallo!\n>\n> > Die Verordnung liegt bei."
    assert summarize(body, "Re: Verordnung") == "Die Verordnung liegt bei."


@pytest.mark.parametrize(
    ("address", "automated"),
    [
        ("no-reply@accounts.example", True),
        ("kdp-no-reply@shop.example", True),
        ("noreply@kasse.example", True),
        ("donotreply@bank.example", True),
        ("anna@example.org", False),
        ("info@noreply-gmbh.example", False),  # only the local part counts
    ],
)
def test_automated_sender(address: str, automated: bool) -> None:
    assert is_automated_sender(address) is automated


def test_automated_sender_gets_no_draft(analyzer: RuleBasedAnalyzer) -> None:
    mail = make_mail(
        sender="Kasse <noreply@kasse.example>", subject="Ihr Leistungsbescheid", body="Siehe Portal."
    )
    analysis = analyzer.analyze(parse_message(mail))
    assert not analysis.reply_needed
    assert analysis.reply_body == ""
    assert any("Automatische Absenderadresse" in reason for reason in analysis.reasons)
