"""Access to the SQLite file: one short connection per operation."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .schema import upgrade

CONNECT_TIMEOUT_SECONDS = 30
"""How long a writer waits for another writer before giving up; readers never wait thanks to WAL."""


class Database:
    """The SQLite file of one PC; every store of the repository opens its connections here."""

    def __init__(self, path: Path) -> None:
        """Remembers the file; nothing is opened before the first call."""
        self._path = path

    @property
    def path(self) -> Path:
        """The database file."""
        return self._path

    def migrate(self) -> None:
        """Creates the folder and every missing table and column (see :func:`schema.upgrade`)."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            upgrade(connection)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """Opens a connection that commits on success and rolls back on an error.

        Short connections keep the file free for the other thread: the import writes in the background
        while the web page reads.

        Yields:
            The connection, with named columns and foreign keys switched on.
        """
        connection = sqlite3.connect(self._path, timeout=CONNECT_TIMEOUT_SECONDS)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            with connection:  # commits on success, rolls back on error
                yield connection
        finally:
            connection.close()
