import os
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parents[2] / "data" / "sec_anomaly.db"

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS companies (
    cik INTEGER PRIMARY KEY,
    name TEXT,
    ticker TEXT,
    industry TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS filing_events (
    accession_id TEXT PRIMARY KEY,
    cik INTEGER NOT NULL,
    filing_type TEXT NOT NULL,
    filed_at TEXT NOT NULL,
    filed_date TEXT NOT NULL,
    primary_document TEXT,
    FOREIGN KEY(cik) REFERENCES companies(cik) ON DELETE CASCADE
);

-- Core query index for baselines/windows
CREATE INDEX IF NOT EXISTS idx_filing_events_cik_type_filed_at
    ON filing_events (cik, filing_type, filed_at);

CREATE INDEX IF NOT EXISTS idx_filing_events_filed_at
    ON filing_events (filed_at);

CREATE TABLE IF NOT EXISTS watermarks (
    cik INTEGER PRIMARY KEY,
    last_seen_filed_at TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    last_run_at TEXT,
    last_run_status TEXT,
    last_error TEXT,
    FOREIGN KEY(cik) REFERENCES companies(cik) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS alerts (
    alert_id INTEGER PRIMARY KEY,
    accession_id TEXT NOT NULL,
    anomaly_type TEXT NOT NULL,
    severity_score REAL NOT NULL,
    description TEXT NOT NULL,
    details TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'OPEN',
    dedupe_key TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY(accession_id) REFERENCES filing_events(accession_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_alerts_created_at
    ON alerts (created_at);

CREATE INDEX IF NOT EXISTS idx_alerts_status
    ON alerts (status);

CREATE TABLE IF NOT EXISTS poll_state (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""

def create_db(path: Path = DB_PATH, reset: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    # Only wipe the DB when explicitly requested
    if reset and path.exists():
        path.unlink()

    conn = sqlite3.connect(path)
    # Ensure foreign key enforcement
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.executescript(CREATE_SQL)
    conn.commit()
    conn.close()
    action = "Recreated" if reset else "Ensured schema for"
    print(f"{action} DB at {path}")

if __name__ == "__main__":
    reset = os.getenv("RESET_DB", "").strip().lower() in {"1", "true", "yes"}
    create_db(reset=reset)
