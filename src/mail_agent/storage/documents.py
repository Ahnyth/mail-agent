"""The attachments of stored messages: where they were filed and the text read from them."""

import sqlite3
from collections.abc import Sequence

from ..models import FiledDocument
from .conversion import document_from_row
from .database import Database
from .records import StoredDocument

DocumentTexts = dict[str, tuple[str | None, str | None]]
"""``{filename: (text, note)}``: the text read from an attachment and a note why there is none."""


class DocumentRecords:
    """Reads single documents and stores re-read texts."""

    def __init__(self, database: Database) -> None:
        """Works on ``database``."""
        self._database = database

    def get(self, document_id: int) -> StoredDocument | None:
        """Returns one document, or ``None`` if the id is unknown."""
        with self._database.connect() as connection:
            row = connection.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone()
        return document_from_row(row) if row else None

    def update_texts(self, message_id: int, texts: DocumentTexts) -> None:
        """Stores the texts of the attachments of a message after they were read again."""
        with self._database.connect() as connection:
            connection.executemany(
                "UPDATE documents SET extracted_text = ?, text_note = ?"
                " WHERE message_id = ? AND filename = ?",
                [(text, note, message_id, name) for name, (text, note) in texts.items()],
            )


def insert_documents(
    connection: sqlite3.Connection, message_id: int, documents: Sequence[FiledDocument]
) -> None:
    """Stores the filed or rejected attachments of a new message."""
    connection.executemany(
        "INSERT INTO documents (message_id, filename, content_type, size, sha256, document_type,"
        " stored_path, rejected_reason, extracted_text, text_note)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                message_id,
                d.filename,
                d.content_type,
                d.size,
                d.sha256,
                d.document_type,
                d.stored_path,
                d.rejected_reason,
                d.extracted_text,
                d.text_note,
            )
            for d in documents
        ],
    )
