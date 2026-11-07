"""Hand-over of the working data between the two PCs.

The two PCs are never used at the same time. The working data (database with the fetch position, filed
attachments, saved known senders) travels as **one encrypted file** through a folder both PCs see (Google
Drive or Dropbox). Next to it lies a small **claim** file that says which PC currently holds the data;
only that PC fetches, so the two databases never drift apart (they could not be merged).

- The active PC writes the package after every fetch and after changes in the web page (a minute after the
  last change), so it cannot be forgotten. Every package has a higher **generation**.
- The other PC shows "Übergabe übernehmen" when the package is newer than its own data. Taking over keeps
  the old data folder as a backup, installs the package and writes the claim; the first PC sees the claim
  and stops fetching.

Encryption: AES-256-GCM in chunks of 4 MiB (each chunk authenticated, the header and the chunk order too, so
a truncated or altered file is rejected). The key is derived with scrypt from the mailbox password, which
lies on both PCs in the Windows credential store anyway; nothing extra has to be set up, and nobody without
the mailbox password can read the file. Only the claim (PC name, generation, time) is plain text.

Layout of a package file: ``MAGIC``, the header length (4 bytes, big-endian), the header (JSON), then the
sealed chunks, each with its own 4-byte length in front.
"""

import base64
import hashlib
import itertools
import json
import os
import shutil
import socket
import sqlite3
import tempfile
import zipfile
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Self

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .security.redaction import get_logger
from .storage import SCHEMA_VERSION

_log = get_logger("handover")

PACKAGE_NAME = "MailAgent-Uebergabe.vma"
"""The encrypted package in the shared folder."""
CLAIM_NAME = "MailAgent-Uebergabe-aktiv.json"
"""The plain claim next to the package: which PC holds the data."""
STATE_NAME = "uebergabe.json"
"""Per PC, next to the data folder (``daten/uebergabe.json``) and never part of a package."""
DATABASE_NAME = "mail_agent.sqlite3"
"""The live database inside the data folder (the same name as ``Settings.database_path``)."""
MAGIC = b"MAILAGENT-UEBERGABE\n"
"""First bytes of every package, so any other file is recognised as damaged at once."""
FORMAT_VERSION = 1
"""Version of the package layout; a package with another version is refused (``handover_newer_format``)."""
CHUNK_BYTES = 4 * 1024 * 1024
"""4 MiB per encrypted chunk: memory stays small however large the database grows."""
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**15, 8, 1
"""About 32 MiB and a tenth of a second per try: guessing the mailbox password from a stolen file is slow."""
SCRYPT_MAXMEM = 64 * 1024 * 1024
"""Memory limit for scrypt: it needs slightly more than OpenSSL's default of 32 MiB (128 · r · N bytes)."""
KEY_BYTES = 32
"""AES-256 needs a key of 32 bytes."""
SALT_BYTES = 16
"""Random salt per package for scrypt (128 bits, the minimum NIST SP 800-132 recommends)."""
NONCE_PREFIX_BYTES = 8
"""Random part of the 12-byte GCM nonce, new for every package; the other 4 bytes number the chunks."""
NONCE_COUNTER_BYTES = 4
"""Chunk number inside the nonce: room for 2**32 chunks of 4 MiB, far more than any data folder."""
AAD_COUNTER_BYTES = 8
"""Chunk number inside the authenticated data of every chunk."""
FINAL_MARK, MIDDLE_MARK = b"\x01", b"\x00"
"""Last byte of the authenticated data: marks the final chunk, so a truncated file is noticed."""
LENGTH_BYTES = 4
"""Big-endian length in front of the header and in front of every sealed chunk."""
TAG_BYTES = 16
"""GCM appends a 16-byte authentication tag to every chunk."""
MAX_HEADER_BYTES = 64 * 1024
"""Upper limit for the header (a real one has a few hundred bytes); a larger length means a damaged file."""
ZIP_LEVEL = 6
"""zlib's default compression level: a good balance between size and speed."""
KEEP_BACKUPS = 2
"""Old data folders kept after a take-over (each about as large as the data, currently around 150 MB)."""
SKIPPED_FILES = frozenset(
    (DATABASE_NAME, f"{DATABASE_NAME}-wal", f"{DATABASE_NAME}-shm", f"{DATABASE_NAME}-journal")
)
"""The database goes into the package as a consistent snapshot, never as the live file."""
TEMP_PREFIX = "mail-agent-uebergabe-"
"""Prefix of the temporary folders, so a leftover one is easy to recognise."""
ARCHIVE_NAME = "daten.zip"
"""The unencrypted archive inside a temporary folder."""
STAMP_FORMAT = "%Y%m%d-%H%M%S"
"""Local time in the names of the backup and incoming folders; sorts by date."""
CLOUD_DRIVE_LETTERS = "GHIJKLMNOPQRSTUVWXYZ"
"""Drive letters Google Drive for desktop may use (it takes ``G:`` or the next free letter)."""
CLOUD_DRIVE_ROOTS = ("Meine Ablage", "My Drive")
"""The personal folder of Google Drive, in German and in English."""
SUGGESTED_FOLDER = "MailAgent-Uebergabe"
"""Name of the hand-over folder suggested inside a cloud folder."""


