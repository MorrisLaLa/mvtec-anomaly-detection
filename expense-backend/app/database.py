import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS devices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('submitter','reviewer','admin')),
    key_hash TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    expires_at TEXT,
    revoked_at TEXT,
    last_seen_at TEXT
);
CREATE TABLE IF NOT EXISTS expenses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id INTEGER NOT NULL REFERENCES devices(id),
    client_id TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    review_status TEXT NOT NULL DEFAULT 'held',
    reviewed_by TEXT,
    reviewed_at TEXT,
    review_reasons TEXT,
    review_comment TEXT,
    review_rule_version TEXT,
    sampled INTEGER NOT NULL DEFAULT 0,
    created_day TEXT NOT NULL DEFAULT '',
    upload_bytes INTEGER NOT NULL DEFAULT 0,
    case_no TEXT,
    owner_case_no TEXT,
    doc_type TEXT,
    employee TEXT,
    amount INTEGER NOT NULL,
    expense_date TEXT,
    store TEXT,
    seller_tax_id TEXT,
    buyer_tax_id TEXT,
    invoice_no TEXT,
    invoice_date TEXT,
    category TEXT,
    category_source TEXT,
    category_confidence REAL,
    recognition_source TEXT,
    recognition_confidence TEXT,
    recognition_note TEXT,
    needs_review INTEGER NOT NULL DEFAULT 0,
    receipt_path TEXT,
    signature_path TEXT,
    voucher_path TEXT,
    note TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (device_id, client_id)
);
CREATE TABLE IF NOT EXISTS review_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    expense_id INTEGER NOT NULL,
    event TEXT NOT NULL,
    actor TEXT NOT NULL,
    actor_device_id INTEGER,
    from_status TEXT,
    to_status TEXT,
    comment TEXT,
    rule_version TEXT,
    self_review INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS review_events_no_update BEFORE UPDATE ON review_events
BEGIN SELECT RAISE(ABORT, 'review_events is append-only'); END;
CREATE TRIGGER IF NOT EXISTS review_events_no_delete BEFORE DELETE ON review_events
BEGIN SELECT RAISE(ABORT, 'review_events is append-only'); END;
CREATE TABLE IF NOT EXISTS vendors (
    tax_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    default_category TEXT
);
CREATE TABLE IF NOT EXISTS classify_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    keyword TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0.9
);
CREATE TABLE IF NOT EXISTS classify_corrections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    store TEXT NOT NULL,
    from_category TEXT,
    to_category TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: str, default_rules: list | None = None) -> None:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = connect(db_path)
    try:
        conn.executescript(SCHEMA)
        for r in default_rules or []:
            conn.execute(
                "INSERT OR IGNORE INTO classify_rules(keyword,category,confidence) VALUES(?,?,?)",
                (r["keyword"], r["category"], r.get("confidence", 0.9)),
            )
        conn.commit()
    finally:
        conn.close()


def default_connection() -> sqlite3.Connection:
    from .config import load_settings

    return connect(load_settings().db_path)
