"""Corrections by hand: stored with the original values, so the evaluation can learn from them."""

import sqlite3

from .conversion import correction_from_row, now_text
from .database import Database
from .records import Correction, CorrectionChange


class CorrectionStore:
    """Stores corrections, lists them and remembers which ones became test cases."""

    def __init__(self, database: Database) -> None:
        """Works on ``database``."""
        self._database = database

    def add(self, message_id: int, change: CorrectionChange) -> bool:
        """Stores a correction with the original values and applies it to the shown analysis.

        Returns:
            ``False`` if the message has no analysis (unknown id).
        """
        with self._database.connect() as connection:
            original = connection.execute(
                "SELECT category, urgency, reply_needed, responsible, engine FROM analyses"
                " WHERE message_id = ?",
                (message_id,),
            ).fetchone()
            if original is None:
                return False
            insert_correction(connection, message_id, original, change)
            apply_correction(connection, message_id, change)
        return True

    def all(self) -> list[Correction]:
        """Returns every correction, oldest first."""
        with self._database.connect() as connection:
            rows = connection.execute("SELECT * FROM corrections ORDER BY id").fetchall()
        return [correction_from_row(row) for row in rows]

    def mark_exported(self, correction_id: int, path: str) -> None:
        """Remembers the file name of the test case written from a correction."""
        with self._database.connect() as connection:
            connection.execute("UPDATE corrections SET exported_to = ? WHERE id = ?", (path, correction_id))


def insert_correction(
    connection: sqlite3.Connection, message_id: int, original: sqlite3.Row, change: CorrectionChange
) -> None:
    """Stores the correction row: the original values of the analysis next to the new ones."""
    connection.execute(
        "INSERT INTO corrections (message_id, original_category, original_urgency, original_reply,"
        " original_responsible, category, urgency, reply_needed, responsible, note, engine, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            message_id,
            original["category"],
            original["urgency"],
            original["reply_needed"],
            original["responsible"],
            *change_values(change),
            change.note,
            original["engine"],
            now_text(),
        ),
    )


def apply_correction(connection: sqlite3.Connection, message_id: int, change: CorrectionChange) -> None:
    """Shows the corrected values in the analysis of the message."""
    connection.execute(
        "UPDATE analyses SET category = ?, urgency = ?, reply_needed = ?, responsible = ?"
        " WHERE message_id = ?",
        (*change_values(change), message_id),
    )


def change_values(change: CorrectionChange) -> tuple[str, str, int, str | None]:
    """Returns category, urgency, reply and responsibility of a change as column values."""
    return (
        change.category.value,
        change.urgency.value,
        int(change.reply_needed),
        change.responsible.value if change.responsible else None,
    )
