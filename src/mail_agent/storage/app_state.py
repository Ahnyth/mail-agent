"""Small values the agent remembers between starts, e.g. the sent folder it found."""

from .database import Database


class AppState:
    """A key/value table for single settings the agent finds out by itself."""

    def __init__(self, database: Database) -> None:
        """Works on ``database``."""
        self._database = database

    def get(self, key: str) -> str | None:
        """Returns the value stored under ``key``, or ``None``."""
        with self._database.connect() as connection:
            row = connection.execute("SELECT value FROM app_state WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set(self, key: str, value: str) -> None:
        """Stores ``value`` under ``key``, replacing an older value."""
        with self._database.connect() as connection:
            connection.execute(
                "INSERT INTO app_state (key, value) VALUES (?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