class HandoverError(Exception):
    """A hand-over failed. ``code`` is a fixed diagnostic code; the web page shows a German text for it."""

    def __init__(self, code: str) -> None:
        """Keeps ``code``, which never contains content or secrets."""
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PackageHeader:
    """Plain part of a package; also authenticated, so it cannot be changed without the key."""

    format: int
    generation: int
    pc: str
    created_at: str
    schema_version: int
    messages: int
    salt: str
    nonce_prefix: str

    @classmethod
    def from_bytes(cls, raw: bytes) -> Self:
        """Parses the header of a package.

        Raises:
            HandoverError: ``handover_damaged`` if the bytes are no header, ``handover_newer_format`` if
                the package was written in another layout.
        """
        try:
            header = cls(**json.loads(raw))
        except (ValueError, TypeError):
            raise HandoverError("handover_damaged") from None
        if header.format != FORMAT_VERSION:
            raise HandoverError("handover_newer_format")
        return header

    def to_bytes(self) -> bytes:
        """Returns the header as JSON with sorted keys, exactly as it is written and authenticated."""
        return json.dumps(asdict(self), sort_keys=True).encode("utf-8")

    @property
    def created(self) -> datetime:
        """Returns the time the package was written (UTC)."""
        return datetime.fromisoformat(self.created_at)


@dataclass(frozen=True)
class Claim:
    """Which PC currently holds the data (``MailAgent-Uebergabe-aktiv.json``)."""

    pc: str
    generation: int
    at: str


@dataclass(frozen=True)
class HandoverState:
    """This PC's view, in ``daten/uebergabe.json``."""

    folder: str = ""
    generation: int = 0
    """Generation of the data on this PC (written or installed)."""
    active: bool = False
    """This PC holds the data and may fetch."""
    changed: bool = False
    """Data changed since the last package (survives a restart, so nothing is left behind)."""
    last_export: str = ""
    last_import: str = ""


def _utc_now() -> str:
    """Returns the current UTC time to the second, as written into headers, claims and the state."""
    return datetime.now(UTC).isoformat(timespec="seconds")


# --- encryption ---------------------------------------------------------------------------------------------


def derive_key(secret: str, salt: bytes) -> bytes:
    """Derives the 32-byte package key from the mailbox password with scrypt."""
    return hashlib.scrypt(
        secret.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        maxmem=SCRYPT_MAXMEM,
        dklen=KEY_BYTES,
    )


