"""Contact form notifications of the own website: the visitor is the sender, never a trusted one."""

from dataclasses import replace

from fastapi.testclient import TestClient

from mail_agent.analysis.signals import detect
from mail_agent.config import Settings
from mail_agent.contact_form import parse_contact_form
from mail_agent.jobs import JobRunner
from mail_agent.parsing import parse_message
from mail_agent.senders import KnownSender, SenderDirectory
from mail_agent.service import MailAgentService
from mail_agent.web.app import create_app
from tests.helpers import BASE_URL, make_mail


def form_mail(
    *,
    email: str = "max.muster@example.org",
    message: str = "Wir suchen einen Pflegedienst für meinen Vater.<br>Hinweis: er wird beatmet.",
    message_id: str = "<form-1@musterpflege.example>",
) -> bytes:
    """Rebuilt like the real Elementor notification (HTML, from info@ to info@, no Reply-To)."""
    html = (
        "<p>Vorname: Max<br>Nachname: Muster<br>"
        f"E-Mail-Adresse: {email}<br>Telefon: 0170 1234567<br>"
        f"Ihre Nachricht: {message}<br>on</p><p>---</p>"
        "<p>Datum: Oktober 1, 2026<br>Zeit: 11:46 a.m.<br>Seiten URL: https://musterpflege.example/kontakt/<br>"
        "Benutzer Agent: Mozilla/5.0 (Windows NT 10.0)<br>Remote IP: 203.0.113.7<br>Unterstützt von: Elementor</p>"
    )
    return make_mail(
        sender="Musterpflege <info@musterpflege.example>",
        subject="Neue Nachricht von Musterpflege",
        body=html,
        html=True,
        message_id=message_id,
    )


def test_visitor_becomes_the_sender() -> None:
    message = parse_message(form_mail())
    assert message.contact_form
    assert (message.sender_name, message.sender_address, message.reply_to) == (
        "Max Muster",
        "max.muster@example.org",
        "max.muster@example.org",
    )
    assert message.body.startswith(
        "Nachricht über das Kontaktformular der Website (Seite: https://musterpflege"
    )
    assert "Telefon: 0170 1234567" in message.body
    # a "Label:" line inside the message stays part of it; the consent checkbox value is dropped
    assert message.body.endswith("Wir suchen einen Pflegedienst für meinen Vater.\nHinweis: er wird beatmet.")


def test_technical_data_is_not_kept() -> None:
    body = parse_message(form_mail()).body
    for removed in ("203.0.113.7", "Mozilla", "Elementor", "Remote IP", "\non"):
        assert removed not in body


def test_invalid_address_keeps_the_form_sender() -> None:
    message = parse_message(form_mail(email="kein-gueltiger-eintrag"))
    assert message.contact_form
    assert message.sender_address == "info@musterpflege.example"
    assert "E-Mail: (keine gültige Adresse angegeben)" in message.body


def test_ordinary_mail_with_separator_is_unchanged() -> None:
    raw = make_mail(body="Hallo,\nE-Mail: a@b.de\n---\nDatum: heute\nGruß")
    message = parse_message(raw)
    assert not message.contact_form
    assert message.sender_address == "anna@example.org"
    assert parse_contact_form("E-Mail-Adresse: a@b.de\nkein Trenner") is None


def test_form_address_is_never_internal_or_known() -> None:
    senders = SenderDirectory(
        senders=(KnownSender(name="Lieferant", type="lieferant", domains=("lieferant.example",)),),
        own_domains=("musterpflege.example",),
    )
    pretends_internal = parse_message(form_mail(email="chef@musterpflege.example"))
    signals = detect(pretends_internal, senders)
    assert not signals.internal
    assert signals.sender is None
    assert signals.reply_elsewhere is None
    pretends_known = parse_message(form_mail(email="rechnung@lieferant.example"))
    assert detect(pretends_known, senders).sender is None


def test_stored_and_shown_as_contact_form(service: MailAgentService, settings: Settings) -> None:
    settings.eml_dir.mkdir(parents=True, exist_ok=True)
    (settings.eml_dir / "form.eml").write_bytes(form_mail())
    service.import_new()
    [summary] = service.repository.inbox.messages()
    detail = service.repository.messages.get(summary.id)
    assert detail is not None
    assert detail.contact_form
    assert detail.sender_address == "max.muster@example.org"

    client = TestClient(create_app(service, JobRunner(service, background=False)), base_url=BASE_URL)
    page = client.get(f"/nachrichten/{summary.id}").text
    assert "Über das Kontaktformular der Website" in page
    assert "Absender als bekannt hinterlegen" not in page
    response = client.post(
        f"/nachrichten/{summary.id}/absender",
        data={"name": "Max", "typ": "sonstiges"},
        follow_redirects=False,
    )
    assert response.status_code == 400


def test_refresh_updates_an_old_row(service: MailAgentService, settings: Settings) -> None:
    """Mails imported before the form was recognised get the visitor as sender on re-analysis."""
    settings.eml_dir.mkdir(parents=True, exist_ok=True)
    (settings.eml_dir / "form.eml").write_bytes(form_mail())
    service.import_new()
    [summary] = service.repository.inbox.messages()
    stale = replace(
        parse_message(form_mail()),
        sender_name="Musterpflege",
        sender_address="info@musterpflege.example",
        reply_to="info@musterpflege.example",
        body="alt",
        contact_form=False,
    )
    service.repository.messages.refresh(summary.id, stale)  # as stored before the form was recognised
    service.reanalyze(summary.id)
    detail = service.repository.messages.get(summary.id)
    assert detail is not None
    assert detail.contact_form
    assert detail.sender_address == "max.muster@example.org"
