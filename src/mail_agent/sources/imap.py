r"""Read-only IMAP source for the office mailbox.

Read-only is enforced on three levels:

1. The mailbox is opened with ``EXAMINE`` (RFC 3501: read-only, no flag changes such as ``\Seen``).
2. Messages are fetched with ``BODY.PEEK[]``, which never sets ``\Seen``.
3. :class:`ReadOnlyIMAP4_SSL` rejects every IMAP command outside an explicit allowlist before it
   reaches the socket (no STORE, COPY, MOVE, EXPUNGE, APPEND, DELETE, SELECT, ...).

Before any network access the credential's user name must match ``allowed_username`` from the
configuration, which protects against accidentally using the production mailbox.

New messages are found by UID: everything after the stored position (``UID n:*``) that is not older
than the start date (``SINCE``). UIDs only grow within one ``UIDVALIDITY`` of the mailbox; if the
server changes it, the position is dropped and the start date alone decides (already stored mails
are recognised by their Message-ID).
"""

import imaplib
import ssl
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from datetime import date, timedelta
from typing import Any, Protocol

from ..config import ImapSettings
from ..security.credentials import Credential, CredentialError, CredentialProvider, check_login_charset
from ..security.redaction import get_logger
from ..security.tls import assert_secure_context, build_ssl_context
from . import FetchCursor, FetchPlan, RawMail, SourceError

_log = get_logger("sources.imap")

ALLOWED_COMMANDS = frozenset({"CAPABILITY", "LOGIN", "LOGOUT", "NOOP", "EXAMINE", "UID"})
"""The only IMAP commands that may reach the server; none of them can change the mailbox."""
ALLOWED_UID_COMMANDS = frozenset({"SEARCH", "FETCH"})
"""The only sub-commands of ``UID``: searching and reading, never ``STORE``, ``COPY`` or ``MOVE``."""
FETCH_ITEMS = "(BODY.PEEK[])"
r"""The only items ``UID FETCH`` may ask for: the whole message without setting ``\Seen``."""
UID_FETCH_ARGUMENTS = 3
"""``UID FETCH`` takes exactly three arguments: the word ``FETCH``, one UID and :data:`FETCH_ITEMS`."""
FETCHED_PART_SIZE = 2
"""imaplib returns a fetched message as a pair: the response line and the message bytes."""
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
"""English month abbreviations of IMAP dates (RFC 3501), independent of the process locale."""


class ForbiddenImapCommandError(Exception):
    """A command outside the read-only allowlist was attempted."""


def check_command(name: str, args: tuple[Any, ...]) -> None:
    """Raises unless ``name``/``args`` is part of the read-only allowlist.

    Raises:
        ForbiddenImapCommandError: The command, the ``UID`` sub-command or the fetched items are not allowed.
    """
    command = name.upper()
    if command not in ALLOWED_COMMANDS:
        raise ForbiddenImapCommandError(command)
    if command == "UID":
        _check_uid_command(args)


def _check_uid_command(args: tuple[Any, ...]) -> None:
    """Allows only ``UID SEARCH`` and ``UID FETCH <uid> (BODY.PEEK[])``."""
    sub = str(args[0]).upper() if args else ""
    if sub not in ALLOWED_UID_COMMANDS:
        raise ForbiddenImapCommandError(f"UID {sub}")
    if sub == "FETCH" and (len(args) != UID_FETCH_ARGUMENTS or str(args[2]).upper() != FETCH_ITEMS):
        raise ForbiddenImapCommandError("UID FETCH items")


class ReadOnlyIMAP4_SSL(imaplib.IMAP4_SSL):  # noqa: N801 – mirrors the imaplib class name
    """``IMAP4_SSL`` that refuses unverified TLS and every non-allowlisted command."""

    def __init__(self, host: str, port: int, *, ssl_context: ssl.SSLContext, timeout: float) -> None:
        """Connects to ``host``; a context without certificate and host name check is refused first."""
        assert_secure_context(ssl_context)
        super().__init__(host, port, ssl_context=ssl_context, timeout=timeout)

    def _command(self, name: str, *args: Any) -> Any:
        """Sends a command only after :func:`check_command` allowed it (every imaplib call ends here)."""
        check_command(name, args)
        return super()._command(name, *args)  # type: ignore[misc]


class ImapClient(Protocol):
    """The subset of ``imaplib.IMAP4`` used here (allows a fake in tests)."""

    def login(self, user: str, password: str) -> tuple[str, list[Any]]:
        """Logs in with user name and password."""

    def select(self, mailbox: str = ..., readonly: bool = ...) -> tuple[str, list[Any]]:
        """Opens a mailbox; with ``readonly=True`` imaplib sends ``EXAMINE``."""

    def response(self, code: str) -> tuple[str, list[Any]]:
        """Returns an untagged response the server sent earlier (no command is sent)."""

    def uid(self, command: str, *args: Any) -> tuple[str, list[Any]]:
        """Sends a ``UID`` command such as ``UID SEARCH`` or ``UID FETCH``."""

    def logout(self) -> tuple[str, list[Any]]:
        """Logs out and closes the connection."""


