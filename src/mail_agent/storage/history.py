"""What the agent knows about earlier mails of a sender, so the AI sorts one sender consistently."""

import sqlite3

from ..models import Category, Status
from .conversion import parse_datetime
from .database import Database

SenderRow = tuple[int, str, str]
"""``(message_id, sender_address, reply_to)`` of one message."""


class SenderHistory:
    """Lists the senders of stored messages and describes earlier mails without their content."""

    def __init__(self, database: Database) -> None:
        """Works on ``database``."""
        self._database = database

    def sender_rows(self, status: Status | None = None) -> list[SenderRow]:
        """Returns id, sender and reply address of all messages, newest first, to group them by sender.

        Args:
            status: Only messages with this status; ``None`` lists all.

        Returns:
            One row per message.
        """
        sql = "SELECT id, sender_address, reply_to FROM messages"
        params: tuple[str, ...] = ()
        if status is not None:
            sql += " WHERE status = ?"
            params = (status.value,)
        with self._database.connect() as connection:
            rows = connection.execute(sql + " ORDER BY received_at DESC, id DESC", params).fetchall()
        return [(row["id"], row["sender_address"], row["reply_to"]) for row in rows]

    def lines(self, message_ids: list[int]) -> tuple[str, ...]:
        """Describes earlier messages in one line each: date, category, priority, draft, correction.

        No mail content goes into these lines; they only tell the AI how the office sorted the sender before.

        Args:
            message_ids: The messages in the order wanted (newest first).

        Returns:
            One line per message that still exists, in the given order.
        """
        if not message_ids:
            return ()
        with self._database.connect() as connection:
            rows = history_rows(connection, message_ids)
        by_id = {row["id"]: row for row in rows}
        return tuple(history_line(by_id[i]) for i in message_ids if i in by_id)


def history_rows(connection: sqlite3.Connection, message_ids: list[int]) -> list[sqlite3.Row]:
    """Returns date, category, priority, draft need and correction mark of the given messages."""
    placeholders = ", ".join("?" * len(message_ids))
    return connection.execute(
        "SELECT m.id, m.received_at, a.category, a.urgency, a.reply_needed,"  # noqa: S608
        " EXISTS (SELECT 1 FROM corrections c WHERE c.message_id = m.id) AS corrected"
        f" FROM messages m JOIN analyses a ON a.message_id = m.id WHERE m.id IN ({placeholders})",
        message_ids,
    ).fetchall()


def history_line(row: sqlite3.Row) -> str:
    """Formats one earlier message, e.g. ``03.11.2026: Abrechnung, Priorität hoch, Antwortentwurf``."""
    received = parse_datetime(row["received_at"])
    line = (
        f"{received.strftime('%d.%m.%Y') if received else 'ohne Datum'}: "
        f"{Category(row['category']).label}, Priorität {row['urgency']}"
        f"{', Antwortentwurf' if row['reply_needed'] else ''}"
    )
    return line + " (so vom Büro korrigiert)" if row["corrected"] else line
