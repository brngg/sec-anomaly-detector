import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.analysis.evaluate_review_priority import evaluate_review_priority
from src.analysis.import_outcomes import import_outcomes
from src.db.db_utils import get_conn, upsert_company, upsert_issuer_risk_score
from src.db.init_db import create_db


def _evidence(nt: float, friday: float, spike: float) -> dict:
    return {
        "model_version": "v1_alert_composite",
        "as_of_date": "2026-01-01",
        "window_scores": {"30": (nt + friday + spike) / 3.0, "90": (nt + friday + spike) / 3.0},
        "component_breakdown": [
            {
                "lookback_days": 30,
                "window_weight": 0.65,
                "window_score": (nt + friday + spike) / 3.0,
                "signal_components": {
                    "NT_FILING": {
                        "signal": "NT_FILING",
                        "count": 1,
                        "weighted_severity": nt,
                        "scale": 1.5,
                        "component": nt,
                        "anomaly_weight": 0.45,
                        "weight_contribution": nt * 0.45,
                    },
                    "FRIDAY_BURYING": {
                        "signal": "FRIDAY_BURYING",
                        "count": 1,
                        "weighted_severity": friday,
                        "scale": 2.5,
                        "component": friday,
                        "anomaly_weight": 0.20,
                        "weight_contribution": friday * 0.20,
                    },
                    "8K_SPIKE": {
                        "signal": "8K_SPIKE",
                        "count": 1,
                        "weighted_severity": spike,
                        "scale": 1.2,
                        "component": spike,
                        "anomaly_weight": 0.35,
                        "weight_contribution": spike * 0.35,
                    },
                },
            }
        ],
    }