class PackageCipher:
    """Seals and opens the chunks of one package with AES-256-GCM.

    Every chunk gets its own nonce (random prefix plus chunk number) and its own authenticated data
    (header, chunk number, final mark). So no chunk can be dropped, reordered, moved into another package
    or cut off at the end without the check failing.
    """

    def __init__(self, key: bytes, header: bytes, nonce_prefix: bytes) -> None:
        """Works with ``key`` for the package whose raw header is ``header``."""
        self._aead = AESGCM(key)
        self.header = header
        self._nonce_prefix = nonce_prefix

    def encrypt(self, source: BinaryIO, target: BinaryIO) -> None:
        """Encrypts ``source`` chunk by chunk into ``target``, each sealed chunk with its length in front."""
        for index, (chunk, final) in enumerate(_chunks(source)):
            sealed = self._aead.encrypt(self._nonce(index), chunk, self._aad(index, final=final))
            target.write(len(sealed).to_bytes(LENGTH_BYTES, "big") + sealed)

    def decrypt(self, source: BinaryIO, target: BinaryIO) -> None:
        """Decrypts what :meth:`encrypt` wrote and checks that nothing is missing, altered or added.

        Raises:
            HandoverError: ``handover_wrong_key`` if the first chunk does not fit (another mailbox password
                or another header), ``handover_damaged`` for a truncated, altered or extended file.
        """
        for index in itertools.count():
            opened = self._open(index, _read_sealed_chunk(source))
            if opened is None:
                # the very first chunk failing means the key does not fit (other mailbox password)
                raise HandoverError("handover_wrong_key" if index == 0 else "handover_damaged")
            plain, final = opened
            target.write(plain)
            if final:
                if source.read(1):
                    raise HandoverError("handover_damaged")  # data after the final chunk
                return

    def _open(self, index: int, sealed: bytes) -> tuple[bytes, bool] | None:
        """Returns the plain chunk and whether it is the final one; ``None`` if it does not authenticate."""
        for final in (False, True):
            try:
                return self._aead.decrypt(self._nonce(index), sealed, self._aad(index, final=final)), final
            except InvalidTag:
                continue
        return None

    def _nonce(self, index: int) -> bytes:
        """Builds the 12-byte nonce of chunk ``index``: never used twice with the same key."""
        return self._nonce_prefix + index.to_bytes(NONCE_COUNTER_BYTES, "big")

    def _aad(self, index: int, *, final: bool) -> bytes:
        """Builds the authenticated data of chunk ``index``: header, chunk number and final mark."""
        return self.header + index.to_bytes(AAD_COUNTER_BYTES, "big") + (FINAL_MARK if final else MIDDLE_MARK)


def _chunks(source: BinaryIO) -> Iterator[tuple[bytes, bool]]:
    """Yields the chunks of ``source``, each with whether it is the last; an empty source gives one."""
    chunk = source.read(CHUNK_BYTES)
    while True:
        following = source.read(CHUNK_BYTES)
        yield chunk, not following
        if not following:
            return
        chunk = following


def _read_sealed_chunk(source: BinaryIO) -> bytes:
    """Reads one sealed chunk with its length in front.

    Raises:
        HandoverError: ``handover_damaged`` if the file ends early or the length is impossible.
    """
    size_bytes = source.read(LENGTH_BYTES)
    if len(size_bytes) != LENGTH_BYTES:
        raise HandoverError("handover_damaged")  # ended before the final chunk: truncated
    size = int.from_bytes(size_bytes, "big")
    if size > CHUNK_BYTES + TAG_BYTES:
        # checked before reading, so a damaged length never makes the agent read gigabytes into memory
        raise HandoverError("handover_damaged")
    sealed = source.read(size)
    if len(sealed) != size:
        raise HandoverError("handover_damaged")
    return sealed


def encrypt_stream(
    source: BinaryIO, target: BinaryIO, key: bytes, header: bytes, nonce_prefix: bytes
) -> None:
    """Encrypts ``source`` into ``target`` in authenticated chunks (see :class:`PackageCipher`).

    Args:
        source: The plain data, read in chunks of ``CHUNK_BYTES``.
        target: Receives the sealed chunks.
        key: The package key from :func:`derive_key`.
        header: The raw package header; every chunk authenticates it.
        nonce_prefix: The random first 8 bytes of every nonce.
    """
    PackageCipher(key, header, nonce_prefix).encrypt(source, target)


def decrypt_stream(
    source: BinaryIO, target: BinaryIO, key: bytes, header: bytes, nonce_prefix: bytes
) -> None:
    """Decrypts what :func:`encrypt_stream` wrote; the arguments mirror it.

    Raises:
        HandoverError: ``handover_wrong_key`` or ``handover_damaged`` (see :meth:`PackageCipher.decrypt`).
    """
    PackageCipher(key, header, nonce_prefix).decrypt(source, target)


# --- header and claim ---------------------------------------------------------------------------------------


def read_header(path: Path) -> PackageHeader:
    """Reads the plain header of the package at ``path`` without decrypting anything.

    Raises:
        HandoverError: ``handover_no_package``, ``handover_folder_unreadable``, ``handover_damaged`` or
            ``handover_newer_format``.
    """
    try:
        with path.open("rb") as file:
            return _read_header(file)[0]
    except FileNotFoundError:
        raise HandoverError("handover_no_package") from None
    except OSError:
        raise HandoverError("handover_folder_unreadable") from None


