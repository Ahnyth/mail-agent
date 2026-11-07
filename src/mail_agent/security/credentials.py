"""Read-only access to the Windows Credential Manager.

The entry is created by the user with the Windows tool (``/pass`` without value prompts hidden)::

    cmdkey /generic:MAIL_AGENT/postfach /user:<adresse> /pass

Reading uses ``advapi32.CredReadW`` via ``ctypes``. There is deliberately no fallback to environment
variables or files.
"""

import ctypes
import re
import sys
from dataclasses import dataclass
from typing import Any, Protocol

from .redaction import mask_email
from .secret import Secret

CRED_TYPE_GENERIC = 1
"""``CRED_TYPE_GENERIC`` from ``wincred.h``: the kind of entry ``cmdkey /generic`` creates."""
CRED_MAX_CREDENTIAL_BLOB_SIZE = 5 * 512
"""``CRED_MAX_CREDENTIAL_BLOB_SIZE`` from ``wincred.h``: the largest password Windows stores (bytes)."""
CRED_READ_FLAGS = 0
"""The ``Flags`` argument of ``CredReadW``: reserved by Windows, must be zero."""
ERROR_NOT_FOUND = 1168
"""Win32 error code ``ERROR_NOT_FOUND``: there is no entry with this name."""
TARGET_RE = re.compile(r"[A-Za-z0-9_.\-/]{1,200}")
"""Allowed entry names: plain characters only, so a name can never smuggle anything into the API call."""

# Printable ASCII only: imaplib sends LOGIN arguments as quoted strings in ASCII mode.
_PRINTABLE_MIN, _PRINTABLE_MAX = 0x20, 0x7E

_DWORD = ctypes.c_uint32
"""Windows ``DWORD``: an unsigned 32-bit number."""


class CredentialError(Exception):
    """Credential could not be used. ``code`` is a fixed, secret-free diagnostic code."""

    def __init__(self, code: str) -> None:
        """Keeps ``code``, which never contains the user name or the password."""
        super().__init__(code)
        self.code = code


class _FILETIME(ctypes.Structure):
    """Windows ``FILETIME`` (only needed for the layout of :class:`CREDENTIALW`)."""

    _fields_ = [("dwLowDateTime", _DWORD), ("dwHighDateTime", _DWORD)]


