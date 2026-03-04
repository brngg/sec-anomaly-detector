"""Database utilities: backend-aware connection helper and CRUD helpers."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any, Generator, Iterable, Iterator, Mapping, Optional

DB_PATH = Path(__file__).resolve().parents[2] / "data" / "sec_anomaly.db"

BACKEND_SQLITE = "sqlite"
BACKEND_POSTGRES = "postgres"


class DBRow(Mapping[str, Any]):
    """Row wrapper that supports both mapping and positional access."""

    def __init__(self, keys: Iterable[str], values: Iterable[Any]) -> None:
        key_list = list(keys)
        value_list = list(values)
        self._keys = key_list
        self._values = tuple(value_list)
        self._data = {key: value for key, value in zip(key_list, value_list)}

    def __getitem__(self, key: str | int) -> Any:
        if isinstance(key, int):
            return self._values[key]
        return self._data[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._keys)

    def __len__(self) -> int:
        return len(self._keys)


class DBCursor:
    """Cursor wrapper that normalizes rows across sqlite/psycopg."""

    def __init__(self, cursor: Any) -> None:
        self._cursor = cursor

    @property
    def rowcount(self) -> int:
        value = getattr(self._cursor, "rowcount", -1)
        return int(value if value is not None else -1)

    @property
    def lastrowid(self) -> int | None:
        value = getattr(self._cursor, "lastrowid", None)
        return int(value) if isinstance(value, int) else value

    def fetchone(self) -> DBRow | None:
        row = self._cursor.fetchone()
        return _normalize_row(self._cursor, row)

    def fetchall(self) -> list[DBRow]:
        rows = self._cursor.fetchall()
        return [_normalize_row(self._cursor, row) for row in rows if row is not None]

    def close(self) -> None:
        self._cursor.close()


class DBConnection:
    """Connection wrapper with dialect-safe execute and change tracking."""

    def __init__(self, conn: Any, backend: str) -> None:
        self._conn = conn
        self.backend = backend
        self._total_changes = 0

    @property
    def total_changes(self) -> int:
        if self.backend == BACKEND_SQLITE:
            return int(getattr(self._conn, "total_changes", 0))
        return self._total_changes

    def cursor(self) -> DBCursor:
        return DBCursor(self._conn.cursor())

    def execute(self, sql: str, params: Iterable[Any] | Mapping[str, Any] | None = None) -> DBCursor:
        rendered_sql, rendered_params = _render_sql(sql, params, backend=self.backend)
        cursor = self._conn.cursor()
        if rendered_params is None:
            cursor.execute(rendered_sql)
        else:
            cursor.execute(rendered_sql, rendered_params)
        self._record_changes(rendered_sql, cursor)
        return DBCursor(cursor)

    def executemany(self, sql: str, param_seq: Iterable[Iterable[Any]]) -> DBCursor:
        rendered_sql, _ = _render_sql(sql, (), backend=self.backend)
        cursor = self._conn.cursor()
        cursor.executemany(rendered_sql, list(param_seq))
        self._record_changes(rendered_sql, cursor)
        return DBCursor(cursor)

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    def close(self) -> None:
        self._conn.close()

    def _record_changes(self, sql: str, cursor: Any) -> None:
        if self.backend != BACKEND_POSTGRES:
            return
        statement = sql.strip().split(None, 1)[0].upper() if sql.strip() else ""
        if statement in {"INSERT", "UPDATE", "DELETE"}:
            changed = getattr(cursor, "rowcount", 0) or 0
            if changed > 0:
                self._total_changes += int(changed)


class PostgresConnectionError(RuntimeError):
    """Raised when Postgres backend is requested without valid config/deps."""


def _normalize_backend(value: str | None) -> str:
    normalized = (value or "").strip().lower()
    if normalized in {BACKEND_SQLITE, BACKEND_POSTGRES}:
        return normalized
    if not normalized:
        return BACKEND_POSTGRES
    raise ValueError(f"Unsupported DB_BACKEND '{value}'. Use 'postgres' or 'sqlite'.")


def get_backend(conn: Any) -> str:
    backend = getattr(conn, "backend", None)
    if backend:
        return str(backend)
    if isinstance(conn, sqlite3.Connection):
        return BACKEND_SQLITE
    return BACKEND_POSTGRES


def _database_url_from_env(read_only: bool) -> str | None:
    if read_only:
        return (
            os.getenv("API_DATABASE_URL")
            or os.getenv("DATABASE_URL_RO")
            or os.getenv("DATABASE_URL")
            or os.getenv("DATABASE_URL_RW")
        )
    return os.getenv("DATABASE_URL") or os.getenv("DATABASE_URL_RW")


def _resolve_backend(path: Path | str | None, backend: str | None) -> str:
    if backend is not None:
        return _normalize_backend(backend)
    if path is not None:
        return BACKEND_SQLITE
    return _normalize_backend(os.getenv("DB_BACKEND", BACKEND_POSTGRES))


def _enable_foreign_keys(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA foreign_keys = ON;")


def _open_postgres_connection(dsn: str) -> Any:
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as exc:  # pragma: no cover - depends on runtime install
        raise PostgresConnectionError(
            "Postgres backend requested but psycopg is not installed. "
            "Install dependency 'psycopg[binary]'."
        ) from exc

    return psycopg.connect(dsn, row_factory=dict_row)


def _render_sql(
    sql: str,
    params: Iterable[Any] | Mapping[str, Any] | None,
    backend: str,
) -> tuple[str, Iterable[Any] | Mapping[str, Any] | None]:
    if params is None:
        normalized_params: Iterable[Any] | Mapping[str, Any] | None = None
    elif isinstance(params, Mapping):
        normalized_params = params
    else:
        normalized_params = tuple(params)

    if backend == BACKEND_POSTGRES and not isinstance(normalized_params, Mapping):
        return sql.replace("?", "%s"), normalized_params
    return sql, normalized_params


def _normalize_row(cursor: Any, row: Any) -> DBRow | None:
    if row is None:
        return None
    if isinstance(row, DBRow):
        return row
    if isinstance(row, sqlite3.Row):
        keys = row.keys()
        values = [row[key] for key in keys]
        return DBRow(keys, values)
    if isinstance(row, Mapping):
        keys = list(row.keys())
        values = [row[key] for key in keys]
        return DBRow(keys, values)

    description = getattr(cursor, "description", None) or []
    keys = [column[0] for column in description] if description else [str(i) for i in range(len(row))]
    return DBRow(keys, list(row))


def _to_json_text(value: Mapping[str, Any] | str | None) -> str:
    """Serialize dict-like values to deterministic JSON, passthrough for strings."""
    if value is None:
        return "{}"
    if isinstance(value, str):
        return value
    return json.dumps(value, sort_keys=True, default=_json_default)


def _json_default(value: Any) -> str:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


@contextmanager
def get_conn(
    path: Path | str | None = None,
    *,
    backend: str | None = None,
    dsn: str | None = None,
    read_only: bool = False,
) -> Generator[DBConnection, None, None]:
    """Yield a database connection based on env/config.

    Resolution order:
    - If ``path`` is provided, sqlite is used against that file.
    - Otherwise backend is read from ``backend`` arg or ``DB_BACKEND`` env.
    - For Postgres, DSN resolves from ``dsn`` arg or DATABASE_URL* env vars.

    Commits on success and rolls back on error.
    """

    resolved_backend = _resolve_backend(path=path, backend=backend)

    if resolved_backend == BACKEND_SQLITE:
        sqlite_path = Path(path) if path is not None else DB_PATH
        sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        raw_conn = sqlite3.connect(sqlite_path)
        raw_conn.row_factory = sqlite3.Row
        _enable_foreign_keys(raw_conn)
        conn = DBConnection(raw_conn, backend=BACKEND_SQLITE)
    else:
        resolved_dsn = dsn or _database_url_from_env(read_only=read_only)
        if not resolved_dsn:
            raise PostgresConnectionError(
                "Postgres backend requires DATABASE_URL or DATABASE_URL_RW (or API_DATABASE_URL for read-only)."
            )
        raw_conn = _open_postgres_connection(resolved_dsn)
        conn = DBConnection(raw_conn, backend=BACKEND_POSTGRES)

    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def row_was_affected(cursor: Any) -> bool:
    rowcount = getattr(cursor, "rowcount", 0) or 0
    return int(rowcount) > 0


def _advisory_lock_id(name: str) -> int:
    digest = hashlib.sha256(name.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=False) & 0x7FFFFFFFFFFFFFFF


def try_advisory_lock(conn: DBConnection, lock_name: str) -> bool:
    """Acquire a session-level advisory lock when using Postgres."""
    if get_backend(conn) != BACKEND_POSTGRES:
        return True

    row = conn.execute(
        "SELECT pg_try_advisory_lock(?) AS locked",
        (_advisory_lock_id(lock_name),),
    ).fetchone()
    return bool(row and row["locked"])


def release_advisory_lock(conn: DBConnection, lock_name: str) -> bool:
    """Release a session-level advisory lock when using Postgres."""
    if get_backend(conn) != BACKEND_POSTGRES:
        return True

    row = conn.execute(
        "SELECT pg_advisory_unlock(?) AS unlocked",
        (_advisory_lock_id(lock_name),),
    ).fetchone()
    return bool(row and row["unlocked"])


def upsert_company(
    conn: DBConnection,
    cik: int,
    name: Optional[str] = None,
    ticker: Optional[str] = None,
    industry: Optional[str] = None,
) -> None:
    """Insert or update a company row by CIK."""
    conn.execute(
        """
        INSERT INTO companies (cik, name, ticker, industry, updated_at)
        VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(cik) DO UPDATE SET
            name = excluded.name,
            ticker = excluded.ticker,
            industry = excluded.industry,
            updated_at = CURRENT_TIMESTAMP
        """,
        (cik, name, ticker, industry),
    )


def insert_filing(
    conn: DBConnection,
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
        INSERT INTO filing_events (
            accession_id,
            cik,
            filing_type,
            filed_at,
            filed_date,
            primary_document
        )
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(accession_id) DO NOTHING
        """,
        (accession_id, cik, filing_type, filed_at, filed_date, primary_document),
    )


