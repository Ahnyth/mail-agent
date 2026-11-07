"""The database layout: tables, columns added later, and the upgrade of older databases."""

import sqlite3

SCHEMA_VERSION = 5
"""Stored in ``PRAGMA user_version``; raised with every change of the layout. A hand-over package from a
newer version is refused, so an older agent never works on a layout it does not know."""

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER PRIMARY KEY,
    message_key     TEXT NOT NULL UNIQUE,
    origin          TEXT NOT NULL,
    raw             BLOB NOT NULL,
    sender_name     TEXT NOT NULL,
    sender_address  TEXT NOT NULL,
    reply_to        TEXT NOT NULL,
    subject         TEXT NOT NULL,
    received_at     TEXT,
    body            TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'offen',
    imported_at     TEXT NOT NULL,
    answered_at     TEXT,
    contact_form    INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS analyses (
    message_id      INTEGER PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,
    category        TEXT NOT NULL,
    urgency         TEXT NOT NULL,
    summary         TEXT NOT NULL,
    reasons         TEXT NOT NULL,
    tasks           TEXT NOT NULL,
    reply_needed    INTEGER NOT NULL,
    reply_subject   TEXT NOT NULL,
    reply_body      TEXT NOT NULL,
    documents       TEXT NOT NULL,
    warnings        TEXT NOT NULL,
    engine          TEXT NOT NULL,
    draft_edited    INTEGER NOT NULL DEFAULT 0,
    analysed_at     TEXT NOT NULL,
    findings        TEXT NOT NULL DEFAULT '[]',
    deadlines       TEXT NOT NULL DEFAULT '[]',
    payment         TEXT,
    responsible     TEXT,
    confidence      REAL,
    duration_ms     INTEGER,
    read_aloud      TEXT NOT NULL DEFAULT '',
    attachments_match INTEGER,
    ai_pending      INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS corrections (
    id                  INTEGER PRIMARY KEY,
    message_id          INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    original_category   TEXT NOT NULL,
    original_urgency    TEXT NOT NULL,
    original_reply      INTEGER NOT NULL,
    original_responsible TEXT,
    category            TEXT NOT NULL,
    urgency             TEXT NOT NULL,
    reply_needed        INTEGER NOT NULL,
    responsible         TEXT,
    note                TEXT NOT NULL,
    engine              TEXT NOT NULL,
    created_at          TEXT NOT NULL,
    exported_to         TEXT
);
CREATE INDEX IF NOT EXISTS idx_corrections_message ON corrections(message_id);
CREATE TABLE IF NOT EXISTS documents (
    id              INTEGER PRIMARY KEY,
    message_id      INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    filename        TEXT NOT NULL,
    content_type    TEXT NOT NULL,
    size            INTEGER NOT NULL,
    sha256          TEXT NOT NULL,
    document_type   TEXT,
    stored_path     TEXT,
    rejected_reason TEXT,
    extracted_text  TEXT,
    text_note       TEXT
);
CREATE INDEX IF NOT EXISTS idx_documents_message ON documents(message_id);
CREATE TABLE IF NOT EXISTS fetch_state (
    source          TEXT PRIMARY KEY,
    validity        TEXT,
    last_uid        INTEGER NOT NULL DEFAULT 0,
    start_date      TEXT,
    updated_at      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS fetch_runs (
    id              INTEGER PRIMARY KEY,
    kind            TEXT NOT NULL,
    trigger         TEXT NOT NULL,
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    found           INTEGER NOT NULL DEFAULT 0,
    imported        INTEGER NOT NULL DEFAULT 0,
    duplicates      INTEGER NOT NULL DEFAULT 0,
    failed          TEXT NOT NULL DEFAULT '[]',
    ai_pending      INTEGER NOT NULL DEFAULT 0,
    error           TEXT
);
CREATE TABLE IF NOT EXISTS app_state (
    key             TEXT PRIMARY KEY,
    value           TEXT NOT NULL
);
"""
"""All tables; ``IF NOT EXISTS`` makes the script safe to run on every start."""

ADDED_COLUMNS: dict[str, dict[str, str]] = {
    "analyses": {
        "findings": "TEXT NOT NULL DEFAULT '[]'",
        "deadlines": "TEXT NOT NULL DEFAULT '[]'",
        "payment": "TEXT",
        "responsible": "TEXT",
        "confidence": "REAL",
        "duration_ms": "INTEGER",
        "read_aloud": "TEXT NOT NULL DEFAULT ''",
        "attachments_match": "INTEGER",
        "ai_pending": "INTEGER NOT NULL DEFAULT 0",
    },
    "messages": {"answered_at": "TEXT", "contact_form": "INTEGER NOT NULL DEFAULT 0"},
    "documents": {"extracted_text": "TEXT", "text_note": "TEXT"},
}
"""Columns added after schema version 1; older databases get them on start-up."""


def upgrade(connection: sqlite3.Connection) -> None:
    """Creates missing tables and columns and records the schema version.

    Runs on every start and after taking over a hand-over package, which may come from an older version.

    Args:
        connection: An open connection with ``sqlite3.Row`` as row factory.
    """
    # The import runs in a background thread while the web page reads. With WAL, readers never wait for
    # the writer; the setting is stored in the database file itself.
    connection.execute("PRAGMA journal_mode = WAL")
    connection.executescript(SCHEMA)
    for table, columns in ADDED_COLUMNS.items():
        add_missing_columns(connection, table, columns)
    connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def add_missing_columns(connection: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    """Adds the columns of ``columns`` that ``table`` does not have yet.

    Args:
        connection: An open connection.
        table: A table name from :data:`ADDED_COLUMNS` (never user input).
        columns: Column name and SQL definition.
    """
    existing = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
    for column, definition in columns.items():
        if column not in existing:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
