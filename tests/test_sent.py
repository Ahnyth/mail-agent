"""Replies in the sent folder mark the answered mail as "beantwortet" and "erledigt" – read-only."""

from datetime import date, datetime
from email.message import EmailMessage
from pathlib import Path
from typing import Any

import pytest

from mail_agent.analysis.rules import RuleBasedAnalyzer
from mail_agent.config import FetchSettings, OrganizationSettings
from mail_agent.filing import DocumentStore
from mail_agent.models import Status
from mail_agent.sent import StoredMail, answered_ids, normalized_subject, read_sent
from mail_agent.service import SENT_MAILBOX_STATE, MailAgentService, ServiceOptions
from mail_agent.sources.imap import ImapSource, check_command
from mail_agent.storage import MailRepository
from tests.helpers import make_mail
from tests.test_imap import SETTINGS, FakeCredentials


def sent_mail(
    *,
    to: str = "anna@example.org",
    subject: str = "AW: Testbetreff",
    in_reply_to: str | None = "<test-1@example.org>",
    date_header: str = "Thu, 29 Oct 2026 09:00:00 +0100",
) -> bytes:
    message = EmailMessage()
    message["From"] = "info@musterpflege.example"
    message["To"] = to
    message["Subject"] = subject
    message["Date"] = date_header
    if in_reply_to:
        message["In-Reply-To"] = in_reply_to
        message["References"] = f"<earlier@example.org> {in_reply_to}"
    message.set_content("Danke, wir melden uns.")
    return bytes(message)


class FakeServer:
    """Several mailboxes; unknown names answer NO like a real server. Same allowlist as production."""

    def __init__(self, mailboxes: dict[str, dict[bytes, bytes]]) -> None:
        self.mailboxes = mailboxes
        self.current = ""
        self.log: list[tuple[Any, ...]] = []

    def login(self, user: str, password: str) -> tuple[str, list[Any]]:
        return "OK", [b""]

    def select(self, mailbox: str = "INBOX", readonly: bool = False) -> tuple[str, list[Any]]:
        assert readonly, "every mailbox is opened with EXAMINE"
        name = mailbox.strip('"')
        self.log.append(("EXAMINE", mailbox))
        if name not in self.mailboxes:
            return "NO", [b"Mailbox does not exist"]
        self.current = name
        return "OK", [str(len(self.mailboxes[name])).encode()]

    def response(self, code: str) -> tuple[str, list[Any]]:
        return code, [b"7" if self.current == "INBOX" else b"9"]

    def uid(self, command: str, *args: Any) -> tuple[str, list[Any]]:
        check_command("UID", (command, *args))
        self.log.append(("UID", self.current, command, *args))
        messages = self.mailboxes[self.current]
        if command == "SEARCH":
            uids = sorted(int(uid) for uid in messages)
            if args[0] == "UID":
                first = int(args[1].split(":")[0])
                uids = [uid for uid in uids if uid >= first] or uids[-1:]
            return "OK", [b" ".join(str(uid).encode() for uid in uids)]
        return "OK", [(b"1 (UID " + args[0].encode() + b" BODY[] {10}", messages[args[0].encode()]), b")"]

    def logout(self) -> tuple[str, list[Any]]:
        return "BYE", [b""]


def _service(tmp_path: Path, server: FakeServer) -> MailAgentService:
    source = ImapSource(
        SETTINGS, FakeCredentials(), client_factory=lambda _s: server, today=lambda: date(2026, 10, 30)
    )
    return MailAgentService(
        repository=MailRepository(tmp_path / "db.sqlite3"),
        analyzer=RuleBasedAnalyzer(OrganizationSettings()),
        documents=DocumentStore(tmp_path / "ablage"),
        source=source,
        options=ServiceOptions(fetch=FetchSettings(enabled=True, start_date=date(2026, 10, 1))),
    )


def test_read_sent_takes_only_the_header() -> None:
    sent = read_sent(sent_mail())
    assert sent is not None
    assert sent.references == ("<test-1@example.org>", "<earlier@example.org>")
    assert sent.recipients == ("anna@example.org",)
    assert sent.subject == "AW: Testbetreff"
    assert sent.sent_at is not None
    assert sent.sent_at.day == 29


