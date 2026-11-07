from email.message import EmailMessage

import pytest

from mail_agent.attachments import read_text
from mail_agent.parsing import (
    MAX_MESSAGE_BYTES,
    MailParseError,
    html_to_text,
    message_key,
    parse_message,
    reply_subject,
    single_line,
)
from tests.helpers import make_mail


def test_parses_plain_message_with_attachment() -> None:
    raw = make_mail(attachments=(("Verordnung.pdf", "application/pdf", b"%PDF-1.4 test"),))
    message = parse_message(raw)
    assert message.sender_name == "Anna Beispiel"
    assert message.sender_address == "anna@example.org"
    assert message.reply_to == "anna@example.org"
    assert message.subject == "Testbetreff"
    assert "ein Test." in message.body
    assert message.received_at is not None
    assert message.received_at.year == 2026
    assert [(a.filename, a.content_type, a.payload) for a in message.attachments] == [
        ("Verordnung.pdf", "application/pdf", b"%PDF-1.4 test")
    ]


def test_signature_logo_embedded_in_html_is_not_an_attachment() -> None:
    # Outlook without a real attachment: multipart/related with the logo next to the HTML body
    message = EmailMessage()
    message["From"] = "Praxis <praxis@example.org>"
    message["Subject"] = "Rückruf"
    message.set_content('<p>Bitte rufen Sie zurück.</p><img src="cid:image001.png@01DB">', subtype="html")
    message.add_related(b"\x89PNG logo", maintype="image", subtype="png", cid="<image001.png@01DB>")
    assert parse_message(bytes(message)).attachments == ()

    message.add_attachment(b"%PDF-1.4", maintype="application", subtype="pdf", filename="Verordnung.pdf")
    message.add_attachment(b"\x89PNG scan", maintype="image", subtype="png", filename="scan.png")
    parsed = parse_message(bytes(message))
    assert [a.filename for a in parsed.attachments] == ["Verordnung.pdf", "scan.png"]


def test_mail_forwarded_as_attachment_is_kept_with_its_text() -> None:
    inner = EmailMessage()
    inner["From"] = "Pflegekasse <bescheid@kasse.example>"
    inner["Subject"] = "Bescheid Pflegegrad"
    inner.set_content("Widerspruch innerhalb eines Monats möglich.")
    outer = EmailMessage()
    outer["From"] = "PDL <pdl@musterpflege.example>"
    outer["Subject"] = "WG: Bescheid"
    outer.set_content("Zur Info, siehe Anlage.")
    outer.add_attachment(inner)
    [attachment] = parse_message(bytes(outer)).attachments
    assert attachment.content_type == "message/rfc822"
    assert attachment.filename.endswith(".eml")
    result = read_text(attachment, None)
    assert result.text is not None
    assert "Bescheid Pflegegrad" in result.text
    assert "Widerspruch innerhalb eines Monats" in result.text


def test_html_body_is_converted_and_scripts_removed() -> None:
    raw = make_mail(body="<p>Erste Zeile<br>Zweite &amp; Zeile</p><script>alert(1)</script>", html=True)
    body = parse_message(raw).body
    assert "Erste Zeile\nZweite & Zeile" in body
    assert "alert" not in body
    assert "<" not in body


def test_header_line_breaks_are_flattened() -> None:
    assert single_line("Betreff\r\nBcc: angreifer@example.com") == "Betreff Bcc: angreifer@example.com"


def test_reply_to_header_is_used_for_replies() -> None:
    message = make_mail().replace(b"Subject:", b"Reply-To: buero@example.org\nSubject:", 1)
    assert parse_message(message).reply_to == "buero@example.org"


def test_message_key_prefers_message_id() -> None:
    first = parse_message(make_mail(body="eins"))
    second = parse_message(make_mail(body="zwei"))
    assert first.key == second.key  # same Message-ID -> same message
    without_id = make_mail(message_id=None)
    assert parse_message(without_id).key == message_key(without_id, None)


def test_missing_sender_is_rejected() -> None:
    with pytest.raises(MailParseError) as exc:
        parse_message(make_mail(sender="kein-absender"))
    assert exc.value.code == "sender_missing"


@pytest.mark.parametrize(
    ("raw", "code"),
    [(b"", "message_empty"), (b"x" * (MAX_MESSAGE_BYTES + 1), "message_too_large")],
)
def test_invalid_input_fails_closed(raw: bytes, code: str) -> None:
    with pytest.raises(MailParseError) as exc:
        parse_message(raw)
    assert exc.value.code == code


def test_unnamed_attachment_gets_generated_name() -> None:
    raw = make_mail(attachments=(("", "application/pdf", b"%PDF"),))
    assert parse_message(raw).attachments[0].filename == "anhang-1.pdf"


def test_invalid_date_is_tolerated() -> None:
    assert parse_message(make_mail(date="kein Datum")).received_at is None


@pytest.mark.parametrize(
    ("subject", "expected"),
    [("Termin", "Re: Termin"), ("AW: Termin", "AW: Termin"), ("re: x", "re: x"), ("", "Re: Ihre Nachricht")],
)
def test_reply_subject(subject: str, expected: str) -> None:
    assert reply_subject(subject) == expected


def test_html_to_text_collapses_whitespace() -> None:
    assert html_to_text("<div>  a   b </div>\n\n\n\n<div>c</div>") == "a b\n\nc"
