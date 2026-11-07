"""Test helpers."""

from email.message import EmailMessage

BASE_URL = "http://localhost"
"""Test client address: the app only accepts localhost/127.0.0.1 as host (no "testserver")."""


def make_mail(
    *,
    sender: str = "Anna Beispiel <anna@example.org>",
    subject: str = "Testbetreff",
    body: str = "Hallo,\n\nein Test.\n",
    date: str = "Wed, 28 Oct 2026 10:00:00 +0100",
    message_id: str | None = "<test-1@example.org>",
    attachments: tuple[tuple[str, str, bytes], ...] = (),
    html: bool = False,
) -> bytes:
    message = EmailMessage()
    message["From"] = sender
    message["To"] = "info@musterpflege.example"
    message["Subject"] = subject
    message["Date"] = date
    if message_id:
        message["Message-ID"] = message_id
    message.set_content(body, subtype="html" if html else "plain")
    for filename, content_type, payload in attachments:
        maintype, subtype = content_type.split("/", 1)
        message.add_attachment(payload, maintype=maintype, subtype=subtype, filename=filename)
    return bytes(message)