def decode_field(value: str) -> bytes:
    """Decodes a base64 field of the package header (salt or nonce prefix).

    Raises:
        HandoverError: ``handover_damaged`` if the field is not valid base64, e.g. in a damaged file.
    """
    try:
        return base64.b64decode(value, validate=True)
    except ValueError:  # binascii.Error is a ValueError
        raise HandoverError("handover_damaged") from None


def _read_header(file: BinaryIO) -> tuple[PackageHeader, bytes]:
    """Reads magic and header from the start of a package.

    Returns:
        The header and its raw bytes, which every chunk authenticates.

    Raises:
        HandoverError: ``handover_damaged`` or ``handover_newer_format``.
    """
    if file.read(len(MAGIC)) != MAGIC:
        raise HandoverError("handover_damaged")
    size = int.from_bytes(file.read(LENGTH_BYTES), "big")
    if not 0 < size <= MAX_HEADER_BYTES:
        raise HandoverError("handover_damaged")
    raw = file.read(size)
    return PackageHeader.from_bytes(raw), raw


def read_claim(folder: Path) -> Claim | None:
    """Returns the claim in ``folder``, or ``None`` if there is none or it cannot be read."""
    try:
        data = json.loads((folder / CLAIM_NAME).read_text(encoding="utf-8"))
        return Claim(pc=str(data["pc"]), generation=int(data["generation"]), at=str(data["at"]))
    except FileNotFoundError:
        return None
    except (OSError, ValueError, KeyError, TypeError):
        return None  # unreadable claim (cloud sync in progress): decided again on the next check


def write_claim(folder: Path, pc: str, generation: int) -> None:
    """Writes the claim that ``pc`` holds the data of ``generation`` (replaced only when complete)."""
    claim = {"pc": pc, "generation": generation, "at": _utc_now()}
    _write_atomically(folder / CLAIM_NAME, lambda f: f.write(json.dumps(claim).encode("utf-8")))


def _write_atomically(path: Path, write: Callable[[BinaryIO], object]) -> None:
    """Writes next to ``path``, then renames, so the cloud never syncs half a file as the real one."""
    temporary = path.with_name(path.name + ".neu")
    with temporary.open("wb") as file:
        write(file)
        file.flush()
        os.fsync(file.fileno())
    temporary.replace(path)


# --- writing a package --------------------------------------------------------------------------------------


class PackageWriter:
    """Encrypts the data folder into the package of the shared folder."""

    def __init__(self, data_dir: Path, secret: str) -> None:
        """Packs ``data_dir``; the key comes from ``secret``, the mailbox password."""
        self._data_dir = data_dir
        self._secret = secret

    def write(self, folder: Path, *, generation: int, pc: str) -> PackageHeader:
        """Writes ``folder/MailAgent-Uebergabe.vma``; an older package is replaced only once this one is done.

        Args:
            folder: The shared hand-over folder.
            generation: The generation of the new package.
            pc: The name of this PC.

        Returns:
            The header of the new package.
        """
        salt, nonce_prefix = os.urandom(SALT_BYTES), os.urandom(NONCE_PREFIX_BYTES)
        with tempfile.TemporaryDirectory(prefix=TEMP_PREFIX) as root:
            archive = Path(root, ARCHIVE_NAME)
            messages = self._pack(archive)
            header = PackageHeader(
                format=FORMAT_VERSION,
                generation=generation,
                pc=pc,
                created_at=_utc_now(),
                schema_version=SCHEMA_VERSION,
                messages=messages,
                salt=base64.b64encode(salt).decode("ascii"),
                nonce_prefix=base64.b64encode(nonce_prefix).decode("ascii"),
            )
            cipher = PackageCipher(derive_key(self._secret, salt), header.to_bytes(), nonce_prefix)
            _write_atomically(folder / PACKAGE_NAME, lambda target: _write_sealed(target, cipher, archive))
        return header

    def _pack(self, archive: Path) -> int:
        """Zips the data folder (the database as a snapshot) and returns the number of messages."""
        with tempfile.TemporaryDirectory(prefix=TEMP_PREFIX) as root:
            snapshot = Path(root, DATABASE_NAME)
            messages = _snapshot(self._data_dir / DATABASE_NAME, snapshot)
            with zipfile.ZipFile(
                archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=ZIP_LEVEL
            ) as zipped:
                zipped.write(snapshot, DATABASE_NAME)
                for path, name in self._files():
                    zipped.write(path, name)
        return messages

    def _files(self) -> Iterator[tuple[Path, str]]:
        """Yields every file of the data folder with its name in the archive, except the live database."""
        for path in sorted(self._data_dir.rglob("*")):
            relative = path.relative_to(self._data_dir)
            if path.is_file() and not _is_live_database(relative):
                yield path, relative.as_posix()


