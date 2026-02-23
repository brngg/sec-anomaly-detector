import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.db.db_utils import (
    get_conn,
    insert_outcome_event,
    update_watermark,
    upsert_company,
    upsert_feature_snapshot,
    upsert_issuer_risk_score,
)
from src.db.init_db import create_db


def test_create_db_non_destructive(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"

    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE sentinel (id INTEGER PRIMARY KEY, value TEXT)")
    conn.execute("INSERT INTO sentinel (value) VALUES ('keep-me')")
    conn.commit()
    conn.close()

    create_db(path=db_path, reset=False)

    conn = sqlite3.connect(db_path)
    value = conn.execute("SELECT value FROM sentinel WHERE id = 1").fetchone()[0]
    conn.close()
    assert value == "keep-me"


def test_create_db_reset(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"

    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE sentinel (id INTEGER PRIMARY KEY, value TEXT)")
    conn.execute("INSERT INTO sentinel (value) VALUES ('wipe-me')")
    conn.commit()
    conn.close()

    create_db(path=db_path, reset=True)

    conn = sqlite3.connect(db_path)
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='sentinel'"
    ).fetchall()
    conn.close()
    assert tables == []


def test_update_watermark(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    create_db(path=db_path, reset=False)

    with get_conn(path=db_path) as conn:
        upsert_company(conn, cik=123456, name="Test Co", ticker="TEST", industry=None)
        update_watermark(
            conn,
            cik=123456,
            last_seen_filed_at="2026-02-01T00:00:00",
            last_run_at="2026-02-01T00:00:00",
            last_run_status="SUCCESS",
            last_error=None,
        )

    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT last_run_status, last_seen_filed_at FROM watermarks WHERE cik=123456"
    ).fetchone()
    conn.close()
    assert row == ("SUCCESS", "2026-02-01T00:00:00")


def test_new_scoring_tables_created(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    create_db(path=db_path, reset=False)

    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type='table'
        """
    ).fetchall()
    conn.close()

    table_names = {row[0] for row in rows}
    assert "feature_snapshots" in table_names
    assert "issuer_risk_scores" in table_names
    assert "outcome_events" in table_names


def test_upsert_feature_snapshot_and_risk_score(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    create_db(path=db_path, reset=False)

    with get_conn(path=db_path) as conn:
        upsert_company(conn, cik=999001, name="Risk Co", ticker="RISK", industry="Tech")
        upsert_feature_snapshot(
            conn,
            cik=999001,
            as_of_date="2026-02-23",
            lookback_days=30,
            features={"nt_count_30d": 1, "friday_after_hours_30d": 2},
            source_alert_count=3,
        )
        upsert_feature_snapshot(
            conn,
            cik=999001,
            as_of_date="2026-02-23",
            lookback_days=30,
            features={"nt_count_30d": 2, "friday_after_hours_30d": 3},
            source_alert_count=5,
        )

        upsert_issuer_risk_score(
            conn,
            cik=999001,
            as_of_date="2026-02-23",
            model_version="v1",
            risk_score=0.72,
            risk_rank=8,
            percentile=0.92,
            evidence={"drivers": ["NT_FILING", "FRIDAY_BURYING"]},
        )
        upsert_issuer_risk_score(
            conn,
            cik=999001,
            as_of_date="2026-02-23",
            model_version="v1",
            risk_score=0.81,
            risk_rank=4,
            percentile=0.96,
            evidence={"drivers": ["NT_FILING", "8K_SPIKE"]},
        )

    conn = sqlite3.connect(db_path)
    feature_row = conn.execute(
        """
        SELECT features, source_alert_count
        FROM feature_snapshots
        WHERE cik = 999001 AND as_of_date = '2026-02-23' AND lookback_days = 30
        """
    ).fetchone()
    score_row = conn.execute(
        """
        SELECT risk_score, risk_rank, percentile, evidence
        FROM issuer_risk_scores
        WHERE cik = 999001 AND as_of_date = '2026-02-23' AND model_version = 'v1'
        """
    ).fetchone()
    conn.close()

    assert feature_row is not None
    assert feature_row[1] == 5
    assert json.loads(feature_row[0]) == {
        "friday_after_hours_30d": 3,
        "nt_count_30d": 2,
    }

    assert score_row is not None
    assert score_row[0] == 0.81
    assert score_row[1] == 4
    assert score_row[2] == 0.96
    assert json.loads(score_row[3]) == {"drivers": ["NT_FILING", "8K_SPIKE"]}


def test_insert_outcome_event_dedup(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    create_db(path=db_path, reset=False)

    with get_conn(path=db_path) as conn:
        upsert_company(conn, cik=777001, name="Outcome Co", ticker="OUT", industry="Financials")
        inserted_first = insert_outcome_event(
            conn,
            cik=777001,
            event_date="2026-02-20",
            outcome_type="RESTATEMENT_DISCLOSURE",
            source="SEC 8-K",
            description="Filed a restatement-related disclosure event",
            metadata={"form": "8-K", "item": "4.02"},
        )
        inserted_second = insert_outcome_event(
            conn,
            cik=777001,
            event_date="2026-02-20",
            outcome_type="RESTATEMENT_DISCLOSURE",
            source="SEC 8-K",
            description="duplicate",
            metadata={"form": "8-K", "item": "4.02"},
        )

    conn = sqlite3.connect(db_path)
    count = conn.execute("SELECT COUNT(*) FROM outcome_events WHERE cik = 777001").fetchone()[0]
    row = conn.execute(
        """
        SELECT outcome_type, source, metadata
        FROM outcome_events
        WHERE cik = 777001
        """
    ).fetchone()
    conn.close()

    assert inserted_first is True
    assert inserted_second is False
    assert count == 1
    assert row[0] == "RESTATEMENT_DISCLOSURE"
    assert row[1] == "SEC 8-K"
    assert json.loads(row[2]) == {"form": "8-K", "item": "4.02"}