def update_watermark(
    conn: DBConnection,
    cik: int,
    last_seen_filed_at: Optional[str] = None,
    last_run_at: Optional[str] = None,
    last_run_status: Optional[str] = None,
    last_error: Optional[str] = None,
) -> None:
    """Insert or update a watermark row for the given CIK."""
    conn.execute(
        """
        INSERT INTO watermarks (
            cik,
            last_seen_filed_at,
            updated_at,
            last_run_at,
            last_run_status,
            last_error
        )
        VALUES (?, ?, CURRENT_TIMESTAMP, ?, ?, ?)
        ON CONFLICT(cik) DO UPDATE SET
            last_seen_filed_at = excluded.last_seen_filed_at,
            updated_at = CURRENT_TIMESTAMP,
            last_run_at = excluded.last_run_at,
            last_run_status = excluded.last_run_status,
            last_error = excluded.last_error
        """,
        (cik, last_seen_filed_at, last_run_at, last_run_status, last_error),
    )


def foreign_key_check(conn: DBConnection) -> list[DBRow]:
    """Return FK check rows (sqlite only; postgres returns empty list)."""
    if get_backend(conn) != BACKEND_SQLITE:
        return []
    return conn.execute("PRAGMA foreign_key_check;").fetchall()


def upsert_feature_snapshot(
    conn: DBConnection,
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
        VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        ON CONFLICT(cik, as_of_date, lookback_days) DO UPDATE SET
            features = excluded.features,
            source_alert_count = excluded.source_alert_count,
            updated_at = CURRENT_TIMESTAMP
        """,
        (cik, as_of_date, lookback_days, features_json, source_alert_count),
    )


def upsert_issuer_risk_score(
    conn: DBConnection,
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
        VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        ON CONFLICT(cik, as_of_date, model_version) DO UPDATE SET
            risk_score = excluded.risk_score,
            risk_rank = excluded.risk_rank,
            percentile = excluded.percentile,
            evidence = excluded.evidence,
            updated_at = CURRENT_TIMESTAMP
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
    conn: DBConnection,
    cik: int,
    event_date: str,
    outcome_type: str,
    source: Optional[str] = None,
    description: Optional[str] = None,
    form: Optional[str] = None,
    item: Optional[str] = None,
    accession_id: Optional[str] = None,
    filing_url: Optional[str] = None,
    verification_status: Optional[str] = None,
    verification_reason: Optional[str] = None,
    metadata: Mapping[str, Any] | str | None = None,
    dedupe_key: Optional[str] = None,
) -> bool:
    """Insert an outcome event with dedupe protection. Returns True if inserted."""
    metadata_map: dict[str, Any]
    if metadata is None:
        metadata_map = {}
    elif isinstance(metadata, str):
        try:
            parsed = json.loads(metadata)
            metadata_map = parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            metadata_map = {}
    else:
        metadata_map = dict(metadata)

    if form is None:
        form = metadata_map.get("form")
    if item is None:
        item = metadata_map.get("item")
    if accession_id is None:
        accession_id = metadata_map.get("accession_id")
    if filing_url is None:
        filing_url = metadata_map.get("url") or metadata_map.get("filing_url")
    if verification_status is None:
        verification_status = metadata_map.get("verification_status")
    if verification_reason is None:
        verification_reason = metadata_map.get("verification_reason")

    metadata_json = _to_json_text(metadata_map)
    if dedupe_key is None:
        dedupe_key = f"{outcome_type}:{cik}:{event_date}"

    cursor = conn.execute(
        """
        INSERT INTO outcome_events (
            cik,
            event_date,
            outcome_type,
            source,
            description,
            form,
            item,
            accession_id,
            filing_url,
            verification_status,
            verification_reason,
            metadata,
            dedupe_key
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(dedupe_key) DO NOTHING
        """,
        (
            cik,
            event_date,
            outcome_type,
            source,
            description,
            form,
            item,
            accession_id,
            filing_url,
            verification_status,
            verification_reason,
            metadata_json,
            dedupe_key,
        ),
    )
    return row_was_affected(cursor)
