"""The run log: every fetch and AI catch-up with its result, shown as "Letzter Abruf" in the inbox."""

from .conversion import now_text, run_from_row, to_json
from .database import Database
from .records import FetchRun, RunResult


class RunLog:
    """Records when a run started, how it ended, and returns the last one."""

    def __init__(self, database: Database) -> None:
        """Works on ``database``."""
        self._database = database

    def start(self, kind: str, trigger: str) -> int:
        """Records the start of a run and returns its id.

        Args:
            kind: ``abruf``, ``ki-nachholen`` or ``neu-auswerten``.
            trigger: ``manuell``, ``zeitplan``, ``kommandozeile`` or ``absender``.

        Returns:
            The id to pass to :meth:`finish`.
        """
        with self._database.connect() as connection:
            cursor = connection.execute(
                "INSERT INTO fetch_runs (kind, trigger, started_at) VALUES (?, ?, ?)",
                (kind, trigger, now_text()),
            )
        return int(cursor.lastrowid or 0)

    def finish(self, run_id: int, result: RunResult) -> None:
        """Records the end of a run with its result."""
        with self._database.connect() as connection:
            connection.execute(
                "UPDATE fetch_runs SET finished_at = ?, found = ?, imported = ?, duplicates = ?, failed = ?,"
                " ai_pending = ?, error = ? WHERE id = ?",
                (
                    now_text(),
                    result.found,
                    result.imported,
                    result.duplicates,
                    to_json(list(result.failed)),
                    result.ai_pending,
                    result.error,
                    run_id,
                ),
            )

    def last(self, kind: str = "abruf") -> FetchRun | None:
        """Returns the last finished run of a kind, or ``None`` before the first one."""
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM fetch_runs WHERE kind = ? AND finished_at IS NOT NULL"
                " ORDER BY id DESC LIMIT 1",
                (kind,),
            ).fetchone()
        return run_from_row(row) if row else None
