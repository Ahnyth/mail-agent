"""Stored messages: adding them, finding them, and loading them with analysis, documents and correction."""

import sqlite3
from collections.abc import Sequence

from ..models import Analysis, FiledDocument, MailMessage, Status
from .analysis_columns import analysis_from_row, insert_analysis
from .conversion import correction_from_row, datetime_text, document_from_row, now_text, parse_datetime
from .database import Database
from .documents import insert_documents
from .records import Correction, MessageDetail, StoredDocument

ALL_MESSAGES = ("1 = 1", ())
"""Condition and parameters that select every message."""


class MessageStore:
    """Adds, finds and loads stored messages."""

    def __init__(self, database: Database) -> None:
        """Works on ``database``."""
        self._database = database

    def contains(self, message_key: str) -> bool:
        """Checks whether a message with this identity was stored before (protection against duplicates)."""
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM messages WHERE message_key = ?", (message_key,)
            ).fetchone()
        return row is not None

    def add(
        self,
        message: MailMessage,
        *,
        origin: str,
        raw: bytes,
        analysis: Analysis,
        documents: Sequence[FiledDocument],
    ) -> int:
        """Stores a new message with its analysis and documents in one transaction.

        Args:
            message: The parsed message.
            origin: Where it came from, e.g. ``imap:4711`` or ``eml:test.eml``.
            raw: The unchanged message; it is needed to analyse the message again later.
            analysis: The first analysis.
            documents: The filed or rejected attachments.

        Returns:
            The id of the new message.
        """
        with self._database.connect() as connection:
            message_id = insert_message(connection, message, origin, raw)
            insert_analysis(connection, message_id, analysis)
            insert_documents(connection, message_id, documents)
        return message_id

    def get(self, message_id: int) -> MessageDetail | None:
        """Returns one message with everything that belongs to it, or ``None`` if the id is unknown."""
        details = self._details("m.id = ?", (message_id,))
        return details[0] if details else None

    def details(self, *, status: Status | None = None) -> list[MessageDetail]:
        """Returns all messages, optionally only those with ``status`` (briefing, lists, exports)."""
        if status is None:
            return self._details(*ALL_MESSAGES)
        return self._details("m.status = ?", (status.value,))

    def raw(self, message_id: int) -> bytes | None:
        """Returns the unchanged message as it came from the mailbox, or ``None`` if the id is unknown."""
        with self._database.connect() as connection:
            row = connection.execute("SELECT raw FROM messages WHERE id = ?", (message_id,)).fetchone()
        return bytes(row["raw"]) if row else None

    def refresh(self, message_id: int, message: MailMessage) -> None:
        """Stores sender and text as the current parser reads them, e.g. a contact form recognised later."""
        with self._database.connect() as connection:
            connection.execute(
                "UPDATE messages SET sender_name = ?, sender_address = ?, reply_to = ?, body = ?,"
                " contact_form = ? WHERE id = ?",
                (
                    message.sender_name,
                    message.sender_address,
                    message.reply_to,
                    message.body,
                    int(message.contact_form),
                    message_id,
                ),
            )

    def _details(self, condition: str, params: tuple[object, ...]) -> list[MessageDetail]:
        """Loads the messages selected by ``condition`` (fixed SQL text, never user input)."""
        with self._database.connect() as connection:
            rows = message_rows(connection, condition, params)
            documents = documents_by_message(connection, condition, params)
            corrections = latest_corrections(connection, condition, params)
        return [
            detail_from_row(row, tuple(documents.get(row["id"], [])), corrections.get(row["id"]))
            for row in rows
        ]


def insert_message(connection: sqlite3.Connection, message: MailMessage, origin: str, raw: bytes) -> int:
    """Stores the message itself and returns its new id."""
    cursor = connection.execute(
        "INSERT INTO messages (message_key, origin, raw, sender_name, sender_address, reply_to,"
        " subject, received_at, body, imported_at, contact_form)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            message.key,
            origin,
            raw,
            message.sender_name,
            message.sender_address,
            message.reply_to,
            message.subject,
            datetime_text(message.received_at),
            message.body,
            now_text(),
            int(message.contact_form),
        ),
    )
    return int(cursor.lastrowid or 0)


def message_rows(
    connection: sqlite3.Connection, condition: str, params: tuple[object, ...]
) -> list[sqlite3.Row]:
    """Returns messages joined with their analysis, oldest first.

    Never the raw message: it holds every attachment and is only needed to analyse again.
    """
    return connection.execute(
        "SELECT m.id, m.origin, m.sender_name, m.sender_address, m.reply_to, m.subject,"  # noqa: S608
        " m.received_at, m.body, m.status, m.answered_at, m.contact_form, a.*"
        " FROM messages m JOIN analyses a ON a.message_id = m.id"
        f" WHERE {condition} ORDER BY m.id",
        params,
    ).fetchall()


def documents_by_message(
    connection: sqlite3.Connection, condition: str, params: tuple[object, ...]
) -> dict[int, list[StoredDocument]]:
    """Returns the documents of the selected messages, grouped by message id."""
    selected = f"SELECT m.id FROM messages m WHERE {condition}"  # noqa: S608
    rows = connection.execute(
        f"SELECT * FROM documents WHERE message_id IN ({selected}) ORDER BY id",  # noqa: S608
        params,
    ).fetchall()
    grouped: dict[int, list[StoredDocument]] = {}
    for row in rows:
        grouped.setdefault(row["message_id"], []).append(document_from_row(row))
    return grouped


def latest_corrections(
    connection: sqlite3.Connection, condition: str, params: tuple[object, ...]
) -> dict[int, Correction]:
    """Returns the newest correction of each selected message that has one."""
    selected = f"SELECT m.id FROM messages m WHERE {condition}"  # noqa: S608
    rows = connection.execute(
        f"SELECT * FROM corrections WHERE message_id IN ({selected}) ORDER BY id",  # noqa: S608
        params,
    ).fetchall()
    return {row["message_id"]: correction_from_row(row) for row in rows}  # later rows overwrite earlier ones


def detail_from_row(
    row: sqlite3.Row, documents: tuple[StoredDocument, ...], correction: Correction | None
) -> MessageDetail:
    """Builds the full message record from a joined row and its documents and correction."""
    return MessageDetail(
        id=row["id"],
        origin=row["origin"],
        sender_name=row["sender_name"],
        sender_address=row["sender_address"],
        reply_to=row["reply_to"],
        subject=row["subject"],
        received_at=parse_datetime(row["received_at"]),
        body=row["body"],
        status=Status(row["status"]),
        analysis=analysis_from_row(row),
        draft_edited=bool(row["draft_edited"]),
        documents=documents,
        correction=correction,
        answered_at=parse_datetime(row["answered_at"]),
        contact_form=bool(row["contact_form"]),
    )