def _is_live_database(relative: Path) -> bool:
    """Checks whether ``relative`` is the live database or one of its side files in the data folder."""
    return len(relative.parts) == 1 and relative.name in SKIPPED_FILES


def _snapshot(database: Path, target: Path) -> int:
    """Copies the live database consistently (SQLite backup API, also while the agent writes).

    Returns:
        The number of messages in the copy.
    """
    source = sqlite3.connect(database)
    copy = sqlite3.connect(target)
    try:
        with copy:
            source.backup(copy)
        return int(copy.execute("SELECT COUNT(*) FROM messages").fetchone()[0])
    finally:
        copy.close()
        source.close()


def _write_sealed(target: BinaryIO, cipher: PackageCipher, archive: Path) -> None:
    """Writes magic, header length, header and the encrypted archive into ``target``."""
    target.write(MAGIC + len(cipher.header).to_bytes(LENGTH_BYTES, "big") + cipher.header)
    with archive.open("rb") as source:
        cipher.encrypt(source, target)


def write_package(data_dir: Path, folder: Path, secret: str, *, generation: int, pc: str) -> PackageHeader:
    """Encrypts the data folder into ``folder/MailAgent-Uebergabe.vma`` (replaced only when complete)."""
    return PackageWriter(data_dir, secret).write(folder, generation=generation, pc=pc)


# --- installing a package -----------------------------------------------------------------------------------


class PackageInstaller:
    """Decrypts and checks a package and puts its data in place of the data folder.

    The current data folder is kept as a backup (``daten-sicherung-<time>``); only the newest
    ``KEEP_BACKUPS`` backups stay. Nothing is changed before the whole package has been checked.
    """

    def __init__(self, data_dir: Path, secret: str) -> None:
        """Replaces ``data_dir``; the key comes from ``secret``, the mailbox password."""
        self._data_dir = data_dir
        self._secret = secret

    def install(self, package: Path) -> PackageHeader:
        """Installs ``package`` and returns its header.

        Raises:
            HandoverError: ``handover_no_package``, ``handover_wrong_key``, ``handover_damaged``,
                ``handover_newer_format``, ``handover_update_first`` or ``handover_install_failed``.
        """
        parent = self._data_dir.parent
        parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime(STAMP_FORMAT)
        incoming = parent / f".{self._data_dir.name}-uebergabe-{stamp}"
        try:
            header = self._unpack(package, incoming)
            _check_database(incoming / DATABASE_NAME)
            self._swap(incoming, stamp)
        except OSError:
            raise HandoverError("handover_install_failed") from None
        finally:
            if incoming.exists():
                shutil.rmtree(incoming, ignore_errors=True)
        self._prune_backups()
        return header

    def _unpack(self, package: Path, incoming: Path) -> PackageHeader:
        """Decrypts ``package`` into a temporary archive and extracts it into ``incoming``."""
        with tempfile.TemporaryDirectory(prefix=TEMP_PREFIX, dir=incoming.parent) as root:
            archive = Path(root, ARCHIVE_NAME)
            header = self._decrypt(package, archive)
            _extract(archive, incoming)
        return header

    def _decrypt(self, package: Path, archive: Path) -> PackageHeader:
        """Checks the header of ``package`` and decrypts the rest into ``archive``."""
        try:
            with package.open("rb") as source:
                header, raw_header = _read_header(source)
                if header.schema_version > SCHEMA_VERSION:
                    raise HandoverError("handover_update_first")
                key = derive_key(self._secret, decode_field(header.salt))
                with archive.open("wb") as target:
                    decrypt_stream(source, target, key, raw_header, decode_field(header.nonce_prefix))
        except FileNotFoundError:
            raise HandoverError("handover_no_package") from None
        return header

    def _swap(self, incoming: Path, stamp: str) -> None:
        """Keeps the current data folder as a backup and moves the new data into its place."""
        data_dir = self._data_dir
        if data_dir.exists():
            data_dir.rename(data_dir.parent / f"{data_dir.name}-sicherung-{stamp}")
        incoming.rename(data_dir)

    def _prune_backups(self) -> None:
        """Deletes all but the newest ``KEEP_BACKUPS`` backups (their names sort by time)."""
        data_dir = self._data_dir
        backups = sorted(data_dir.parent.glob(f"{data_dir.name}-sicherung-*"))
        for old in backups[:-KEEP_BACKUPS]:
            shutil.rmtree(old, ignore_errors=True)


