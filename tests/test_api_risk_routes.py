import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.api.deps import get_db
from src.api.main import create_app
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
            evidence={"window_scores": {"30": 0.9, "90": 0.8}},
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
