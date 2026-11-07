from datetime import UTC, date, datetime

import pytest

from mail_agent.analysis.rules import RuleBasedAnalyzer
from mail_agent.analysis.signals import (
    MailSignals,
    detect,
    find_deadlines,
    find_ibans,
    find_payment,
    iban_is_valid,
    security_warnings,
)
from mail_agent.config import OrganizationSettings
from mail_agent.models import Category, Responsible, Urgency
from mail_agent.parsing import parse_message
from mail_agent.senders import KnownSender, SenderDirectory
from tests.helpers import make_mail

SUPPLIER_IBAN = "DE89 3704 0044 0532 0130 00"
OTHER_IBAN = "DE44 5001 0517 5407 3249 31"
SENDERS = SenderDirectory(
    own_domains=("musterpflege.example",),
    senders=(
        KnownSender("Lindner", "lieferant", domains=("lindner.example",), ibans=("DE89370400440532013000",)),
        KnownSender("Pflege aktuell", "newsletter", domains=("news.example",)),
        KnownSender("AOK", "kostentraeger", domains=("aok.example",)),
    ),
)


def _signals(sender: str, body: str, subject: str = "Betreff"):  # type: ignore[no-untyped-def]
    return detect(parse_message(make_mail(sender=sender, subject=subject, body=body)), SENDERS)


def test_iban_check_digits() -> None:
    assert iban_is_valid(SUPPLIER_IBAN)
    assert not iban_is_valid("DE00 3704 0044 0532 0130 00")
    assert find_ibans(f"Konto: {SUPPLIER_IBAN}, alt: DE00 1234 5678 9012 3456 78") == (
        "DE89370400440532013000",
    )


def test_known_supplier_with_deposited_iban_gets_no_warning() -> None:
    signals = _signals("rechnung@lindner.example", f"Rechnung 12 über 99,00 EUR. IBAN {SUPPLIER_IBAN}")
    assert signals.known
    assert signals.payment_request
    assert security_warnings(signals) == ()


def test_known_supplier_with_other_iban_is_warned() -> None:
    signals = _signals("rechnung@lindner.example", f"Rechnung 12. Bitte auf {OTHER_IBAN} zahlen.")
    assert signals.iban_mismatch == ("DE44500105175407324931",)
    assert "weicht von der hinterlegten" in security_warnings(signals)[0]


def test_lookalike_domain_with_bank_change_is_warned() -> None:
    signals = _signals(
        "rechnung@lindner-rechnung.example",
        f"Unsere Bankverbindung hat sich geändert. Bitte überweisen Sie sofort auf {OTHER_IBAN}.",
    )
    assert not signals.known
    warnings = security_warnings(signals)
    assert any("geänderte Bankverbindung" in w for w in warnings)
    assert any("unbekannten Absender" in w for w in warnings)


def _with_reply_to(reply_to: str, sender: str, body: str) -> MailSignals:
    raw = f"Reply-To: {reply_to}\n".encode() + make_mail(sender=sender, subject="Bitte", body=body)
    return detect(parse_message(raw), SENDERS)


def test_faked_own_sender_with_foreign_reply_to_is_not_trusted() -> None:
    signals = _with_reply_to(
        "gf.musterpflege@mail.example",
        "Geschäftsführung <gf@musterpflege.example>",
        f"Bitte heute noch diskret 24.850,00 EUR überweisen: {OTHER_IBAN}. Dringend!",
    )
    assert signals.impersonation_risk
    assert not signals.known
    assert not signals.internal
    assert any("typische Betrugsmasche" in w for w in security_warnings(signals))


def test_contact_form_with_customer_reply_to_gets_no_fraud_warning() -> None:
    signals = _with_reply_to(
        "angehoerige@mail.example",
        "Kontaktformular <web@musterpflege.example>",
        "Pflegeanfrage für meine Mutter. Wie läuft die Abrechnung mit der Pflegekasse?",
    )
    assert signals.reply_elsewhere == "mail.example"
    assert not signals.known  # the real sender is the customer, not a colleague
    assert security_warnings(signals) == ()


def test_reply_to_within_the_same_sender_is_fine() -> None:
    signals = _with_reply_to(
        "buchhaltung@musterpflege.example", "pdl@musterpflege.example", "Teamsitzung Montag."
    )
    assert signals.reply_elsewhere is None
    assert signals.known