class CREDENTIALW(ctypes.Structure):
    """Windows ``CREDENTIALW`` structure, field by field as in ``wincred.h``."""

    _fields_ = [
        ("Flags", _DWORD),
        ("Type", _DWORD),
        ("TargetName", ctypes.c_wchar_p),
        ("Comment", ctypes.c_wchar_p),
        ("LastWritten", _FILETIME),
        ("CredentialBlobSize", _DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
        ("Persist", _DWORD),
        ("AttributeCount", _DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", ctypes.c_wchar_p),
        ("UserName", ctypes.c_wchar_p),
    ]


@dataclass(frozen=True, repr=False)
class Credential:
    """User name and password of the mailbox; its text form never shows either in full."""

    username: str
    password: Secret

    def __repr__(self) -> str:
        return f"Credential(username={mask_email(self.username)!r}, password=***)"

    __str__ = __repr__


class CredentialProvider(Protocol):
    """Hands out credentials by name (the Windows store, or a fake in tests)."""

    def get_credential(self, target: str) -> Credential:
        """Returns the credential stored under ``target``.

        Raises:
            CredentialError: The entry is missing or cannot be used.
        """
        ...


def check_login_charset(password: Secret) -> None:
    """Rejects passwords IMAP LOGIN cannot transport safely, before any network access."""
    if any(not _PRINTABLE_MIN <= ord(char) <= _PRINTABLE_MAX for char in password.reveal()):
        raise CredentialError("password_unsupported_characters")


def _load_advapi32() -> Any:
    """Loads ``advapi32.dll`` and declares the two functions used, so ctypes checks every argument.

    Raises:
        CredentialError: Not on Windows, or the library cannot be loaded.
    """
    if sys.platform != "win32":
        raise CredentialError("credential_manager_requires_windows")
    try:
        api = ctypes.WinDLL("advapi32", use_last_error=True)
    except OSError:
        raise CredentialError("credential_manager_not_loadable") from None
    api.CredReadW.argtypes = [
        ctypes.c_wchar_p,
        _DWORD,
        _DWORD,
        ctypes.POINTER(ctypes.POINTER(CREDENTIALW)),
    ]
    api.CredReadW.restype = ctypes.c_int
    api.CredFree.argtypes = [ctypes.c_void_p]
    api.CredFree.restype = None
    return api


def _last_error() -> int:
    """Returns the Windows error of the last ctypes call; 0 elsewhere."""
    getter = getattr(ctypes, "get_last_error", None)  # Windows only
    return int(getter()) if getter else 0


def decode_password_blob(blob_ptr: Any, size: int) -> Secret:
    """Decodes the UTF-16LE password blob written by ``cmdkey``; the temporary copy is wiped.

    Raises:
        CredentialError: The blob is empty, too large, of odd length or no valid UTF-16.
    """
    address = ctypes.cast(blob_ptr, ctypes.c_void_p).value
    if not address or size <= 0:
        raise CredentialError("credential_password_empty")
    if size > CRED_MAX_CREDENTIAL_BLOB_SIZE or size % 2:
        raise CredentialError("credential_blob_invalid")
    raw = bytearray((ctypes.c_ubyte * size).from_address(address))
    try:
        text = raw.decode("utf-16-le").removesuffix("\x00")
    except UnicodeDecodeError:
        raise CredentialError("credential_blob_not_utf16") from None
    finally:
        raw[:] = bytes(len(raw))
    if not text:
        raise CredentialError("credential_password_empty")
    return Secret(text)


def _read_entry(api: Any, target: str) -> "ctypes._Pointer[CREDENTIALW]":
    """Reads the generic entry ``target``; Windows allocates it, the caller frees it with ``CredFree``.

    Raises:
        CredentialError: ``credential_not_found`` or ``credential_read_failed:<error>``.
    """
    pcred = ctypes.POINTER(CREDENTIALW)()
    if not api.CredReadW(target, CRED_TYPE_GENERIC, CRED_READ_FLAGS, ctypes.pointer(pcred)):
        error = _last_error()
        raise CredentialError(
            "credential_not_found" if error == ERROR_NOT_FOUND else f"credential_read_failed:{error}"
        )
    return pcred


def _credential_from(cred: CREDENTIALW) -> Credential:
    """Builds the credential from an entry; only generic entries with a user name are accepted.

    Raises:
        CredentialError: Wrong kind of entry, no user name, or an unusable password.
    """
    if cred.Type != CRED_TYPE_GENERIC:
        raise CredentialError("credential_type_not_generic")
    if not cred.UserName:
        raise CredentialError("credential_username_missing")
    password = decode_password_blob(cred.CredentialBlob, cred.CredentialBlobSize)
    return Credential(username=cred.UserName, password=password)


def _wipe(address: int | None, size: int) -> None:
    """Overwrites the password Windows handed out with zeros, before ``CredFree`` releases it."""
    if address and 0 < size <= CRED_MAX_CREDENTIAL_BLOB_SIZE:
        ctypes.memset(address, 0, size)


class WindowsCredentialProvider:
    """Reads generic entries. ``api`` is injectable for tests (simulated advapi32)."""

    def __init__(self, api: Any = None) -> None:
        """Uses ``api`` instead of the real ``advapi32.dll`` when given (tests)."""
        self._api = api

    def get_credential(self, target: str) -> Credential:
        """Returns the generic entry ``target``; Windows' copy of the password is wiped and freed.

        Raises:
            CredentialError: Invalid name, missing or unusable entry, or not on Windows.
        """
        if not TARGET_RE.fullmatch(target):
            raise CredentialError("credential_target_invalid")
        api = self._api if self._api is not None else _load_advapi32()
        pcred = _read_entry(api, target)
        blob_address, blob_size = None, 0
        try:
            cred = pcred.contents
            blob_address = ctypes.cast(cred.CredentialBlob, ctypes.c_void_p).value
            blob_size = cred.CredentialBlobSize
            return _credential_from(cred)
        finally:
            _wipe(blob_address, blob_size)
            api.CredFree(pcred)
