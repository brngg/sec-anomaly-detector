import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.api.deps import get_db
from src.api.main import create_app
from src.db.db_utils import get_conn, insert_filing, upsert_company, upsert_issuer_risk_score
from src.db.init_db import create_db


def _build_client(db_path: Path) -> TestClient:
    app = create_app()

    def override_get_db():
        with get_conn(path=db_path) as conn:
            yield conn

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app)


def _insert_alert(
    conn,
    accession_id: str,
    anomaly_type: str,
    severity_score: float,
    dedupe_key: str,
    created_at: str,
) -> int:
    cur = conn.execute(
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
    return int(cur.lastrowid)


def test_alerts_filter_by_cik_and_date(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    create_db(path=db_path, reset=False)

    with get_conn(path=db_path) as conn:
        upsert_company(conn, cik=1010, name="Filter Co A", ticker="FCA", industry="Tech")
        upsert_company(conn, cik=2020, name="Filter Co B", ticker="FCB", industry="Tech")
        insert_filing(conn, "acc-1010-a", 1010, "8-K", "2026-02-20T10:00:00", "2026-02-20")
        insert_filing(conn, "acc-2020-a", 2020, "8-K", "2026-02-21T10:00:00", "2026-02-21")

        _insert_alert(
            conn,
            accession_id="acc-1010-a",
            anomaly_type="NT_FILING",
            severity_score=0.9,
            dedupe_key="filter:1010:1",
            created_at="2026-02-20 10:00:00",
        )
        _insert_alert(
            conn,
            accession_id="acc-2020-a",
            anomaly_type="8K_SPIKE",
            severity_score=0.8,
            dedupe_key="filter:2020:1",
            created_at="2026-02-21 10:00:00",
        )

    client = _build_client(db_path)

    response = client.get("/alerts", params={"cik": 1010, "date_from": "2026-02-20", "date_to": "2026-02-20"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["items"][0]["anomaly_type"] == "NT_FILING"


def test_alerts_summary_route_resolves_correct_handler(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    create_db(path=db_path, reset=False)

    with get_conn(path=db_path) as conn:
        upsert_company(conn, cik=9090, name="Summary Co", ticker="SUM", industry="Tech")
        insert_filing(conn, "acc-9090-a", 9090, "8-K", "2026-02-20T10:00:00", "2026-02-20")
        _insert_alert(
            conn,
            accession_id="acc-9090-a",
            anomaly_type="NT_FILING",
            severity_score=0.85,
            dedupe_key="summary:9090:1",
            created_at="2026-02-20 10:00:00",
        )

    client = _build_client(db_path)
    response = client.get("/alerts/summary")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["by_type"]["NT_FILING"] == 1


def test_explain_contributors_match_alert_drilldown(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    create_db(path=db_path, reset=False)

    with get_conn(path=db_path) as conn:
        upsert_company(conn, cik=3030, name="Explain Co", ticker="EXP", industry="Tech")
        insert_filing(conn, "acc-3030-a", 3030, "8-K", "2026-02-22T10:00:00", "2026-02-22")
        insert_filing(conn, "acc-3030-b", 3030, "8-K", "2026-02-21T10:00:00", "2026-02-21")

        alert_a = _insert_alert(
            conn,
            accession_id="acc-3030-a",
            anomaly_type="NT_FILING",
            severity_score=0.95,
            dedupe_key="explain:3030:1",
            created_at="2026-02-22 10:00:00",
        )
        alert_b = _insert_alert(
            conn,
            accession_id="acc-3030-b",
            anomaly_type="8K_SPIKE",
            severity_score=0.80,
            dedupe_key="explain:3030:2",
            created_at="2026-02-21 10:00:00",
        )

        upsert_issuer_risk_score(
            conn,
            cik=3030,
            as_of_date="2026-02-23",
            model_version="v1_alert_composite",
            risk_score=0.88,
            risk_rank=1,
            percentile=1.0,
            evidence={
                "window_scores": {"30": 0.9, "90": 0.8},
                "top_contributing_alerts_30d": [
                    {
                        "alert_id": alert_a,
                        "accession_id": "acc-3030-a",
                        "anomaly_type": "NT_FILING",
                        "severity_score": 0.95,
                        "recency_weight": 1.0,
                        "weighted_severity": 0.95,
                        "contribution_proxy": 0.28,
                        "created_at": "2026-02-22 10:00:00",
                    },
                    {
                        "alert_id": alert_b,
                        "accession_id": "acc-3030-b",
                        "anomaly_type": "8K_SPIKE",
                        "severity_score": 0.80,
                        "recency_weight": 1.0,
                        "weighted_severity": 0.80,
                        "contribution_proxy": 0.23,
                        "created_at": "2026-02-21 10:00:00",
                    },
                ],
            },
        )

    client = _build_client(db_path)

    explain = client.get("/risk/3030/explain")
    assert explain.status_code == 200
    explain_payload = explain.json()
    contributor_ids = {
        item["alert_id"]
        for item in explain_payload["score"]["evidence"]["top_contributing_alerts_30d"]
    }

    alerts = client.get(
        "/alerts",
        params={"cik": 3030, "date_from": "2026-02-20", "date_to": "2026-02-23"},
    )
    assert alerts.status_code == 200
    alert_ids = {item["alert_id"] for item in alerts.json()["items"]}

    assert contributor_ids.issubset(alert_ids)
