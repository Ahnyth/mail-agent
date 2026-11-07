"""The working state of messages: open or done, and whether an answer went out."""

from datetime import datetime

from ..models import Category, Status
from ..sent import StoredMail
from .conversion import now_text, parse_datetime
from .database import Database


class WorkflowStore:
    """Changes the status of messages and records answers."""

    def __init__(self, database: Database) -> None:
        """Works on ``database``."""
        self._database = database

    def set_status(self, message_id: int, status: Status) -> bool:
        """Marks one message as open or done; ``False`` if the id is unknown."""
        with self._database.connect() as connection:
            cursor = connection.execute(
                "UPDATE messages SET status = ? WHERE id = ?", (status.value, message_id)
            )
        return cursor.rowcount == 1

    def set_status_many(self, message_ids: list[int], status: Status) -> int:
        """Marks several messages at once (e.g. "Alle Werbung erledigen") and returns how many changed."""
        if not message_ids:
            return 0
        placeholders = ", ".join("?" * len(message_ids))
        with self._database.connect() as connection:
            cursor = connection.execute(
                f"UPDATE messages SET status = ? WHERE id IN ({placeholders})",  # noqa: S608
                (status.value, *message_ids),
            )
        return cursor.rowcount

    def open_ids(self, category: Category) -> list[int]:
        """Returns the ids of the open messages of one category."""
        with self._database.connect() as connection:
            rows = connection.execute(
                "SELECT m.id FROM messages m JOIN analyses a ON a.message_id = m.id"
                " WHERE m.status = ? AND a.category = ?",
                (Status.OPEN.value, category.value),
            ).fetchall()
        return [row["id"] for row in rows]

    def set_answered(self, message_id: int, answered: bool) -> bool:
        """Sets or removes the mark "beantwortet" by hand; ``False`` if the id is unknown."""
        with self._database.connect() as connection:
            cursor = connection.execute(
                "UPDATE messages SET answered_at = ? WHERE id = ?",
                (now_text() if answered else None, message_id),
            )
        return cursor.rowcount == 1

    def mark_replied(self, message_id: int, sent_at: datetime | None) -> bool:
        """Records a reply found in the sent folder: "beantwortet" at the time it was sent, and "erledigt".

        Returns:
            ``True`` if the message was not marked as answered before.
        """
        when = sent_at.isoformat() if sent_at else now_text()
        with self._database.connect() as connection:
            cursor = connection.execute(
                "UPDATE messages SET answered_at = ?, status = ? WHERE id = ? AND answered_at IS NULL",
                (when, Status.DONE.value, message_id),
            )
        return cursor.rowcount == 1

    def reply_candidates(self) -> list[StoredMail]:
        """Returns the messages without a recorded answer, to match them with replies from the sent folder."""
        with self._database.connect() as connection:
            rows = connection.execute(
                "SELECT id, message_key, sender_address, reply_to, subject, received_at FROM messages"
                " WHERE answered_at IS NULL"
            ).fetchall()
        return [
            StoredMail(
                id=row["id"],
                message_key=row["message_key"],
                sender_address=row["sender_address"],
                reply_to=row["reply_to"],
                subject=row["subject"],
                received_at=parse_datetime(row["received_at"]),
            )
            for row in rows
        ]