ClientFactory = Callable[[ImapSettings], ImapClient]
"""Opens the connection to the server (replaced by a fake in tests)."""


def _default_client_factory(settings: ImapSettings) -> ImapClient:
    """Opens the guarded TLS connection with the system's trusted certificates."""
    return ReadOnlyIMAP4_SSL(
        settings.host,
        settings.port,
        ssl_context=build_ssl_context(),
        timeout=settings.timeout_seconds,
    )


def imap_date(day: date) -> str:
    """Returns the IMAP date (``01-Oct-2026``) independent of the process locale."""
    return f"{day.day:02d}-{_MONTHS[day.month - 1]}-{day.year}"


def mailbox_argument(name: str) -> str:
    """Returns a mailbox name for ``EXAMINE``, quoted if it has spaces or quotes ("Gesendete Objekte")."""
    if name and all((c.isascii() and c.isalnum()) or c in "._-/" for c in name):
        return name
    escaped = name.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _normalized_user(name: str) -> str:
    """Returns a user name the way it is compared: without surrounding spaces, in lower case."""
    return name.strip().lower()


def _extract_message(data: list[Any]) -> bytes | None:
    """Returns the message bytes from a ``UID FETCH`` answer; ``None`` if there are none."""
    for item in data:
        if isinstance(item, tuple) and len(item) == FETCHED_PART_SIZE and isinstance(item[1], bytes):
            return item[1]
    return None


def _validity(client: ImapClient) -> str | None:
    """Returns the UIDVALIDITY from the untagged EXAMINE response (no extra command is sent)."""
    try:
        _, data = client.response("UIDVALIDITY")
    except (AttributeError, imaplib.IMAP4.error):
        return None
    value = data[0] if data else None
    if isinstance(value, bytes):
        value = value.decode("ascii", errors="replace")
    return value.strip() if isinstance(value, str) and value.strip().isdigit() else None


def _resume_uid(cursor: FetchCursor | None, validity: str | None) -> int | None:
    """Returns the last UID read if the stored position still applies (same UIDVALIDITY), else ``None``."""
    if cursor is None or validity is None or cursor.validity != validity:
        return None
    return cursor.last_uid


def _search_criteria(after_uid: int | None, since: str) -> tuple[str, ...]:
    """Builds the ``UID SEARCH`` criteria: after the stored position if any, never before ``since``."""
    if after_uid is None:
        return ("SINCE", since)
    return ("UID", f"{after_uid + 1}:*", "SINCE", since)


def _found_uids(data: list[Any], after_uid: int | None) -> list[int]:
    """Returns the UIDs of a ``UID SEARCH`` answer in ascending order, only those after ``after_uid``."""
    found = data[0].split() if data and isinstance(data[0], bytes) else []
    uids = sorted({int(uid) for uid in found if uid.isdigit()})
    if after_uid is None:
        return uids
    # "n:*" always matches the highest UID, even if it is below n
    return [uid for uid in uids if uid > after_uid]


def _open_read_only(client: ImapClient, credential: Credential, mailbox: str) -> None:
    """Logs in and opens ``mailbox`` read-only with ``EXAMINE``.

    Raises:
        SourceError: ``imap_login_failed`` or ``imap_mailbox_unavailable``.
    """
    try:
        client.login(credential.username, credential.password.reveal())
    except imaplib.IMAP4.error:
        raise SourceError("imap_login_failed") from None
    status, _ = client.select(mailbox_argument(mailbox), readonly=True)
    if status != "OK":
        raise SourceError("imap_mailbox_unavailable")


def _logout(client: ImapClient) -> None:
    """Logs out; a logout without answer is only noted, the connection ends anyway."""
    try:
        client.logout()
    except (OSError, imaplib.IMAP4.error, ForbiddenImapCommandError):
        _log.warning("IMAP-Abmeldung nicht bestätigt")


