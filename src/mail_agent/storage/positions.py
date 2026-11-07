"""How far each source has been read, so an interrupted fetch continues where it stopped."""

from .conversion import now_text, parse_date
from .database import Database
from .records import FetchState


class FetchPositions:
    """Reads and stores the read position of the inbox and of the sent folder."""

    def __init__(self, database: Database) -> None:
        """Works on ``database``."""
        self._database = database

    def get(self, source: str) -> FetchState | None:
        """Returns the position of a source (its identity, e.g. ``imap:host/user/INBOX``), or ``None``."""
        with self._database.connect() as connection:
            row = connection.execute("SELECT * FROM fetch_state WHERE source = ?", (source,)).fetchone()
        if row is None:
            return None
        return FetchState(row["source"], row["validity"], row["last_uid"], parse_date(row["start_date"]))

    def save(self, state: FetchState) -> None:
        """Stores the position of a source, replacing the previous one."""
        with self._database.connect() as connection:
            connection.execute(
                "INSERT INTO fetch_state (source, validity, last_uid, start_date, updated_at)"
                " VALUES (?, ?, ?, ?, ?) ON CONFLICT(source) DO UPDATE SET validity = excluded.validity,"
                " last_uid = excluded.last_uid, start_date = excluded.start_date,"
                " updated_at = excluded.updated_at",
                (
                    state.source,
                    state.validity,
                    state.last_uid,
                    state.start_date.isoformat() if state.start_date else None,
                    now_text(),
                ),
            )