@pytest.mark.parametrize(
    "body",
    [
        "Die Abrechnung läuft über die Pflegekasse.",  # "rechnung" inside "Abrechnung"
        "Streitwert 28.825,00 EUR laut Klageerweiterung.",  # an amount alone
    ],
)
def test_no_payment_request_without_payment_words(body: str) -> None:
    assert not _signals("x@unbekannt.example", body).payment_request


@pytest.mark.parametrize(
    "text",
    [
        "Hosting Plus, 1 Monat(e) im Voraus (vom 03.11.2026 bis 02.12.2026)",
        "Urlaub vom 16.11. bis einschließlich 29.11.",
        "Leistungszeitraum 01.10.2026 – bis 31.10.2026",
        "Der Betrag ist zahlbar bis 09.11.2026.",
    ],
)
def test_periods_and_payment_terms_are_no_deadlines(text: str) -> None:
    assert find_deadlines(text, None) == ()


def test_deadline_after_a_date_in_another_sentence_still_counts() -> None:
    [deadline] = find_deadlines("Ihr Schreiben vom 03.11.2026 – bitte bis 20.11.2026 Stellung nehmen.", None)
    assert deadline.due == date(2026, 11, 20)


def test_leap_day_without_year_late_in_a_leap_year_is_ignored_not_a_crash() -> None:
    # 29.02. lies far behind 01.12.2028, so it would mean next year – but 29.02.2029 does not exist
    received = datetime(2028, 12, 1, 9, 0, tzinfo=UTC)
    assert find_deadlines("Bitte melden Sie sich bis zum 29.02. zurück.", received) == ()
    [deadline] = find_deadlines("Bitte melden Sie sich bis zum 28.02. zurück.", received)
    assert deadline.due == date(2029, 2, 28)


def test_confidentiality_footer_is_no_pressure() -> None:
    signals = _signals(
        "ra@kanzlei.example",
        "Anbei die Unterlagen.\n\nDiese E-Mail enthält vertrauliche Informationen und ist nur für den "
        "Empfänger bestimmt.",
    )
    assert signals.pressure == ()
    assert _signals("x@unbekannt.example", "Bitte diskret und vertraulich behandeln.").pressure


def test_unknown_sender_ordinary_invoice_is_not_a_warning() -> None:
    signals = _signals("info@neu.example", "Anbei unsere Rechnung 5 über 120,00 EUR, zahlbar in 14 Tagen.")
    assert signals.payment_request
    assert not signals.pressure
    assert security_warnings(signals) == ()


def test_login_link_from_unknown_sender() -> None:
    signals = _signals("service@bank-sicher.example", "Bitte bestätigen: https://bank-sicher.example/login")
    assert signals.login_links
    assert any("Anmeldeseite" in w for w in security_warnings(signals))
    known = _signals("portal@aok.example", "Bescheid im Portal: https://portal.aok.example/login")
    assert security_warnings(known) == ()


def test_forward_detection() -> None:
    body = "Zur Info.\n\n---------- Forwarded message ---------\nVon: X\n\nText"
    internal = _signals("pdl@musterpflege.example", body, subject="WG: Rechnung")
    assert internal.forwarded
    assert internal.pure_forward
    assert internal.own_text == "Zur Info."
    external = _signals("anna@example.org", "Hallo,\n\n-----Ursprüngliche Nachricht-----\nVon: Y\n\nAlt")
    assert external.pure_forward  # no own words
    plain = _signals("anna@example.org", "Hallo,\nkönnen Sie helfen?")
    assert not plain.forwarded


def test_deadlines() -> None:
    received = parse_message(make_mail(date="Wed, 28 Oct 2026 10:00:00 +0100")).received_at
    found = find_deadlines("Bitte senden Sie uns die Unterlagen bis zum 15.11.2026 zu.", received)
    assert [d.due for d in found] == [date(2026, 11, 15)]
    assert "Unterlagen" in found[0].description
    month = find_deadlines("Widerspruch ist innerhalb eines Monats nach Bekanntgabe möglich.", received)
    assert month[0].due == date(2026, 11, 28)
    assert "Widerspruchsfrist" in month[0].description
    assert find_deadlines("Spätestens am 03.01. brauchen wir den Bogen.", received)[0].due == date(2027, 1, 3)
    assert find_deadlines("Termin am 06.11.2026 um 10 Uhr.", received) == ()