def test_normalized_subject() -> None:
    assert normalized_subject("AW: Re:  WG: Termin am  Montag") == "termin am montag"
    assert normalized_subject("Antw: Fwd: Rechnung") == "rechnung"


def _stored(**overrides: Any) -> StoredMail:
    values: dict[str, Any] = {
        "id": 1,
        "message_key": "x",
        "sender_address": "anna@example.org",
        "reply_to": "anna@example.org",
        "subject": "Testbetreff",
        "received_at": datetime.fromisoformat("2026-10-28T10:00:00+01:00"),
    }
    values.update(overrides)
    return StoredMail(**values)


def test_match_by_recipient_and_subject_without_reference() -> None:
    sent = read_sent(sent_mail(in_reply_to=None))
    assert sent is not None
    assert answered_ids(sent, [_stored()]) == [1]
    assert answered_ids(sent, [_stored(subject="Etwas anderes")]) == []
    assert answered_ids(sent, [_stored(sender_address="b@x.de", reply_to="b@x.de")]) == []
    later = datetime.fromisoformat("2026-10-30T10:00:00+01:00")
    assert answered_ids(sent, [_stored(received_at=later)]) == []  # sent before the mail arrived


def test_fetch_marks_replied_mail_answered_and_done(tmp_path: Path) -> None:
    server = FakeServer(
        {
            "INBOX": {
                b"1": make_mail(),
                b"2": make_mail(message_id="<test-2@example.org>", subject="Andere"),
            },
            "Gesendete Objekte": {b"5": sent_mail()},
        }
    )
    service = _service(tmp_path, server)
    report = service.run("abruf", "manuell")
    assert report.answered == 1
    assert "1 als beantwortet erkannt" in report.text
    by_subject = {m.subject: m for m in service.repository.inbox.messages()}
    assert by_subject["Testbetreff"].status is Status.DONE
    assert by_subject["Testbetreff"].answered_at is not None
    assert by_subject["Andere"].status is Status.OPEN
    # folders are found by trying usual names, always read-only and with the quoted name
    assert ("EXAMINE", '"Gesendete Objekte"') in server.log
    assert service.repository.state.get(SENT_MAILBOX_STATE) == "Gesendete Objekte"


def test_second_fetch_reads_only_new_sent_mails(tmp_path: Path) -> None:
    server = FakeServer({"INBOX": {b"1": make_mail()}, "Sent": {b"5": sent_mail()}})
    service = _service(tmp_path, server)
    service.run("abruf", "manuell")
    server.log.clear()
    report = service.run("abruf", "manuell")
    assert report.answered == 0
    sent_fetches = [entry for entry in server.log if entry[:3] == ("UID", "Sent", "FETCH")]
    assert sent_fetches == []
    searches = [entry for entry in server.log if entry[:3] == ("UID", "Sent", "SEARCH")]
    assert searches[0][3:5] == ("UID", "6:*")


def test_without_sent_folder_the_fetch_still_works(tmp_path: Path) -> None:
    server = FakeServer({"INBOX": {b"1": make_mail()}})
    service = _service(tmp_path, server)
    report = service.run("abruf", "manuell")
    assert (report.imported, report.answered) == (1, 0)
    assert service.repository.state.get(SENT_MAILBOX_STATE) is None


@pytest.mark.parametrize("command", ["STORE", "COPY", "MOVE", "EXPUNGE", "APPEND", "SELECT", "LIST"])
def test_allowlist_stays_read_only(command: str) -> None:
    from mail_agent.sources.imap import ForbiddenImapCommandError

    with pytest.raises(ForbiddenImapCommandError):
        check_command(command, ())


def test_inbox_shows_the_sent_folder(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from mail_agent.jobs import JobRunner
    from mail_agent.web.app import create_app
    from tests.helpers import BASE_URL

    server = FakeServer({"INBOX": {b"1": make_mail()}, "Sent": {b"5": sent_mail()}})
    service = _service(tmp_path, server)
    client = TestClient(create_app(service, JobRunner(service, background=False)), base_url=BASE_URL)
    client.post("/einlesen")
    page = client.get("/?status=alle").text
    assert "Antworten im Ordner „Sent“ werden erkannt" in page
    assert "beantwortet" in page
