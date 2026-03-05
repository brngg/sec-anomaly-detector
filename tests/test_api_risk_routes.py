import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.api.deps import get_db
from src.api.main import create_app
from src.api.routes.risk import _row_to_risk_score
from src.db.db_utils import get_conn, upsert_company, upsert_issuer_risk_score
from src.db.init_db import create_db


def _build_client(db_path: Path) -> TestClient:
    app = create_app()

    def override_get_db():
        with get_conn(path=db_path) as conn:
            yield conn

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app)


def test_risk_top_defaults_to_latest_as_of_date(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    create_db(path=db_path, reset=False)

    with get_conn(path=db_path) as conn:
        upsert_company(conn, cik=1001, name="High Risk Inc", ticker="HRI", industry="Tech")
        upsert_company(conn, cik=1002, name="Lower Risk Co", ticker="LOW", industry="Tech")

        upsert_issuer_risk_score(
            conn,
            cik=1001,
            as_of_date="2026-02-22",
            model_version="v1_alert_composite",
            risk_score=0.70,
            risk_rank=1,
            percentile=1.0,
            evidence={"window_scores": {"30": 0.7, "90": 0.6}},
        )
        upsert_issuer_risk_score(
            conn,
            cik=1002,
            as_of_date="2026-02-22",
            model_version="v1_alert_composite",
            risk_score=0.20,
            risk_rank=2,
            percentile=0.0,
            evidence={"window_scores": {"30": 0.2, "90": 0.1}},
        )
        upsert_issuer_risk_score(
            conn,
            cik=1001,
            as_of_date="2026-02-23",
            model_version="v1_alert_composite",
            risk_score=0.90,
            risk_rank=1,
            percentile=1.0,
            evidence={
                "window_scores": {"30": 0.9, "90": 0.8},
                "calibrated_review_priority": 0.77,
                "reason_summary": "Top drivers: NT_FILING, 8K_SPIKE.",
                "rank_stability": {
                    "state": "SPIKING_PRIORITY",
                    "universe_size": 100,
                    "rank_today": 1,
                    "rank_1d_ago": 30,
                    "rank_delta_1d": -29,
                    "top_days_7d": 2,
                    "best_rank_7d": 1,
                    "worst_rank_7d": 30,
                    "thresholds": {
                        "top_quartile_rank_max": 25,
                        "spike_min_rank_improvement": 15,
                    },
                },
                "uncertainty": {
                    "alert_count_90d": 3,
                    "effective_alert_count_90d": 2.3,
                    "signal_diversity": 0.66,
                    "recent_weight_share_7d": 0.5,
                    "confidence_score": 0.74,
                    "uncertainty_band": "MEDIUM",
                    "formula": "confidence=...",
                },
                "calibration_metadata": {
                    "status": "APPLIED",
                    "artifact_path": "/tmp/artifact.json",
                    "artifact_as_of_date": "2026-02-23",
                    "artifact_age_days": 0,
                    "train_samples": 100,
                    "used_prior_fallback": False,
                    "artifact_schema_version": 1,
                    "warn_days": 14,
                    "expire_days": 30,
                    "error_code": None,
                    "parse_errors_count": 0,
                },
            },
        )
        upsert_issuer_risk_score(
            conn,
            cik=1002,
            as_of_date="2026-02-23",
            model_version="v1_alert_composite",
            risk_score=0.30,
            risk_rank=2,
            percentile=0.0,
            evidence={"window_scores": {"30": 0.3, "90": 0.2}},
        )

    client = _build_client(db_path)
    response = client.get("/risk/top")
    assert response.status_code == 200

    payload = response.json()
    assert payload["as_of_date"] == "2026-02-23"
    assert payload["total"] == 2
    assert payload["items"][0]["cik"] == 1001
    assert payload["items"][0]["company_ticker"] == "HRI"
    assert isinstance(payload["items"][0]["evidence"], dict)
    assert payload["items"][0]["calibrated_review_priority"] == 0.77
    assert payload["items"][0]["evidence"]["rank_stability"]["state"] == "SPIKING_PRIORITY"
    assert payload["items"][0]["evidence"]["uncertainty"]["uncertainty_band"] == "MEDIUM"


def test_risk_history_and_explain(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    create_db(path=db_path, reset=False)

    with get_conn(path=db_path) as conn:
        upsert_company(conn, cik=2001, name="History Co", ticker="HIS", industry="Health")
        upsert_issuer_risk_score(
            conn,
            cik=2001,
            as_of_date="2026-02-20",
            model_version="v1_alert_composite",
            risk_score=0.40,
            risk_rank=5,
            percentile=0.5,
            evidence={"window_scores": {"30": 0.4, "90": 0.3}},
        )
        upsert_issuer_risk_score(
            conn,
            cik=2001,
            as_of_date="2026-02-23",
            model_version="v1_alert_composite",
            risk_score=0.75,
            risk_rank=2,
            percentile=0.9,
            evidence={"window_scores": {"30": 0.8, "90": 0.7}},
        )

    client = _build_client(db_path)

    history = client.get("/risk/2001/history")
    assert history.status_code == 200
    history_payload = history.json()
    assert history_payload["cik"] == 2001
    assert history_payload["total"] == 2
    assert history_payload["items"][0]["as_of_date"] == "2026-02-23"
    assert history_payload["items"][1]["as_of_date"] == "2026-02-20"

    explain = client.get("/risk/2001/explain")
    assert explain.status_code == 200
    explain_payload = explain.json()
    assert explain_payload["score"]["cik"] == 2001
    assert explain_payload["score"]["as_of_date"] == "2026-02-23"
    assert explain_payload["score"]["risk_score"] == 0.75
    assert isinstance(explain_payload["score"]["evidence"], dict)


def test_risk_endpoints_handle_missing_company_and_empty_scores(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    create_db(path=db_path, reset=False)
    client = _build_client(db_path)

    top = client.get("/risk/top")
    assert top.status_code == 200
    top_payload = top.json()
    assert top_payload["total"] == 0
    assert top_payload["items"] == []
    assert top_payload["as_of_date"] is None

    missing_history = client.get("/risk/9999/history")
    assert missing_history.status_code == 404
    assert missing_history.json()["detail"] == "Company not found"

    missing_explain = client.get("/risk/9999/explain")
    assert missing_explain.status_code == 404
    assert missing_explain.json()["detail"] == "Company not found"


def test_risk_top_prefers_default_model_version_when_multiple_exist(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    create_db(path=db_path, reset=False)

    with get_conn(path=db_path) as conn:
        upsert_company(conn, cik=3001, name="Default Model Co", ticker="DMC", industry="Tech")

        upsert_issuer_risk_score(
            conn,
            cik=3001,
            as_of_date="2026-03-04",
            model_version="v1_alert_composite",
            risk_score=0.20,
            risk_rank=1,
            percentile=1.0,
            evidence={"window_scores": {"30": 0.2, "90": 0.1}},
        )
        upsert_issuer_risk_score(
            conn,
            cik=3001,
            as_of_date="2026-03-04",
            model_version="v2_monthly_abnormal",
            risk_score=0.80,
            risk_rank=1,
            percentile=1.0,
            evidence={"monthly_baseline": {"current_interval_score_30d": 0.8}},
        )

    client = _build_client(db_path)

    top_default = client.get("/risk/top")
    assert top_default.status_code == 200
    payload_default = top_default.json()
    assert payload_default["total"] == 1
    assert payload_default["model_version"] == "v2_monthly_abnormal"
    assert payload_default["items"][0]["model_version"] == "v2_monthly_abnormal"
    assert payload_default["items"][0]["risk_score"] == 0.8

    top_v1 = client.get("/risk/top", params={"model_version": "v1_alert_composite"})
    assert top_v1.status_code == 200
    payload_v1 = top_v1.json()
    assert payload_v1["total"] == 1
    assert payload_v1["model_version"] == "v1_alert_composite"
    assert payload_v1["items"][0]["model_version"] == "v1_alert_composite"
    assert payload_v1["items"][0]["risk_score"] == 0.2


def test_row_to_risk_score_converts_date_datetime_to_iso_strings() -> None:
    parsed = _row_to_risk_score(
        {
            "score_id": 1,
            "cik": 123456,
            "as_of_date": date(2026, 3, 5),
            "model_version": "v2_monthly_abnormal",
            "risk_score": 0.42,
            "risk_rank": 7,
            "percentile": 0.87,
            "evidence": {
                "window_scores": {"30": 0.42},
                "calibration_metadata": {
                    "status": "APPLIED",
                    "warn_days": 14,
                    "expire_days": 30,
                    "artifact_as_of_date": date(2026, 3, 5),
                },
                "top_contributing_alerts_30d": [
                    {
                        "alert_id": 1,
                        "accession_id": "abc",
                        "anomaly_type": "NT_FILING",
                        "severity_score": Decimal("0.9"),
                        "recency_weight": Decimal("1.0"),
                        "weighted_severity": Decimal("0.9"),
                        "contribution_proxy": Decimal("0.5"),
                        "event_at": datetime(2026, 3, 5, 0, 0, 0, tzinfo=timezone.utc),
                        "created_at": datetime(2026, 3, 5, 0, 1, 0, tzinfo=timezone.utc),
                    }
                ],
            },
            "created_at": datetime(2026, 3, 5, 1, 2, 3, tzinfo=timezone.utc),
            "updated_at": datetime(2026, 3, 5, 2, 3, 4, tzinfo=timezone.utc),
            "company_name": "Acme Co",
            "company_ticker": "ACME",
        }
    )

    assert parsed.as_of_date == "2026-03-05"
    assert parsed.created_at.startswith("2026-03-05T01:02:03")
    assert parsed.updated_at.startswith("2026-03-05T02:03:04")
    assert parsed.evidence.calibration_metadata is not None
    assert parsed.evidence.calibration_metadata.artifact_as_of_date == "2026-03-05"
