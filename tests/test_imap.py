"""Read-only IMAP source with a fake client; the guard is tested without any socket."""

import imaplib
import ssl
from datetime import date
from typing import Any
from unittest import mock

import pytest

from mail_agent.config import ImapSettings
from mail_agent.security.credentials import Credential, CredentialError
from mail_agent.security.secret import Secret
from mail_agent.security.tls import InsecureTlsContextError
from mail_agent.sources import FetchCursor, FetchPlan, RawMail, SourceError
from mail_agent.sources.imap import (
    FETCH_ITEMS,
    ForbiddenImapCommandError,
    ImapSource,
    ReadOnlyIMAP4_SSL,
    check_command,
    imap_date,
)
from tests.helpers import make_mail

SETTINGS = ImapSettings(allowed_username="test@example.org", since_days=7)


class FakeCredentials:
    def __init__(
        self, username: str = "test@example.org", password: str = "geheim", error: str | None = None
    ):
        self.username, self.password, self.error = username, password, error

    def get_credential(self, target: str) -> Credential:
        if self.error:
            raise CredentialError(self.error)
        return Credential(self.username, Secret(self.password))


class FakeImap:
    """Fake server. ``UID SEARCH UID n:*`` behaves like RFC 3501: it always contains the highest UID."""

    def __init__(
        self, messages: dict[bytes, bytes], login_ok: bool = True, validity: bytes | None = b"77"
    ) -> None:
        self.messages = messages
        self.login_ok = login_ok
        self.validity = validity
        self.log: list[tuple[Any, ...]] = []

    def login(self, user: str, password: str) -> tuple[str, list[Any]]:
        self.log.append(("LOGIN", user))
        if not self.login_ok:
            raise imaplib.IMAP4.error("auth failed")
        return "OK", [b"logged in"]

    def select(self, mailbox: str = "INBOX", readonly: bool = False) -> tuple[str, list[Any]]:
        self.log.append(("SELECT", mailbox, readonly))
        return "OK", [str(len(self.messages)).encode()]

    def response(self, code: str) -> tuple[str, list[Any]]:
        assert code == "UIDVALIDITY"
        return code, [self.validity]

    def uid(self, command: str, *args: Any) -> tuple[str, list[Any]]:
        check_command("UID", (command, *args))  # the fake enforces the same allowlist
        self.log.append(("UID", command, *args))
        if command == "SEARCH":
            uids = sorted(int(uid) for uid in self.messages)
            if args[0] == "UID":
                first = int(args[1].split(":")[0])
                uids = [uid for uid in uids if uid >= first] or uids[-1:]
            return "OK", [b" ".join(str(uid).encode() for uid in uids)]
        if args[0].encode() not in self.messages:
            return "OK", [None]  # deleted in the meantime
        return "OK", [
            (b"1 (UID " + args[0].encode() + b" BODY[] {10}", self.messages[args[0].encode()]),
            b")",
        ]

    def logout(self) -> tuple[str, list[Any]]:
        self.log.append(("LOGOUT",))
        return "BYE", [b""]


def _source(fake: FakeImap, credentials: FakeCredentials | None = None) -> ImapSource:
    return ImapSource(
        SETTINGS,
        credentials or FakeCredentials(),
        client_factory=lambda _s: fake,
        today=lambda: date(2026, 10, 29),
    )


def _messages(*uids: int) -> dict[bytes, bytes]:
    return {str(uid).encode(): make_mail(message_id=f"<{uid}@x>") for uid in uids}


def test_plan_finds_everything_since_the_start_date_read_only() -> None:
    fake = FakeImap(_messages(1, 2, 3))
    plan = _source(fake).plan(None, date(2026, 10, 3))
    assert plan == FetchPlan(("1", "2", "3"), "77", date(2026, 10, 3))  # no upper limit
    assert ("SELECT", "INBOX", True) in fake.log  # EXAMINE
    assert ("UID", "SEARCH", "SINCE", "03-Oct-2026") in fake.log
    assert fake.log[-1] == ("LOGOUT",)


def test_plan_without_start_date_uses_since_days() -> None:
    fake = FakeImap(_messages(1))
    plan = _source(fake).plan(None, None)
    assert plan.start == date(2026, 10, 22)
    assert ("UID", "SEARCH", "SINCE", "22-Oct-2026") in fake.log


def test_plan_continues_after_the_stored_position() -> None:
    fake = FakeImap(_messages(4, 5, 9))
    plan = _source(fake).plan(FetchCursor("77", 5), date(2026, 10, 3))
    assert plan.items == ("9",)
    assert ("UID", "SEARCH", "UID", "6:*", "SINCE", "03-Oct-2026") in fake.log


def test_plan_ignores_the_highest_uid_rfc_quirk() -> None:
    # nothing new: "UID 10:*" still answers with the highest existing UID 9
    plan = _source(FakeImap(_messages(4, 9))).plan(FetchCursor("77", 9), date(2026, 10, 3))
    assert plan.items == ()


def test_changed_uidvalidity_drops_the_position() -> None:
    fake = FakeImap(_messages(1, 2), validity=b"78")
    plan = _source(fake).plan(FetchCursor("77", 5), date(2026, 10, 3))
    assert plan.items == ("1", "2")  # start date decides, stored mails are skipped by Message-ID
    assert plan.validity == "78"
    assert ("UID", "SEARCH", "SINCE", "03-Oct-2026") in fake.log


