"""Database utilities: connection helper and CRUD helpers.

Renamed from `client.py` to `db_utils.py` for clarity.
"""

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator, Optional

DB_PATH = Path(__file__).resolve().parents[2] / "data" / "sec_anomaly.db"


def _enable_foreign_keys(conn: sqlite3.Connection) -> None:
    """Enable foreign key enforcement for the given SQLite connection."""
    conn.execute("PRAGMA foreign_keys = ON;")


@contextmanager
def get_conn(path: Path = DB_PATH) -> Generator[sqlite3.Connection, None, None]:
    """Context manager that yields a sqlite3.Connection with foreign keys enabled.

    Commits on success and rolls back on error.
    """
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    _enable_foreign_keys(conn)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def upsert_company(conn: sqlite3.Connection, cik: int, name: Optional[str] = None, ticker: Optional[str] = None, industry: Optional[str] = None) -> None:
    """Insert or update a company row by CIK."""
    conn.execute(
        """
        INSERT INTO companies (cik, name, ticker, industry, updated_at)
        VALUES (?, ?, ?, ?, datetime('now'))
        ON CONFLICT(cik) DO UPDATE SET
            name=excluded.name,
            ticker=excluded.ticker,
            industry=excluded.industry,
            updated_at=datetime('now')
        """,
        (cik, name, ticker, industry),
    )


def insert_filing(conn: sqlite3.Connection, accession_id: str, cik: int, filing_type: str, filed_at: str, filing_date: Optional[str] = None, primary_document: Optional[str] = None, size_bytes: Optional[int] = None) -> None:
    """Insert a filing if it does not already exist (dedupe on accession_id)."""
    conn.execute(
        """
        INSERT OR IGNORE INTO filing_events (accession_id, cik, filing_type, filed_at, filing_date, primary_document, size_bytes)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (accession_id, cik, filing_type, filed_at, filing_date, primary_document, size_bytes),
    )


def update_watermark(conn: sqlite3.Connection, cik: int, last_seen_filed_at: Optional[str] = None, last_run_at: Optional[str] = None, last_run_status: Optional[str] = None, last_error: Optional[str] = None) -> None:
    """Insert or update a watermark row for the given CIK."""
    conn.execute(
        """
        INSERT INTO watermarks (cik, last_seen_filed_at, updated_at, last_run_at, last_run_status, last_error)
        VALUES (?, ?, datetime('now'), ?, ?, ?)
        ON CONFLICT(cik) DO UPDATE SET
            last_seen_filed_at = excluded.last_seen_filed_at,
            updated_at = datetime('now'),
            last_run_at = excluded.last_run_at,
            last_run_status = excluded.last_run_status,
            last_error = excluded.last_error
        """,
        (cik, last_seen_filed_at, last_run_at, last_run_status, last_error),
    )


def foreign_key_check(conn: sqlite3.Connection):
    """Return the result of PRAGMA foreign_key_check (empty list = no violations)."""
    return conn.execute("PRAGMA foreign_key_check;").fetchall()