def _extract(archive: Path, incoming: Path) -> None:
    """Extracts the decrypted archive after checking its names; it must contain the database."""
    try:
        with zipfile.ZipFile(archive) as zipped:
            _check_members(zipped)
            if DATABASE_NAME not in zipped.namelist():
                raise HandoverError("handover_damaged")
            zipped.extractall(incoming)
    except zipfile.BadZipFile:
        raise HandoverError("handover_damaged") from None


def _check_members(zipped: zipfile.ZipFile) -> None:
    """Refuses names that could land outside the target folder (absolute, ``..``, drive or backslash)."""
    for name in zipped.namelist():
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or ":" in name or "\\" in name:
            raise HandoverError("handover_damaged")


def _check_database(path: Path) -> None:
    """Checks that the incoming database is intact and not newer than this program.

    Raises:
        HandoverError: ``handover_damaged`` or ``handover_update_first``.
    """
    conn = sqlite3.connect(path)
    try:
        version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        result = conn.execute("PRAGMA integrity_check").fetchone()[0]
        conn.execute("SELECT COUNT(*) FROM messages").fetchone()
    except sqlite3.Error:
        raise HandoverError("handover_damaged") from None
    finally:
        conn.close()
    if result != "ok":
        raise HandoverError("handover_damaged")
    if version > SCHEMA_VERSION:
        raise HandoverError("handover_update_first")


def install_package(package: Path, data_dir: Path, secret: str) -> PackageHeader:
    """Decrypts and checks ``package``, keeps the data folder as a backup and puts the new data in place."""
    return PackageInstaller(data_dir, secret).install(package)


# --- this PC's side -----------------------------------------------------------------------------------------


def suggested_folders() -> list[Path]:
    """Returns the Google Drive and Dropbox folders on this PC as suggestions for the hand-over folder."""
    found = []
    for candidate in _cloud_folders():
        try:
            if candidate.is_dir():
                found.append(candidate / SUGGESTED_FOLDER)
        except OSError:
            continue
    return found


def _cloud_folders() -> list[Path]:
    """Lists where Google Drive for desktop and Dropbox usually keep their folders."""
    drives = [Path(f"{drive}:/{name}") for drive in CLOUD_DRIVE_LETTERS for name in CLOUD_DRIVE_ROOTS]
    home = Path.home()
    return [*drives, *(home / "Google Drive" / name for name in CLOUD_DRIVE_ROOTS), home / "Dropbox"]