def test_load_reads_a_portion_on_its_own_connection() -> None:
    fake = FakeImap(_messages(1, 2, 3))
    source = _source(fake)
    mails = list(source.load(FetchPlan(("1", "2", "3"), "77"), ["2", "3", "8"]))  # 8 deleted meanwhile
    assert mails == [
        RawMail(fake.messages[b"2"], "imap:INBOX/2", "2"),
        RawMail(fake.messages[b"3"], "imap:INBOX/3", "3"),
    ]
    assert ("UID", "FETCH", "2", FETCH_ITEMS) in fake.log
    assert fake.log[0] == ("LOGIN", "test@example.org")
    assert fake.log[-1] == ("LOGOUT",)


def test_load_refuses_a_rebuilt_mailbox() -> None:
    fake = FakeImap(_messages(1), validity=b"78")
    with pytest.raises(SourceError) as exc:
        list(_source(fake).load(FetchPlan(("1",), "77"), ["1"]))
    assert exc.value.code == "imap_mailbox_changed"
    assert fake.log[-1] == ("LOGOUT",)


def test_missing_uidvalidity_means_no_position() -> None:
    plan = _source(FakeImap(_messages(1), validity=None)).plan(FetchCursor("77", 5), date(2026, 10, 3))
    assert plan.validity is None
    assert plan.items == ("1",)


def test_identity_names_server_user_and_mailbox() -> None:
    assert _source(FakeImap({})).identity == "imap:imap.example.org/test@example.org/INBOX"


def test_wrong_username_is_refused_before_connecting() -> None:
    factory = mock.Mock()
    source = ImapSource(
        SETTINGS, FakeCredentials(username="chef@musterpflege.example"), client_factory=factory
    )
    with pytest.raises(SourceError) as exc:
        source.plan(None, None)
    assert exc.value.code == "username_not_allowed"
    factory.assert_not_called()


def test_unsupported_password_is_refused_before_connecting() -> None:
    factory = mock.Mock()
    source = ImapSource(SETTINGS, FakeCredentials(password="pässwort"), client_factory=factory)
    with pytest.raises(SourceError) as exc:
        source.plan(None, None)
    assert exc.value.code == "password_unsupported_characters"
    factory.assert_not_called()


def test_missing_credential_is_reported() -> None:
    with pytest.raises(SourceError) as exc:
        _source(FakeImap({}), FakeCredentials(error="credential_not_found")).plan(None, None)
    assert exc.value.code == "credential_not_found"


def test_login_failure_still_logs_out() -> None:
    fake = FakeImap({}, login_ok=False)
    with pytest.raises(SourceError) as exc:
        _source(fake).plan(None, None)
    assert exc.value.code == "imap_login_failed"
    assert fake.log[-1] == ("LOGOUT",)


def test_certificate_error_is_reported() -> None:
    def factory(_settings: ImapSettings) -> Any:
        raise ssl.SSLCertVerificationError("bad cert")

    source = ImapSource(SETTINGS, FakeCredentials(), client_factory=factory)
    with pytest.raises(SourceError) as exc:
        source.plan(None, None)
    assert exc.value.code == "tls_certificate_invalid"


@pytest.mark.parametrize(
    ("name", "args"),
    [
        ("CAPABILITY", ()),
        ("LOGIN", ("u", "p")),
        ("EXAMINE", ("INBOX",)),
        ("UID", ("SEARCH", "SINCE", "01-Oct-2026")),
        ("UID", ("SEARCH", "UID", "6:*", "SINCE", "01-Oct-2026")),
        ("UID", ("FETCH", "5", "(BODY.PEEK[])")),
        ("LOGOUT", ()),
    ],
)
def test_allowlisted_commands_pass(name: str, args: tuple[Any, ...]) -> None:
    check_command(name, args)


@pytest.mark.parametrize(
    ("name", "args"),
    [
        ("SELECT", ("INBOX",)),  # read-write open
        ("STORE", ("1", "+FLAGS", "(\\Deleted)")),
        ("EXPUNGE", ()),
        ("APPEND", ("INBOX", None, None, b"x")),
        ("COPY", ("1", "Archiv")),
        ("DELETE", ("INBOX",)),
        ("UID", ("STORE", "1", "+FLAGS", "(\\Seen)")),
        ("UID", ("MOVE", "1", "Trash")),
        ("UID", ("FETCH", "1", "(BODY[])")),  # would set \Seen
        ("UID", ("FETCH", "1", "(RFC822)")),
        ("UID", ()),
    ],
)
def test_writing_commands_are_blocked(name: str, args: tuple[Any, ...]) -> None:
    with pytest.raises(ForbiddenImapCommandError):
        check_command(name, args)


def test_guarded_client_blocks_before_reaching_imaplib() -> None:
    client = ReadOnlyIMAP4_SSL.__new__(ReadOnlyIMAP4_SSL)  # no connection
    with mock.patch.object(imaplib.IMAP4, "_command") as underlying:
        with pytest.raises(ForbiddenImapCommandError):
            client._command("STORE", "1", "+FLAGS", "(\\Seen)")
        underlying.assert_not_called()
        client._command("NOOP")
        underlying.assert_called_once_with("NOOP")


def test_guarded_client_rejects_unverified_tls() -> None:
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    with pytest.raises(InsecureTlsContextError):
        ReadOnlyIMAP4_SSL("imap.example.org", 993, ssl_context=context, timeout=5)


def test_imap_date_is_locale_independent() -> None:
    assert imap_date(date(2026, 3, 5)) == "05-Mar-2026"
