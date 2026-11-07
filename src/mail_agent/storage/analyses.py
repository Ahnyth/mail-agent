"""The analyses of stored messages: new analyses, edited drafts, and which messages may be analysed again."""

import sqlite3

from ..models import Analysis, Status
from .analysis_columns import ANALYSIS_ASSIGNMENTS, analysis_values
from .conversion import now_text
from .database import Database

UNTOUCHED = " AND draft_edited = 0 AND NOT EXISTS (SELECT 1 FROM corrections c WHERE c.message_id = ?)"
"""Extra condition: the draft was not edited and nobody corrected the analysis by hand."""


class AnalysisStore:
    """Replaces analyses, stores drafts and finds messages that can be analysed again without losing work."""

    def __init__(self, database: Database) -> None:
        """Works on ``database``."""
        self._database = database

    def replace(self, message_id: int, analysis: Analysis, *, only_if_untouched: bool = False) -> bool:
        """Stores a new analysis of a message; a correction by hand still outranks it.

        Args:
            message_id: The message.
            analysis: The new analysis.
            only_if_untouched: Leave the message alone if its draft was edited or its analysis corrected
                in the meantime.

        Returns:
            Whether the analysis was stored.
        """
        with self._database.connect() as connection:
            if not update_analysis(connection, message_id, analysis, only_if_untouched=only_if_untouched):
                return False
            reapply_correction(connection, message_id)
            update_document_types(connection, message_id, analysis)
        return True

    def save_draft(self, message_id: int, reply_body: str) -> bool:
        """Stores a draft edited by hand; a later analysis keeps it. ``False`` if the id is unknown."""
        with self._database.connect() as connection:
            cursor = connection.execute(
                "UPDATE analyses SET reply_body = ?, draft_edited = 1 WHERE message_id = ?",
                (reply_body, message_id),
            )
        return cursor.rowcount == 1

    def pending_ai_ids(self) -> list[int]:
        """Returns the open messages waiting for the AI that can be analysed again without losing work."""
        return self.untouched_open_ids(pending_only=True)

    def untouched_open_ids(self, ids: list[int] | None = None, *, pending_only: bool = False) -> list[int]:
        """Returns open messages without edited draft and without correction, oldest first.

        Args:
            ids: Only look among these messages; ``None`` looks at all.
            pending_only: Only messages that still wait for the AI.

        Returns:
            The matching message ids.
        """
        sql = (
            "SELECT m.id FROM messages m JOIN analyses a ON a.message_id = m.id"
            " WHERE a.draft_edited = 0 AND m.status = ?"
            " AND NOT EXISTS (SELECT 1 FROM corrections c WHERE c.message_id = m.id)"
        )
        if pending_only:
            sql += " AND a.ai_pending = 1"
        with self._database.connect() as connection:
            rows = connection.execute(sql + " ORDER BY m.received_at, m.id", (Status.OPEN.value,)).fetchall()
        found = [row["id"] for row in rows]
        if ids is None:
            return found
        wanted = set(ids)
        return [i for i in found if i in wanted]


def update_analysis(
    connection: sqlite3.Connection, message_id: int, analysis: Analysis, *, only_if_untouched: bool
) -> bool:
    """Overwrites the analysis columns and resets the "edited" mark of the draft.

    Returns:
        ``False`` if no row matched (unknown id, or touched by hand with ``only_if_untouched``).
    """
    condition = UNTOUCHED if only_if_untouched else ""
    cursor = connection.execute(
        f"UPDATE analyses SET {ANALYSIS_ASSIGNMENTS}, draft_edited = 0, analysed_at = ?"  # noqa: S608
        f" WHERE message_id = ?{condition}",
        (*analysis_values(analysis), now_text(), message_id, *((message_id,) if only_if_untouched else ())),
    )
    return cursor.rowcount == 1


def reapply_correction(connection: sqlite3.Connection, message_id: int) -> None:
    """Puts the newest correction back over the new analysis (category, priority, reply, responsible)."""
    correction = connection.execute(
        "SELECT category, urgency, reply_needed, responsible FROM corrections"
        " WHERE message_id = ? ORDER BY id DESC LIMIT 1",
        (message_id,),
    ).fetchone()
    if correction is not None:
        connection.execute(
            "UPDATE analyses SET category = ?, urgency = ?, reply_needed = ?, responsible = ?"
            " WHERE message_id = ?",
            (*correction, message_id),
        )


def update_document_types(connection: sqlite3.Connection, message_id: int, analysis: Analysis) -> None:
    """Stores the document types that the new analysis recognised."""
    for document in analysis.documents:
        connection.execute(
            "UPDATE documents SET document_type = ? WHERE message_id = ? AND filename = ?",
            (document.document_type, message_id, document.filename),
        )