def test_import_outcomes_inserts_and_dedupes(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    csv_path = tmp_path / "outcomes.csv"
    create_db(path=db_path, reset=False)

    with get_conn(path=db_path) as conn:
        upsert_company(conn, cik=5010, name="Outcome A", ticker="OA", industry="Tech")

    csv_path.write_text(
        "cik,event_date,outcome_type,source,description,form,item,url,accession_id,verification_status\n"
        "5010,2026-02-20,RESTATEMENT_DISCLOSURE,SEC 8-K,first,8-K,4.02,https://www.sec.gov/example,0000005010-26-000001,VERIFIED_HIGH\n"
        "5010,2026-02-20,RESTATEMENT_DISCLOSURE,SEC 8-K,duplicate,8-K,4.02,https://www.sec.gov/example,0000005010-26-000001,VERIFIED_HIGH\n"
        "bad,2026-02-20,RESTATEMENT_DISCLOSURE,SEC 8-K,invalid,8-K,4.02,https://www.sec.gov/example,0000005010-26-000002,VERIFIED_HIGH\n",
        encoding="utf-8",
    )

    stats = import_outcomes(csv_path=csv_path, path=db_path)
    assert stats["inserted"] == 1
    assert stats["skipped"] == 1
    assert stats["invalid"] == 1

    with get_conn(path=db_path) as conn:
        row = conn.execute(
            """
            SELECT form, item, accession_id, filing_url, verification_status, metadata
            FROM outcome_events
            WHERE cik = 5010
            """
        ).fetchone()

    assert row is not None
    assert row["form"] == "8-K"
    assert row["item"] == "4.02"
    assert row["accession_id"] == "0000005010-26-000001"
    assert row["filing_url"] == "https://www.sec.gov/example"
    assert row["verification_status"] == "VERIFIED_HIGH"
    metadata = json.loads(row["metadata"])
    assert metadata["form"] == "8-K"
    assert metadata["item"] == "4.02"


def test_import_outcomes_respects_min_confidence(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    csv_path = tmp_path / "outcomes.csv"
    create_db(path=db_path, reset=False)

    with get_conn(path=db_path) as conn:
        upsert_company(conn, cik=7010, name="Outcome A", ticker="OA", industry="Tech")
        upsert_company(conn, cik=7011, name="Outcome B", ticker="OB", industry="Tech")
        upsert_company(conn, cik=7012, name="Outcome C", ticker="OC", industry="Tech")

    csv_path.write_text(
        "cik,event_date,outcome_type,source,description,verification_status,confidence_band\n"
        "7010,2026-02-20,RESTATEMENT_DISCLOSURE,SEC,high,VERIFIED_HIGH,HIGH\n"
        "7011,2026-02-20,RESTATEMENT_DISCLOSURE,SEC,medium,VERIFIED_MEDIUM,MEDIUM\n"
        "7012,2026-02-20,RESTATEMENT_DISCLOSURE,SEC,low,POSSIBLE,LOW\n",
        encoding="utf-8",
    )

    stats = import_outcomes(csv_path=csv_path, path=db_path, min_confidence="MEDIUM")
    assert stats["inserted"] == 2
    assert stats["filtered"] == 1

    with get_conn(path=db_path) as conn:
        count = conn.execute("SELECT COUNT(*) AS count FROM outcome_events").fetchone()["count"]
        statuses = conn.execute(
            "SELECT verification_status FROM outcome_events ORDER BY verification_status"
        ).fetchall()

    assert count == 2
    assert [row["verification_status"] for row in statuses] == ["VERIFIED_HIGH", "VERIFIED_MEDIUM"]


def test_evaluate_review_priority_outputs_metrics_and_reports(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    reports_dir = tmp_path / "reports"
    create_db(path=db_path, reset=False)

    with get_conn(path=db_path) as conn:
        for cik in (6001, 6002, 6003):
            upsert_company(conn, cik=cik, name=f"Co {cik}", ticker=f"T{cik}", industry="Tech")

        # First snapshot
        upsert_issuer_risk_score(
            conn,
            cik=6001,
            as_of_date="2026-01-01",
            model_version="v1_alert_composite",
            risk_score=0.90,
            risk_rank=1,
            percentile=1.0,
            evidence=_evidence(0.9, 0.2, 0.1),
        )
        upsert_issuer_risk_score(
            conn,
            cik=6002,
            as_of_date="2026-01-01",
            model_version="v1_alert_composite",
            risk_score=0.50,
            risk_rank=2,
            percentile=0.5,
            evidence=_evidence(0.4, 0.2, 0.1),
        )
        upsert_issuer_risk_score(
            conn,
            cik=6003,
            as_of_date="2026-01-01",
            model_version="v1_alert_composite",
            risk_score=0.10,
            risk_rank=3,
            percentile=0.0,
            evidence=_evidence(0.1, 0.0, 0.0),
        )

        # Second snapshot
        upsert_issuer_risk_score(
            conn,
            cik=6001,
            as_of_date="2026-01-08",
            model_version="v1_alert_composite",
            risk_score=0.88,
            risk_rank=1,
            percentile=1.0,
            evidence=_evidence(0.8, 0.2, 0.1),
        )
        upsert_issuer_risk_score(
            conn,
            cik=6002,
            as_of_date="2026-01-08",
            model_version="v1_alert_composite",
            risk_score=0.40,
            risk_rank=2,
            percentile=0.5,
            evidence=_evidence(0.2, 0.3, 0.1),
        )
        upsert_issuer_risk_score(
            conn,
            cik=6003,
            as_of_date="2026-01-08",
            model_version="v1_alert_composite",
            risk_score=0.05,
            risk_rank=3,
            percentile=0.0,
            evidence=_evidence(0.0, 0.0, 0.0),
        )

        # Outcome on same day as second snapshot must not count for that snapshot.
        conn.execute(
            """
            INSERT INTO outcome_events (cik, event_date, outcome_type, source, description, metadata, dedupe_key)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                6002,
                "2026-01-08",
                "RESTATEMENT_DISCLOSURE",
                "SEC 8-K",
                "same-day outcome",
                "{}",
                "outcome:6002:2026-01-08",
            ),
        )
        conn.execute(
            """
            INSERT INTO outcome_events (cik, event_date, outcome_type, source, description, metadata, dedupe_key)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                6001,
                "2026-01-20",
                "RESTATEMENT_DISCLOSURE",
                "SEC 8-K",
                "forward outcome",
                "{}",
                "outcome:6001:2026-01-20",
            ),
        )

    summary = evaluate_review_priority(
        path=db_path,
        model_version="v1_alert_composite",
        outcome_types=("RESTATEMENT_DISCLOSURE",),
        horizon_days=90,
        k_values=(1, 2),
        bootstrap_samples=50,
        random_seed=3,
        min_calibration_samples=2,
        output_dir=reports_dir,
    )

    assert summary["as_of_dates_evaluated"] == 2
    assert summary["aggregate_metrics"]
    assert summary["calibration_artifact_schema_version"] == 1

    second_snapshot_rows = [
        row
        for row in summary["daily_metrics"]
        if row["as_of_date"] == "2026-01-08" and row["method"] == "model" and row["k"] == 2
    ]
    assert second_snapshot_rows
    # 6002 same-day outcome should not be counted for 2026-01-08.
    assert second_snapshot_rows[0]["total_positives"] == 1

    report_json = Path(summary["report_json_path"])
    report_md = Path(summary["report_md_path"])
    calibration_json = Path(summary["calibration_path"])
    assert report_json.exists()
    assert report_md.exists()
    assert calibration_json.exists()

    calibration_payload = json.loads(calibration_json.read_text(encoding="utf-8"))
    assert calibration_payload["artifact_schema_version"] == 1
    assert calibration_payload["calibration"]


def test_evaluate_review_priority_verification_status_filter(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    create_db(path=db_path, reset=False)

    with get_conn(path=db_path) as conn:
        upsert_company(conn, cik=8101, name="Co 8101", ticker="A", industry="Tech")
        upsert_company(conn, cik=8102, name="Co 8102", ticker="B", industry="Tech")

        upsert_issuer_risk_score(
            conn,
            cik=8101,
            as_of_date="2026-01-01",
            model_version="v1_alert_composite",
            risk_score=0.90,
            risk_rank=1,
            percentile=1.0,
            evidence=_evidence(0.9, 0.1, 0.0),
        )
        upsert_issuer_risk_score(
            conn,
            cik=8102,
            as_of_date="2026-01-01",
            model_version="v1_alert_composite",
            risk_score=0.80,
            risk_rank=2,
            percentile=0.5,
            evidence=_evidence(0.8, 0.1, 0.0),
        )

        conn.execute(
            """
            INSERT INTO outcome_events (
                cik, event_date, outcome_type, source, description, verification_status, metadata, dedupe_key
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                8101,
                "2026-01-20",
                "RESTATEMENT_DISCLOSURE",
                "SEC",
                "high label",
                "VERIFIED_HIGH",
                "{}",
                "k1",
            ),
        )
        conn.execute(
            """
            INSERT INTO outcome_events (
                cik, event_date, outcome_type, source, description, verification_status, metadata, dedupe_key
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                8102,
                "2026-01-20",
                "RESTATEMENT_DISCLOSURE",
                "SEC",
                "medium label",
                "VERIFIED_MEDIUM",
                "{}",
                "k2",
            ),
        )

    strict = evaluate_review_priority(
        path=db_path,
        model_version="v1_alert_composite",
        outcome_types=("RESTATEMENT_DISCLOSURE",),
        verification_statuses=("VERIFIED_HIGH",),
        horizon_days=90,
        k_values=(2,),
        bootstrap_samples=10,
        random_seed=3,
        min_calibration_samples=2,
        output_dir=None,
    )
    broad = evaluate_review_priority(
        path=db_path,
        model_version="v1_alert_composite",
        outcome_types=("RESTATEMENT_DISCLOSURE",),
        verification_statuses=("VERIFIED_HIGH", "VERIFIED_MEDIUM"),
        horizon_days=90,
        k_values=(2,),
        bootstrap_samples=10,
        random_seed=3,
        min_calibration_samples=2,
        output_dir=None,
    )

    strict_row = next(row for row in strict["daily_metrics"] if row["method"] == "model")
    broad_row = next(row for row in broad["daily_metrics"] if row["method"] == "model")
    assert strict_row["total_positives"] == 1
    assert broad_row["total_positives"] == 2
