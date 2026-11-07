"""How an analysis is spread over the columns of the ``analyses`` table, and read back."""

import json
import sqlite3
from datetime import date

from ..models import Analysis, Category, Deadline, DocumentClassification, Urgency
from .conversion import now_text, payment_from_json, payment_to_json, responsible_from, to_json

ANALYSIS_COLUMNS = (
    "category, urgency, summary, reasons, tasks, reply_needed, reply_subject, reply_body, documents,"
    " warnings, engine, findings, deadlines, payment, responsible, confidence, duration_ms, read_aloud,"
    " attachments_match, ai_pending"
)
"""The columns written for every analysis, in the order of :func:`analysis_values`."""
ANALYSIS_PLACEHOLDERS = ", ".join("?" * len(ANALYSIS_COLUMNS.split(",")))
ANALYSIS_ASSIGNMENTS = ", ".join(f"{column.strip()} = ?" for column in ANALYSIS_COLUMNS.split(","))
"""``category = ?, urgency = ?, …`` for an ``UPDATE`` of all analysis columns."""


def analysis_values(analysis: Analysis) -> tuple[object, ...]:
    """Returns the column values of an analysis in the order of :data:`ANALYSIS_COLUMNS`."""
    return (
        analysis.category.value,
        analysis.urgency.value,
        analysis.summary,
        to_json(list(analysis.reasons)),
        to_json(list(analysis.tasks)),
        int(analysis.reply_needed),
        analysis.reply_subject,
        analysis.reply_body,
        to_json([[d.filename, d.document_type] for d in analysis.documents]),
        to_json(list(analysis.warnings)),
        analysis.engine,
        to_json(list(analysis.findings)),
        to_json([[d.due.isoformat(), d.description] for d in analysis.deadlines]),
        payment_to_json(analysis.payment),
        analysis.responsible.value if analysis.responsible else None,
        analysis.confidence,
        analysis.duration_ms,
        analysis.read_aloud,
        None if analysis.attachments_match is None else int(analysis.attachments_match),
        int(analysis.ai_pending),
    )


def analysis_from_row(row: sqlite3.Row) -> Analysis:
    """Builds an analysis from a row that contains all columns of the ``analyses`` table."""
    return Analysis(
        category=Category(row["category"]),
        urgency=Urgency(row["urgency"]),
        summary=row["summary"],
        reasons=tuple(json.loads(row["reasons"])),
        tasks=tuple(json.loads(row["tasks"])),
        reply_needed=bool(row["reply_needed"]),
        reply_subject=row["reply_subject"],
        reply_body=row["reply_body"],
        documents=tuple(DocumentClassification(f, t) for f, t in json.loads(row["documents"])),
        warnings=tuple(json.loads(row["warnings"])),
        engine=row["engine"],
        findings=tuple(json.loads(row["findings"])),
        deadlines=tuple(Deadline(date.fromisoformat(d), text) for d, text in json.loads(row["deadlines"])),
        payment=payment_from_json(row["payment"]),
        responsible=responsible_from(row["responsible"]),
        confidence=row["confidence"],
        duration_ms=row["duration_ms"],
        read_aloud=row["read_aloud"],
        attachments_match=None if row["attachments_match"] is None else bool(row["attachments_match"]),
        ai_pending=bool(row["ai_pending"]),
    )


def insert_analysis(connection: sqlite3.Connection, message_id: int, analysis: Analysis) -> None:
    """Stores the first analysis of a new message."""
    connection.execute(
        f"INSERT INTO analyses (message_id, {ANALYSIS_COLUMNS}, analysed_at)"  # noqa: S608
        f" VALUES (?, {ANALYSIS_PLACEHOLDERS}, ?)",
        (message_id, *analysis_values(analysis), now_text()),
    )
