import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.analysis.build_risk_scores import LOOKBACK_WINDOWS, MODEL_VERSION, run_risk_scoring
from src.db.db_utils import get_conn, insert_filing, upsert_company
from src.db.init_db import create_db


def _insert_alert(
    conn,
    accession_id: str,
    anomaly_type: str,
    severity_score: float,
    created_at: str,
    dedupe_key: str,
) -> None:
    conn.execute(
        """
        INSERT INTO alerts (
            accession_id,
            anomaly_type,
            severity_score,
            description,
            details,
            status,
            dedupe_key,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, 'OPEN', ?, ?)
        """,
        (
            accession_id,
            anomaly_type,
            severity_score,
            f"{anomaly_type} synthetic test event",
            "{}",
            dedupe_key,
            created_at,
        ),
    )


def test_run_risk_scoring_builds_ranked_scores(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    create_db(path=db_path, reset=False)

    with get_conn(path=db_path) as conn:
        # Three issuers: one high-risk, one moderate/old signal, one no-signal.
        upsert_company(conn, cik=1001, name="High Risk Inc", ticker="HRI", industry="Tech")
        upsert_company(conn, cik=1002, name="Moderate Co", ticker="MOD", industry="Industrials")
        upsert_company(conn, cik=1003, name="Quiet Corp", ticker="QUI", industry="Utilities")

        insert_filing(conn, "acc-1001-a", 1001, "8-K", "2026-02-22T10:00:00", "2026-02-22")
        insert_filing(conn, "acc-1001-b", 1001, "8-K", "2026-02-21T10:00:00", "2026-02-21")
        insert_filing(conn, "acc-1002-a", 1002, "10-Q", "2026-01-01T10:00:00", "2026-01-01")

        _insert_alert(
            conn,
            accession_id="acc-1001-a",
            anomaly_type="NT_FILING",
            severity_score=0.90,
            created_at="2026-02-22 10:00:00",
            dedupe_key="test:1001:nt",
        )
        _insert_alert(
            conn,
            accession_id="acc-1001-b",
            anomaly_type="8K_SPIKE",
            severity_score=0.80,
            created_at="2026-02-21 10:00:00",
            dedupe_key="test:1001:spike",
        )
        _insert_alert(
            conn,
            accession_id="acc-1002-a",
            anomaly_type="FRIDAY_BURYING",
            severity_score=0.65,
            created_at="2026-01-01 10:00:00",
            dedupe_key="test:1002:friday",
        )

    stats = run_risk_scoring(path=db_path, as_of_date="2026-02-23")
    assert stats["issuers_scored"] == 3
    assert stats["snapshots_upserted"] == 6  # 3 issuers x 2 windows
    assert stats["scores_upserted"] == 3
    assert stats["source_alerts"] == 3

    # Re-run to verify upsert behavior stays idempotent.
    second_stats = run_risk_scoring(path=db_path, as_of_date="2026-02-23")
    assert second_stats["issuers_scored"] == 3
    assert second_stats["snapshots_upserted"] == 6
    assert second_stats["scores_upserted"] == 3

    with get_conn(path=db_path) as conn:
        score_rows = conn.execute(
            """
            SELECT cik, risk_score, risk_rank, percentile, evidence
            FROM issuer_risk_scores
            WHERE as_of_date = '2026-02-23' AND model_version = ?
            ORDER BY risk_rank ASC
            """,
            (MODEL_VERSION,),
        ).fetchall()
        snapshot_rows = conn.execute(
            """
            SELECT cik, lookback_days, source_alert_count, features
            FROM feature_snapshots
            WHERE as_of_date = '2026-02-23'
            ORDER BY cik, lookback_days
            """
        ).fetchall()

    assert len(score_rows) == 3
    assert score_rows[0]["cik"] == 1001
    assert score_rows[0]["risk_score"] > score_rows[1]["risk_score"]
    assert score_rows[1]["risk_score"] >= score_rows[2]["risk_score"]
    assert score_rows[0]["risk_rank"] == 1
    assert 0.0 <= score_rows[0]["percentile"] <= 1.0

    # Ensure no duplicates were created by second run.
    assert len(snapshot_rows) == 6

    moderate_30 = next(
        row for row in snapshot_rows if row["cik"] == 1002 and row["lookback_days"] == 30
    )
    moderate_90 = next(
        row for row in snapshot_rows if row["cik"] == 1002 and row["lookback_days"] == 90
    )
    assert moderate_30["source_alert_count"] == 0
    assert moderate_90["source_alert_count"] == 1

    evidence = json.loads(score_rows[0]["evidence"])
    assert evidence["model_version"] == MODEL_VERSION
    assert "window_scores" in evidence
    assert f"top_signals_{min(LOOKBACK_WINDOWS)}d" in evidence


def test_equal_scores_share_rank_and_percentile(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    create_db(path=db_path, reset=False)

    with get_conn(path=db_path) as conn:
        upsert_company(conn, cik=2001, name="No Signal A", ticker="NSA", industry="Tech")
        upsert_company(conn, cik=2002, name="No Signal B", ticker="NSB", industry="Tech")
        upsert_company(conn, cik=2003, name="Signal Co", ticker="SIG", industry="Tech")

        insert_filing(conn, "acc-2003-a", 2003, "8-K", "2026-02-22T10:00:00", "2026-02-22")
        _insert_alert(
            conn,
            accession_id="acc-2003-a",
            anomaly_type="NT_FILING",
            severity_score=0.90,
            created_at="2026-02-22 10:00:00",
            dedupe_key="test:2003:nt",
        )

    run_risk_scoring(path=db_path, as_of_date="2026-02-23")

    with get_conn(path=db_path) as conn:
        rows = conn.execute(
            """
            SELECT cik, risk_score, risk_rank, percentile
            FROM issuer_risk_scores
            WHERE as_of_date = '2026-02-23' AND model_version = ?
            ORDER BY cik
            """,
            (MODEL_VERSION,),
        ).fetchall()

    assert len(rows) == 3
    no_signal_rows = [row for row in rows if row["cik"] in {2001, 2002}]
    assert len(no_signal_rows) == 2
    assert no_signal_rows[0]["risk_score"] == 0.0
    assert no_signal_rows[1]["risk_score"] == 0.0
    assert no_signal_rows[0]["risk_rank"] == no_signal_rows[1]["risk_rank"]
    assert no_signal_rows[0]["percentile"] == no_signal_rows[1]["percentile"]


def test_out_of_range_severity_raises(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    create_db(path=db_path, reset=False)

    with get_conn(path=db_path) as conn:
        upsert_company(conn, cik=3001, name="Bad Severity", ticker="BAD", industry="Tech")
        insert_filing(conn, "acc-3001-a", 3001, "8-K", "2026-02-22T10:00:00", "2026-02-22")
        _insert_alert(
            conn,
            accession_id="acc-3001-a",
            anomaly_type="NT_FILING",
            severity_score=42.0,
            created_at="2026-02-22 10:00:00",
            dedupe_key="test:3001:nt",
        )

    with pytest.raises(ValueError, match="severity_score out of expected range"):
        run_risk_scoring(path=db_path, as_of_date="2026-02-23")