class StateFile:
    """Reads and writes this PC's :class:`HandoverState`."""

    def __init__(self, path: Path) -> None:
        """Works on ``path`` (``daten/uebergabe.json``)."""
        self.path = path

    def load(self) -> HandoverState:
        """Returns the saved state; a missing or unreadable file means "not set up"."""
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return HandoverState(**data)
        except (FileNotFoundError, ValueError, TypeError):
            return HandoverState()

    def save(self, state: HandoverState) -> None:
        """Writes ``state``; the folder is created when it is missing."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(asdict(state), indent=2), encoding="utf-8")


class Handover:
    """This PC's side of the hand-over: state, claim, writing and taking over packages."""

    def __init__(self, data_dir: Path, secret: Callable[[], str], pc: str | None = None) -> None:
        """Hands over ``data_dir``.

        Args:
            data_dir: The data folder that travels in the package.
            secret: Reads the mailbox password only when a key is needed, so it is never kept.
            pc: The name of this PC; the host name by default.
        """
        self.data_dir = data_dir
        self._secret = secret
        self.pc = pc or socket.gethostname().upper()
        self._state_file = StateFile(data_dir.parent / STATE_NAME)

    # --- state ----------------------------------------------------------------------------------------------

    @property
    def state_path(self) -> Path:
        """Returns where this PC keeps its state (``daten/uebergabe.json``)."""
        return self._state_file.path

    def state(self) -> HandoverState:
        """Returns this PC's saved state."""
        return self._state_file.load()

    def _save(self, state: HandoverState) -> None:
        """Saves this PC's state."""
        self._state_file.save(state)

    @property
    def configured(self) -> bool:
        """Tells whether a hand-over folder is set."""
        return bool(self.state().folder)

    @property
    def folder(self) -> Path:
        """Returns the hand-over folder."""
        return Path(self.state().folder)

    def configure(self, folder: Path, *, may_fetch_now: bool) -> HandoverState:
        """Uses ``folder``; without a package there, this PC starts as the holder if it fetches today."""
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError:
            raise HandoverError("handover_folder_unreadable") from None
        state = replace(self.state(), folder=str(folder))
        if not (folder / PACKAGE_NAME).exists() and read_claim(folder) is None:
            state = replace(state, active=may_fetch_now, changed=may_fetch_now)
        else:
            state = replace(state, active=False)
        self._save(state)
        return state

    # --- who holds the data ---------------------------------------------------------------------------------

    def remote_header(self) -> PackageHeader | None:
        """Returns the header of the package in the folder, or ``None`` without a readable package."""
        if not self.configured:
            return None
        try:
            return read_header(self.folder / PACKAGE_NAME)
        except HandoverError:
            return None

    def remote_newer(self) -> bool:
        """Tells whether the folder holds data this PC does not have yet (offer "Übergabe übernehmen")."""
        header = self.remote_header()
        state = self.state()
        return header is not None and header.pc != self.pc and header.generation > state.generation

    def is_active(self) -> bool:
        """Tells whether this PC may fetch: no longer once another PC claims equal or newer data."""
        state = self.state()
        if not state.active:
            return False
        claim = read_claim(self.folder)
        if claim is not None and claim.pc != self.pc and claim.generation >= state.generation:
            _log.info("Übergabe: %s arbeitet jetzt mit den Daten – Abruf hier aus", claim.pc)
            self._save(replace(state, active=False))
            return False
        return True

    def note_change(self) -> None:
        """Remembers that the data changed, so the next package is written (only on the active PC)."""
        state = self.state()
        if state.folder and state.active and not state.changed:
            self._save(replace(state, changed=True))

    # --- writing and taking over ----------------------------------------------------------------------------

    def export(self) -> PackageHeader:
        """Writes a new package (only the PC that holds the data may)."""
        self._require_folder()
        if not self.is_active():
            raise HandoverError("handover_not_active")
        state = self.state()
        remote = self.remote_header()
        generation = max(state.generation, remote.generation if remote else 0) + 1
        try:
            header = write_package(
                self.data_dir, self.folder, self._key_secret(), generation=generation, pc=self.pc
            )
            write_claim(self.folder, self.pc, generation)
        except OSError:
            raise HandoverError("handover_folder_unreadable") from None
        self._save(
            replace(state, generation=generation, active=True, changed=False, last_export=header.created_at)
        )
        _log.info("Übergabe geschrieben: Stand %d, %d Mails", generation, header.messages)
        return header

    def take_over(self) -> PackageHeader:
        """Installs the newest package and makes this PC the one that fetches."""
        self._require_folder()
        package = self.folder / PACKAGE_NAME
        header = read_header(package)
        state = self.state()
        if not self._is_own_unchanged(header, state):
            header = install_package(package, self.data_dir, self._key_secret())
        try:
            write_claim(self.folder, self.pc, header.generation)
        except OSError:
            raise HandoverError("handover_folder_unreadable") from None
        self._save(
            replace(state, generation=header.generation, active=True, changed=False, last_import=_utc_now())
        )
        _log.info(
            "Übergabe übernommen: Stand %d von %s, %d Mails", header.generation, header.pc, header.messages
        )
        return header

    def _is_own_unchanged(self, header: PackageHeader, state: HandoverState) -> bool:
        """Checks whether the package is this PC's own and unchanged since, so its data is already here."""
        return header.pc == self.pc and header.generation == state.generation

    def _require_folder(self) -> None:
        """Raises ``handover_not_configured`` while no hand-over folder is set."""
        if not self.configured:
            raise HandoverError("handover_not_configured")

    def _key_secret(self) -> str:
        """Reads the mailbox password for the key.

        Raises:
            HandoverError: ``handover_no_key`` if the password cannot be read or is empty.
        """
        try:
            secret = self._secret()
        except Exception:
            raise HandoverError("handover_no_key") from None
        if not secret:
            raise HandoverError("handover_no_key")
        return secret
