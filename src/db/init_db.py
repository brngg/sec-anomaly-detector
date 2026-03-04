"""Schema bootstrap and lightweight migrations for sqlite/Postgres backends."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    repo_root = Path(__file__).resolve().parents[2]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - dotenv import is optional for bootstrap
    load_dotenv = None

from src.db import db_utils

DB_PATH = Path(__file__).resolve().parents[2] / "data" / "sec_anomaly.db"
REPO_ROOT = Path(__file__).resolve().parents[2]

CREATE_SQL_SQLITE = """
CREATE TABLE IF NOT EXISTS companies (
    cik INTEGER PRIMARY KEY,
    name TEXT,
    ticker TEXT,
    industry TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS filing_events (
    accession_id TEXT PRIMARY KEY,
    cik INTEGER NOT NULL,
    filing_type TEXT NOT NULL,
    filed_at TEXT NOT NULL,
    filed_date DATE NOT NULL,
    primary_document TEXT,
    FOREIGN KEY(cik) REFERENCES companies(cik) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_filing_events_cik_type_filed_at
    ON filing_events (cik, filing_type, filed_at);

CREATE INDEX IF NOT EXISTS idx_filing_events_filed_at
    ON filing_events (filed_at);

CREATE TABLE IF NOT EXISTS watermarks (
    cik INTEGER PRIMARY KEY,
    last_seen_filed_at TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
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
    event_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(accession_id) REFERENCES filing_events(accession_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_alerts_event_at
    ON alerts (event_at);

CREATE INDEX IF NOT EXISTS idx_alerts_created_at
    ON alerts (created_at);

CREATE INDEX IF NOT EXISTS idx_alerts_status
    ON alerts (status);

CREATE TABLE IF NOT EXISTS feature_snapshots (
    snapshot_id INTEGER PRIMARY KEY,
    cik INTEGER NOT NULL,
    as_of_date DATE NOT NULL,
    lookback_days INTEGER NOT NULL,
    features TEXT NOT NULL,
    source_alert_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (cik, as_of_date, lookback_days),
    FOREIGN KEY(cik) REFERENCES companies(cik) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_feature_snapshots_cik_as_of
    ON feature_snapshots (cik, as_of_date);

CREATE INDEX IF NOT EXISTS idx_feature_snapshots_as_of
    ON feature_snapshots (as_of_date);

CREATE TABLE IF NOT EXISTS issuer_risk_scores (
    score_id INTEGER PRIMARY KEY,
    cik INTEGER NOT NULL,
    as_of_date DATE NOT NULL,
    model_version TEXT NOT NULL DEFAULT 'v1',
    risk_score REAL NOT NULL CHECK (risk_score >= 0.0 AND risk_score <= 1.0),
    risk_rank INTEGER,
    percentile REAL CHECK (percentile IS NULL OR (percentile >= 0.0 AND percentile <= 1.0)),
    evidence TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (cik, as_of_date, model_version),
    FOREIGN KEY(cik) REFERENCES companies(cik) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_issuer_risk_scores_as_of_score
    ON issuer_risk_scores (as_of_date, risk_score DESC);

CREATE INDEX IF NOT EXISTS idx_issuer_risk_scores_cik_as_of
    ON issuer_risk_scores (cik, as_of_date);

CREATE TABLE IF NOT EXISTS outcome_events (
    outcome_id INTEGER PRIMARY KEY,
    cik INTEGER NOT NULL,
    event_date DATE NOT NULL,
    outcome_type TEXT NOT NULL,
    source TEXT,
    description TEXT,
    form TEXT,
    item TEXT,
    accession_id TEXT,
    filing_url TEXT,
    verification_status TEXT,
    verification_reason TEXT,
    metadata TEXT NOT NULL DEFAULT '{}',
    dedupe_key TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(cik) REFERENCES companies(cik) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_outcome_events_cik_date
    ON outcome_events (cik, event_date);

CREATE INDEX IF NOT EXISTS idx_outcome_events_type_date
    ON outcome_events (outcome_type, event_date);

CREATE TABLE IF NOT EXISTS poll_state (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""

CREATE_SQL_POSTGRES = """
CREATE TABLE IF NOT EXISTS companies (
    cik BIGINT PRIMARY KEY,
    name TEXT,
    ticker TEXT,
    industry TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS filing_events (
    accession_id TEXT PRIMARY KEY,
    cik BIGINT NOT NULL REFERENCES companies(cik) ON DELETE CASCADE,
    filing_type TEXT NOT NULL,
    filed_at TIMESTAMPTZ NOT NULL,
    filed_date DATE NOT NULL,
    primary_document TEXT
);

CREATE INDEX IF NOT EXISTS idx_filing_events_cik_type_filed_at
    ON filing_events (cik, filing_type, filed_at);

CREATE INDEX IF NOT EXISTS idx_filing_events_filed_at
    ON filing_events (filed_at);

CREATE TABLE IF NOT EXISTS watermarks (
    cik BIGINT PRIMARY KEY REFERENCES companies(cik) ON DELETE CASCADE,
    last_seen_filed_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_run_at TIMESTAMPTZ,
    last_run_status TEXT,
    last_error TEXT
);

CREATE TABLE IF NOT EXISTS alerts (
    alert_id BIGSERIAL PRIMARY KEY,
    accession_id TEXT NOT NULL REFERENCES filing_events(accession_id) ON DELETE CASCADE,
    anomaly_type TEXT NOT NULL,
    severity_score DOUBLE PRECISION NOT NULL,
    description TEXT NOT NULL,
    details JSONB NOT NULL,
    status TEXT NOT NULL DEFAULT 'OPEN',
    dedupe_key TEXT NOT NULL UNIQUE,
    event_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_alerts_event_at
    ON alerts (event_at);

CREATE INDEX IF NOT EXISTS idx_alerts_created_at
    ON alerts (created_at);

CREATE INDEX IF NOT EXISTS idx_alerts_status
    ON alerts (status);

CREATE TABLE IF NOT EXISTS feature_snapshots (
    snapshot_id BIGSERIAL PRIMARY KEY,
    cik BIGINT NOT NULL REFERENCES companies(cik) ON DELETE CASCADE,
    as_of_date DATE NOT NULL,
    lookback_days INTEGER NOT NULL,
    features JSONB NOT NULL,
    source_alert_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (cik, as_of_date, lookback_days)
);

CREATE INDEX IF NOT EXISTS idx_feature_snapshots_cik_as_of
    ON feature_snapshots (cik, as_of_date);

CREATE INDEX IF NOT EXISTS idx_feature_snapshots_as_of
    ON feature_snapshots (as_of_date);

CREATE TABLE IF NOT EXISTS issuer_risk_scores (
    score_id BIGSERIAL PRIMARY KEY,
    cik BIGINT NOT NULL REFERENCES companies(cik) ON DELETE CASCADE,
    as_of_date DATE NOT NULL,
    model_version TEXT NOT NULL DEFAULT 'v1',
    risk_score DOUBLE PRECISION NOT NULL CHECK (risk_score >= 0.0 AND risk_score <= 1.0),
    risk_rank INTEGER,
    percentile DOUBLE PRECISION CHECK (percentile IS NULL OR (percentile >= 0.0 AND percentile <= 1.0)),
    evidence JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (cik, as_of_date, model_version)
);

CREATE INDEX IF NOT EXISTS idx_issuer_risk_scores_as_of_score
    ON issuer_risk_scores (as_of_date, risk_score DESC);

CREATE INDEX IF NOT EXISTS idx_issuer_risk_scores_cik_as_of
    ON issuer_risk_scores (cik, as_of_date);

CREATE TABLE IF NOT EXISTS outcome_events (
    outcome_id BIGSERIAL PRIMARY KEY,
    cik BIGINT NOT NULL REFERENCES companies(cik) ON DELETE CASCADE,
    event_date DATE NOT NULL,
    outcome_type TEXT NOT NULL,
    source TEXT,
    description TEXT,
    form TEXT,
    item TEXT,
    accession_id TEXT,
    filing_url TEXT,
    verification_status TEXT,
    verification_reason TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    dedupe_key TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_outcome_events_cik_date
    ON outcome_events (cik, event_date);

CREATE INDEX IF NOT EXISTS idx_outcome_events_type_date
    ON outcome_events (outcome_type, event_date);

CREATE TABLE IF NOT EXISTS poll_state (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


def _normalize_backend(value: str | None) -> str:
    normalized = (value or "").strip().lower()
    if normalized in {db_utils.BACKEND_SQLITE, db_utils.BACKEND_POSTGRES}:
        return normalized
    if not normalized:
        return db_utils.BACKEND_POSTGRES
    raise ValueError(f"Unsupported backend '{value}'.")


def _resolve_backend(path: Path | None, backend: str | None) -> str:
    if backend is not None:
        return _normalize_backend(backend)
    if path is not None:
        return db_utils.BACKEND_SQLITE
    return _normalize_backend(os.getenv("DB_BACKEND", db_utils.BACKEND_POSTGRES))


def _execute_script(conn: db_utils.DBConnection, script: str) -> None:
    for statement in script.split(";"):
        sql = statement.strip()
        if not sql:
            continue
        conn.execute(sql)


def _table_columns_sqlite(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {str(row[1]) for row in rows}


def _table_columns_postgres(conn: db_utils.DBConnection, table: str) -> set[str]:
    rows = conn.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = ?
        """,
        (table,),
    ).fetchall()
    return {str(row["column_name"]) for row in rows}


def _migrate_alert_event_at_sqlite(conn: sqlite3.Connection) -> None:
    columns = _table_columns_sqlite(conn, "alerts")
    if not columns:
        return

    if "event_at" not in columns:
        conn.execute("ALTER TABLE alerts ADD COLUMN event_at TEXT")

    conn.execute(
        """
        UPDATE alerts
        SET event_at = (
            SELECT f.filed_at
            FROM filing_events f
            WHERE f.accession_id = alerts.accession_id
        )
        WHERE event_at IS NULL
        """
    )
    conn.execute(
        """
        UPDATE alerts
        SET event_at = COALESCE(event_at, created_at, CURRENT_TIMESTAMP)
        WHERE event_at IS NULL
        """
    )


def _migrate_alert_event_at_postgres(conn: db_utils.DBConnection) -> None:
    columns = _table_columns_postgres(conn, "alerts")
    if not columns:
        return

    if "event_at" not in columns:
        conn.execute("ALTER TABLE alerts ADD COLUMN event_at TIMESTAMPTZ")

    conn.execute(
        """
        UPDATE alerts a
        SET event_at = f.filed_at
        FROM filing_events f
        WHERE a.event_at IS NULL
          AND f.accession_id = a.accession_id
        """
    )
    conn.execute(
        """
        UPDATE alerts
        SET event_at = COALESCE(event_at, created_at, CURRENT_TIMESTAMP)
        WHERE event_at IS NULL
        """
    )
    conn.execute("ALTER TABLE alerts ALTER COLUMN event_at SET NOT NULL")


def _migrate_outcome_events_sqlite(conn: sqlite3.Connection) -> None:
    existing = _table_columns_sqlite(conn, "outcome_events")
    if not existing:
        return

    desired: dict[str, str] = {
        "form": "TEXT",
        "item": "TEXT",
        "accession_id": "TEXT",
        "filing_url": "TEXT",
        "verification_status": "TEXT",
        "verification_reason": "TEXT",
    }
    for column, column_type in desired.items():
        if column in existing:
            continue
        conn.execute(f"ALTER TABLE outcome_events ADD COLUMN {column} {column_type}")

    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_outcome_events_accession
            ON outcome_events (accession_id)
        """
    )

    rows = conn.execute(
        """
        SELECT
            outcome_id,
            metadata,
            form,
            item,
            accession_id,
            filing_url,
            verification_status,
            verification_reason
        FROM outcome_events
        """
    ).fetchall()
    for row in rows:
        metadata_raw = row[1]
        if not metadata_raw:
            continue
        try:
            parsed = json.loads(metadata_raw)
        except Exception:
            continue
        if not isinstance(parsed, dict):
            continue

        form = row[2] or parsed.get("form")
        item = row[3] or parsed.get("item")
        accession_id = row[4] or parsed.get("accession_id")
        filing_url = row[5] or parsed.get("filing_url") or parsed.get("url")
        verification_status = row[6] or parsed.get("verification_status")
        verification_reason = row[7] or parsed.get("verification_reason")

        conn.execute(
            """
            UPDATE outcome_events
            SET form = ?,
                item = ?,
                accession_id = ?,
                filing_url = ?,
                verification_status = ?,
                verification_reason = ?
            WHERE outcome_id = ?
            """,
            (
                form,
                item,
                accession_id,
                filing_url,
                verification_status,
                verification_reason,
                row[0],
            ),
        )


def _migrate_outcome_events_postgres(conn: db_utils.DBConnection) -> None:
    existing = _table_columns_postgres(conn, "outcome_events")
    if not existing:
        return

    desired: dict[str, str] = {
        "form": "TEXT",
        "item": "TEXT",
        "accession_id": "TEXT",
        "filing_url": "TEXT",
        "verification_status": "TEXT",
        "verification_reason": "TEXT",
    }
    for column, column_type in desired.items():
        if column in existing:
            continue
        conn.execute(f"ALTER TABLE outcome_events ADD COLUMN {column} {column_type}")

    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_outcome_events_accession
            ON outcome_events (accession_id)
        """
    )


def create_db(
    path: Path | None = None,
    reset: bool = False,
    backend: str | None = None,
    dsn: str | None = None,
) -> None:
    """Create or migrate schema for the selected backend."""

    resolved_backend = _resolve_backend(path=path, backend=backend)

    if resolved_backend == db_utils.BACKEND_SQLITE:
        sqlite_path = path or DB_PATH
        sqlite_path.parent.mkdir(parents=True, exist_ok=True)

        if reset and sqlite_path.exists():
            sqlite_path.unlink()

        conn = sqlite3.connect(sqlite_path)
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.executescript(CREATE_SQL_SQLITE)
        _migrate_alert_event_at_sqlite(conn)
        _migrate_outcome_events_sqlite(conn)
        conn.commit()
        conn.close()
        action = "Recreated" if reset else "Ensured schema for"
        print(f"{action} sqlite DB at {sqlite_path}")
        return

    with db_utils.get_conn(path=None, backend=db_utils.BACKEND_POSTGRES, dsn=dsn) as conn:
        if reset:
            conn.execute("DROP SCHEMA IF EXISTS public CASCADE")
            conn.execute("CREATE SCHEMA public")

        _execute_script(conn, CREATE_SQL_POSTGRES)
        _migrate_alert_event_at_postgres(conn)
        _migrate_outcome_events_postgres(conn)

    action = "Recreated" if reset else "Ensured schema for"
    print(f"{action} postgres schema")


if __name__ == "__main__":
    if load_dotenv is not None:
        load_dotenv(REPO_ROOT / ".env", override=False)
    reset = os.getenv("RESET_DB", "").strip().lower() in {"1", "true", "yes"}
    create_db(path=None, reset=reset)
