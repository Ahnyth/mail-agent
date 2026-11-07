"""The inbox list: filtered, searched and sorted message summaries, and the counters of the filters."""

import sqlite3

from ..models import Category, Status, Urgency
from .conversion import parse_datetime, responsible_from
from .database import Database
from .records import InboxFilter, MessageSummary

SUMMARY_COLUMNS = (
    "m.id, m.sender_name, m.sender_address, m.subject, m.received_at, m.status, m.answered_at,"
    " a.category, a.urgency, a.summary, a.responsible, a.ai_pending,"
    " (SELECT COUNT(*) FROM documents d WHERE d.message_id = m.id) AS document_count"
)
"""The columns of one line of the list; the text column is added only for a search."""
SEARCH_COLUMNS = ("sender_name", "sender_address", "subject", "summary", "body")
"""Where the words of a search are looked for."""


class InboxQueries:
    """Reads the inbox list and the numbers shown next to the filters."""

    def __init__(self, database: Database) -> None:
        """Works on ``database``."""
        self._database = database

    def messages(self, filters: InboxFilter | None = None) -> list[MessageSummary]:
        """Returns the messages that match ``filters``: open before done, then most urgent, then newest.

        Args:
            filters: The restrictions of the list; ``None`` lists every message.

        Returns:
            The lines of the list.
        """
        filters = filters or InboxFilter()
        sql, params = summary_query(filters)
        with self._database.connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        words = filters.search.casefold().split()
        summaries = [summary_from_row(row) for row in rows if matches_all(row, words)]
        return sorted_for_inbox(summaries)

    def counts(self) -> dict[str, int]:
        """Returns the open messages per category and per responsibility plus the totals.

        Returns:
            Keys are category values, ``zustaendig:<value>``, ``offen``, ``erledigt``, ``dringend`` and
            ``ki_ausstehend``.
        """
        with self._database.connect() as connection:
            rows = connection.execute(
                "SELECT a.category, a.urgency, a.responsible, a.ai_pending, m.status"
                " FROM messages m JOIN analyses a ON a.message_id = m.id"
            ).fetchall()
        counts: dict[str, int] = {"offen": 0, "erledigt": 0, "dringend": 0, "ki_ausstehend": 0}
        for row in rows:
            count_row(counts, row)
        return counts


def summary_query(filters: InboxFilter) -> tuple[str, list[str]]:
    """Builds the query of the list; values always travel as parameters, never inside the SQL text."""
    body_column = ", m.body" if filters.search.strip() else ""
    sql = (
        f"SELECT {SUMMARY_COLUMNS}{body_column}"  # noqa: S608 - fixed column lists only
        " FROM messages m JOIN analyses a ON a.message_id = m.id WHERE 1 = 1"
    )
    params: list[str] = []
    if filters.ai_pending:
        sql += " AND a.ai_pending = 1"
    for clause, value in (
        (" AND a.category = ?", filters.category),
        (" AND a.category != ?", filters.exclude_category),
        (" AND m.status = ?", filters.status),
        (" AND a.responsible = ?", filters.responsible),
    ):
        if value is not None:
            sql += clause
            params.append(value.value)
    return sql, params


def matches_all(row: sqlite3.Row, words: list[str]) -> bool:
    """Checks whether every search word appears in one of the searched columns (any case, umlauts too)."""
    if not words:
        return True
    text = " ".join(row[column] for column in SEARCH_COLUMNS).casefold()
    return all(word in text for word in words)


def summary_from_row(row: sqlite3.Row) -> MessageSummary:
    """Builds one line of the list from a row of :func:`summary_query`."""
    return MessageSummary(
        id=row["id"],
        sender=row["sender_name"] or row["sender_address"],
        subject=row["subject"],
        received_at=parse_datetime(row["received_at"]),
        status=Status(row["status"]),
        category=Category(row["category"]),
        urgency=Urgency(row["urgency"]),
        summary=row["summary"],
        document_count=row["document_count"],
        responsible=responsible_from(row["responsible"]),
        answered_at=parse_datetime(row["answered_at"]),
        ai_pending=bool(row["ai_pending"]),
    )


def sorted_for_inbox(summaries: list[MessageSummary]) -> list[MessageSummary]:
    """Sorts open before done, then by urgency, then newest first (two stable passes)."""
    newest_first = sorted(summaries, key=lambda s: -(s.received_at.timestamp() if s.received_at else 0))
    return sorted(newest_first, key=lambda s: (s.status != Status.OPEN, s.urgency.rank))


def count_row(counts: dict[str, int], row: sqlite3.Row) -> None:
    """Adds one message to the counters; only open messages count for categories and urgency."""
    counts[row["status"]] = counts.get(row["status"], 0) + 1
    if row["status"] != Status.OPEN.value:
        return
    counts[row["category"]] = counts.get(row["category"], 0) + 1
    if row["ai_pending"]:
        counts["ki_ausstehend"] += 1
    if row["responsible"]:
        key = f"zustaendig:{row['responsible']}"
        counts[key] = counts.get(key, 0) + 1
    if row["urgency"] == Urgency.HIGH.value:
        counts["dringend"] += 1