def test_payment_details() -> None:
    payment = find_payment(
        f"Rechnung Nr. 2026-1187 über 1.412,80 EUR, fällig am 20.11.2026. {SUPPLIER_IBAN}", None
    )
    assert payment is not None
    assert (payment.amount, payment.invoice_number, payment.due) == (
        "1.412,80 EUR",
        "2026-1187",
        date(2026, 11, 20),
    )
    assert payment.iban == "DE89370400440532013000"
    assert find_payment("Termin am Montag", None) is None


@pytest.fixture
def rules() -> RuleBasedAnalyzer:
    return RuleBasedAnalyzer(OrganizationSettings(), SENDERS)


def test_rules_known_newsletter_is_clear_advertising(rules: RuleBasedAnalyzer) -> None:
    mail = make_mail(sender="info@news.example", subject="Wir tragen Ihrer Rechnung Rechnung", body="Termin!")
    analysis = rules.analyze(parse_message(mail))
    assert (analysis.category, analysis.urgency, analysis.reply_needed) == (
        Category.ADVERTISING,
        Urgency.LOW,
        False,
    )
    assert analysis.confidence == 0.95


def test_rules_internal_and_forward(rules: RuleBasedAnalyzer) -> None:
    internal = rules.analyze(
        parse_message(make_mail(sender="pdl@musterpflege.example", body="Kannst du Dienst tauschen?"))
    )
    assert internal.category is Category.INTERNAL
    assert internal.reply_needed
    forward = rules.analyze(
        parse_message(
            make_mail(
                sender="pdl@musterpflege.example",
                subject="WG: Rechnung 7",
                body="---------- Forwarded message ---------\nVon: L\n\nRechnung 7 über 10,00 EUR",
            )
        )
    )
    assert forward.category is Category.BILLING
    assert forward.responsible is Responsible.BILLING
    assert not forward.reply_needed


def test_rules_supplier_invoice_needs_no_reply(rules: RuleBasedAnalyzer) -> None:
    mail = make_mail(
        sender="r@lindner.example", subject="Rechnung 9", body=f"Betrag 50,00 EUR auf {SUPPLIER_IBAN}"
    )
    analysis = rules.analyze(parse_message(mail))
    assert analysis.category is Category.BILLING
    assert not analysis.reply_needed
    assert analysis.warnings == ()
    assert analysis.payment is not None
    assert analysis.payment.iban == "DE89370400440532013000"


def test_rules_bank_change_is_suspicious_and_urgent(rules: RuleBasedAnalyzer) -> None:
    mail = make_mail(
        sender="r@lindner.example", subject="Rechnung 9", body=f"Neue Bankverbindung: {OTHER_IBAN}"
    )
    analysis = rules.analyze(parse_message(mail))
    assert (analysis.category, analysis.urgency) == (Category.SUSPICIOUS, Urgency.HIGH)
    assert analysis.responsible is Responsible.MANAGEMENT


def test_rules_formal_letter_without_question_gets_no_draft(rules: RuleBasedAnalyzer) -> None:
    mail = make_mail(
        sender="leistung@aok.example", subject="Kostenzusage", body="Die Kostenzusage liegt bei."
    )
    analysis = rules.analyze(parse_message(mail))
    assert analysis.category is Category.INSURANCE
    assert not analysis.reply_needed
    asking = make_mail(
        sender="leistung@aok.example", subject="Rückfrage", body="Bitte um Rückmeldung bis Freitag."
    )
    assert rules.analyze(parse_message(asking)).reply_needed


def test_rules_deadline_within_a_week_is_urgent(rules: RuleBasedAnalyzer) -> None:
    mail = make_mail(
        sender="leistung@aok.example",
        body="Bitte reichen Sie die Nachweise bis zum 02.11.2026 ein.",
    )
    analysis = rules.analyze(parse_message(mail))
    assert analysis.urgency is Urgency.HIGH
    assert analysis.deadlines[0].due == date(2026, 11, 2)
