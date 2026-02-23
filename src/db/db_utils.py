"""Database utilities: connection helper and CRUD helpers."""

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator, Mapping, Optional

DB_PATH = Path(__file__).resolve().parents[2] / "data" / "sec_anomaly.db"


def _enable_foreign_keys(conn: sqlite3.Connection) -> None:
    """Enable foreign key enforcement for the given SQLite connection."""
    conn.execute("PRAGMA foreign_keys = ON;")


def _to_json_text(value: Mapping[str, Any] | str | None) -> str:
    """Serialize dict-like values to deterministic JSON, passthrough for strings."""
    if value is None:
        return "{}"
    if isinstance(value, str):
        return value
    return json.dumps(value, sort_keys=True)


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


def upsert_company(
    conn: sqlite3.Connection,
    cik: int,
    name: Optional[str] = None,
    ticker: Optional[str] = None,
    industry: Optional[str] = None,
) -> None:
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


def insert_filing(
    conn: sqlite3.Connection,
    accession_id: str,
    cik: int,
    filing_type: str,
    filed_at: str,
    filed_date: str,
    primary_document: Optional[str] = None,
) -> None:
    """Insert a filing if it does not already exist (dedupe on accession_id)."""
    conn.execute(
        """
        INSERT OR IGNORE INTO filing_events (accession_id, cik, filing_type, filed_at, filed_date, primary_document)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (accession_id, cik, filing_type, filed_at, filed_date, primary_document),
    )


def update_watermark(
    conn: sqlite3.Connection,
    cik: int,
    last_seen_filed_at: Optional[str] = None,
    last_run_at: Optional[str] = None,
    last_run_status: Optional[str] = None,
    last_error: Optional[str] = None,
) -> None:
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


def upsert_feature_snapshot(
    conn: sqlite3.Connection,
    cik: int,
    as_of_date: str,
    lookback_days: int,
    features: Mapping[str, Any] | str,
    source_alert_count: int = 0,
) -> None:
    """Insert or update a feature snapshot row for an issuer and lookback window."""
    features_json = _to_json_text(features)
    conn.execute(
        """
        INSERT INTO feature_snapshots (
            cik,
            as_of_date,
            lookback_days,
            features,
            source_alert_count,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, datetime('now'), datetime('now'))
        ON CONFLICT(cik, as_of_date, lookback_days) DO UPDATE SET
            features = excluded.features,
            source_alert_count = excluded.source_alert_count,
            updated_at = datetime('now')
        """,
        (cik, as_of_date, lookback_days, features_json, source_alert_count),
    )


def upsert_issuer_risk_score(
    conn: sqlite3.Connection,
    cik: int,
    as_of_date: str,
    risk_score: float,
    evidence: Mapping[str, Any] | str,
    model_version: str = "v1",
    risk_rank: Optional[int] = None,
    percentile: Optional[float] = None,
) -> None:
    """Insert or update an issuer risk score for a model version and date."""
    evidence_json = _to_json_text(evidence)
    conn.execute(
        """
        INSERT INTO issuer_risk_scores (
            cik,
            as_of_date,
            model_version,
            risk_score,
            risk_rank,
            percentile,
            evidence,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
        ON CONFLICT(cik, as_of_date, model_version) DO UPDATE SET
            risk_score = excluded.risk_score,
            risk_rank = excluded.risk_rank,
            percentile = excluded.percentile,
            evidence = excluded.evidence,
            updated_at = datetime('now')
        """,
        (
            cik,
            as_of_date,
            model_version,
            risk_score,
            risk_rank,
            percentile,
            evidence_json,
        ),
    )


def insert_outcome_event(
    conn: sqlite3.Connection,
    cik: int,
    event_date: str,
    outcome_type: str,
    source: Optional[str] = None,
    description: Optional[str] = None,
    metadata: Mapping[str, Any] | str | None = None,
    dedupe_key: Optional[str] = None,
) -> bool:
    """Insert an outcome event with dedupe protection. Returns True if inserted."""
    metadata_json = _to_json_text(metadata)
    if dedupe_key is None:
        dedupe_key = f"{outcome_type}:{cik}:{event_date}"

    changes_before = conn.total_changes
    conn.execute(
        """
        INSERT OR IGNORE INTO outcome_events (
            cik,
            event_date,
            outcome_type,
            source,
            description,
            metadata,
            dedupe_key
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (cik, event_date, outcome_type, source, description, metadata_json, dedupe_key),
    )
    return conn.total_changes > changes_before
