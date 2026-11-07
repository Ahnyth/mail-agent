import copy
import ctypes
import json
import logging
import pickle
import ssl
from typing import Any

import pytest

from mail_agent.security.credentials import (
    CRED_TYPE_GENERIC,
    CREDENTIALW,
    Credential,
    CredentialError,
    WindowsCredentialProvider,
    check_login_charset,
)
from mail_agent.security.redaction import RedactingFilter, mask_email, redact
from mail_agent.security.secret import Secret
from mail_agent.security.tls import InsecureTlsContextError, assert_secure_context, build_ssl_context


def test_secret_never_leaks() -> None:
    secret = Secret("geheim")
    assert "geheim" not in repr(secret) + str(secret) + f"{secret}"
    with pytest.raises(TypeError):
        pickle.dumps(secret)
    with pytest.raises(TypeError):
        copy.deepcopy(secret)
    with pytest.raises(TypeError):
        json.dumps(secret)
    assert secret.reveal() == "geheim"
    with pytest.raises(ValueError, match="secret_empty"):
        Secret("")


def test_credential_repr_masks_everything() -> None:
    text = repr(Credential("anna@example.org", Secret("geheim")))
    assert "geheim" not in text
    assert "anna@example.org" not in text


def test_login_charset() -> None:
    check_login_charset(Secret('Abc 123 !"#'))
    for bad in ("pässwort", "zeile\numbruch", "tab\t"):
        with pytest.raises(CredentialError):
            check_login_charset(Secret(bad))


def test_tls_context_is_strict() -> None:
    context = build_ssl_context()
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname
    assert context.minimum_version >= ssl.TLSVersion.TLSv1_2
    context.check_hostname = False
    with pytest.raises(InsecureTlsContextError):
        assert_secure_context(context)


def test_redaction() -> None:
    assert mask_email("anna@example.org") == "a***@***"
    assert redact("Login von anna@example.org: LOGIN anna geheim") == "Login von a***@***: LOGIN ***"


def test_redacting_filter_drops_tracebacks() -> None:
    try:
        raise ValueError("Inhalt der Mail von anna@example.org")
    except ValueError:
        import sys

        record = logging.LogRecord(
            "x", logging.ERROR, __file__, 1, "Fehler bei %s", ("anna@example.org",), sys.exc_info()
        )
    RedactingFilter().filter(record)
    assert record.msg == "Fehler bei a***@*** [ValueError]"
    assert record.exc_info is None


class FakeAdvapi:
    """Simulated advapi32 that returns one generic credential."""

    def __init__(self, username: str | None, password: str, cred_type: int = CRED_TYPE_GENERIC) -> None:
        blob = password.encode("utf-16-le")
        self._buffer = (ctypes.c_ubyte * max(len(blob), 1)).from_buffer_copy(blob or b"\0")
        self._cred = CREDENTIALW()
        self._cred.Type = cred_type
        self._cred.UserName = username
        self._cred.CredentialBlobSize = len(blob)
        self._cred.CredentialBlob = ctypes.cast(self._buffer, ctypes.POINTER(ctypes.c_ubyte))
        self.freed = False

    def CredReadW(self, target: str, cred_type: int, flags: int, out: Any) -> int:  # noqa: N802
        self._pointer = ctypes.pointer(self._cred)  # keep the pointer alive
        out[0] = self._pointer
        return 1

    def CredFree(self, pointer: Any) -> None:  # noqa: N802
        self.freed = True


def test_windows_credential_provider_reads_and_wipes() -> None:
    api = FakeAdvapi("test@example.org", "geheim")
    credential = WindowsCredentialProvider(api).get_credential("MAIL_AGENT/postfach-test")
    assert credential.username == "test@example.org"
    assert credential.password.reveal() == "geheim"
    assert api.freed
    assert bytes(api._buffer) == bytes(len(api._buffer))  # password buffer overwritten


@pytest.mark.parametrize(
    ("api", "code"),
    [
        (FakeAdvapi(None, "geheim"), "credential_username_missing"),
        (FakeAdvapi("u@example.org", "geheim", cred_type=2), "credential_type_not_generic"),
        (FakeAdvapi("u@example.org", ""), "credential_password_empty"),
    ],
)
def test_windows_credential_provider_rejects_bad_entries(api: FakeAdvapi, code: str) -> None:
    with pytest.raises(CredentialError) as exc:
        WindowsCredentialProvider(api).get_credential("MAIL_AGENT/postfach-test")
    assert exc.value.code == code
    assert api.freed


def test_invalid_target_is_rejected_without_api_call() -> None:
    with pytest.raises(CredentialError) as exc:
        WindowsCredentialProvider(object()).get_credential("bad target!")
    assert exc.value.code == "credential_target_invalid"