class ImapSource:
    """The mailbox on the IMAP server, read-only; every portion uses its own connection."""

    def __init__(
        self,
        settings: ImapSettings,
        credentials: CredentialProvider,
        client_factory: ClientFactory = _default_client_factory,
        today: Callable[[], date] = date.today,
    ) -> None:
        """Reads the mailbox described by ``settings``.

        Args:
            settings: Server, mailbox, allowed user name and limits.
            credentials: Hands out the mailbox password (Windows credential store).
            client_factory: Opens the connection (replaced by a fake in tests).
            today: Returns today's date (replaceable in tests).
        """
        self._settings = settings
        self._credentials = credentials
        self._client_factory = client_factory
        self._today = today

    @property
    def identity(self) -> str:
        """Returns the name under which the position in the configured mailbox is stored."""
        return self.mailbox_identity(self._settings.mailbox)

    def mailbox_identity(self, mailbox: str) -> str:
        """Returns the name under which the position in ``mailbox`` is stored (e.g. the sent folder)."""
        settings = self._settings
        return f"imap:{settings.host}/{_normalized_user(settings.allowed_username)}/{mailbox}"

    def plan(self, cursor: FetchCursor | None, start: date | None) -> FetchPlan:
        """Returns the UIDs of messages after ``cursor`` received on or after ``start``, oldest first."""
        return self.plan_mailbox(self._settings.mailbox, cursor, start)

    def load(self, plan: FetchPlan, items: Sequence[str]) -> Iterator[RawMail]:
        """Reads ``items`` (UIDs from ``plan``) on a fresh connection."""
        yield from self.load_mailbox(self._settings.mailbox, plan, items)

    def find_mailbox(self, candidates: Sequence[str]) -> str | None:
        """Returns the first of ``candidates`` that exists, tried read-only with ``EXAMINE``.

        ``LIST`` is deliberately not in the allowlist, so folders are found by trying usual names.
        """
        with self._session() as client:
            for name in candidates:
                status, _ = client.select(mailbox_argument(name), readonly=True)
                if status == "OK":
                    return name
        return None

    def plan_mailbox(self, mailbox: str, cursor: FetchCursor | None, start: date | None) -> FetchPlan:
        """Plans like :meth:`plan`, for any mailbox (e.g. the sent folder)."""
        if start is None:
            start = self._today() - timedelta(days=self._settings.since_days)
        since = imap_date(start)
        with self._session(mailbox) as client:
            validity = _validity(client)
            after_uid = _resume_uid(cursor, validity)
            status, data = client.uid("SEARCH", *_search_criteria(after_uid, since))
            if status != "OK":
                raise SourceError("imap_search_failed")
        uids = _found_uids(data, after_uid)
        _log.info(
            "IMAP: %d neue Nachrichten seit %s (%s)",
            len(uids),
            since,
            f"nach UID {after_uid}" if after_uid is not None else "ohne gespeicherte Position",
        )
        return FetchPlan(tuple(str(uid) for uid in uids), validity, start)

    def load_mailbox(self, mailbox: str, plan: FetchPlan, items: Sequence[str]) -> Iterator[RawMail]:
        """Reads like :meth:`load`, from any mailbox."""
        with self._session(mailbox) as client:
            if plan.validity is not None and _validity(client) != plan.validity:
                raise SourceError("imap_mailbox_changed")
            for uid in items:
                status, data = client.uid("FETCH", uid, FETCH_ITEMS)
                raw = _extract_message(data) if status == "OK" else None
                if raw is None:
                    _log.warning("IMAP: Nachricht UID %s nicht lesbar", uid)
                    continue
                yield RawMail(data=raw, origin=f"imap:{mailbox}/{uid}", item=uid)

    @contextmanager
    def _session(self, mailbox: str | None = None) -> Iterator[ImapClient]:
        """Yields a logged-in, read-only (EXAMINE) connection; any IMAP failure becomes a ``SourceError``."""
        credential = self._credential()
        client = self._connect()
        try:
            _open_read_only(client, credential, mailbox or self._settings.mailbox)
            yield client
        except ForbiddenImapCommandError:
            raise SourceError("imap_command_forbidden") from None
        except imaplib.IMAP4.error:
            raise SourceError("imap_protocol_error") from None
        except OSError:
            raise SourceError("imap_connection_lost") from None
        finally:
            _logout(client)

    def _credential(self) -> Credential:
        """Returns the checked mailbox credential, before any network access.

        Raises:
            SourceError: The credential is missing or unusable, or it names another user than
                ``allowed_username`` (``username_not_allowed``).
        """
        settings = self._settings
        try:
            credential = self._credentials.get_credential(settings.credential_target)
            check_login_charset(credential.password)
        except CredentialError as exc:
            raise SourceError(exc.code) from None
        if _normalized_user(credential.username) != _normalized_user(settings.allowed_username):
            raise SourceError("username_not_allowed")
        return credential

    def _connect(self) -> ImapClient:
        """Opens the connection.

        Raises:
            SourceError: ``tls_certificate_invalid`` or ``imap_connection_failed``.
        """
        try:
            return self._client_factory(self._settings)
        except ssl.SSLCertVerificationError:
            raise SourceError("tls_certificate_invalid") from None
        except (OSError, imaplib.IMAP4.error):
            raise SourceError("imap_connection_failed") from None
