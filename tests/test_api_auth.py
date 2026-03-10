import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.api.deps import get_db
from src.api.main import create_app
from src.db.db_utils import get_conn, upsert_company
from src.db.init_db import create_db


def _build_client(db_path: Path) -> TestClient:
    app = create_app()

    def override_get_db():
        with get_conn(path=db_path) as conn:
            yield conn

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app)


def test_health_route_stays_public_when_auth_enabled(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "test.db"
    create_db(path=db_path, reset=False)
    monkeypatch.setenv("API_AUTH_ENABLED", "1")
    monkeypatch.setenv("API_KEY", "secret-key")

    client = _build_client(db_path)
    response = client.get("/health")

    assert response.status_code == 200


def test_protected_routes_require_matching_api_key(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "test.db"
    create_db(path=db_path, reset=False)

    with get_conn(path=db_path) as conn:
        upsert_company(conn, cik=1234, name="Auth Co", ticker="AUT", industry="Tech")

    monkeypatch.setenv("API_AUTH_ENABLED", "1")
    monkeypatch.setenv("API_KEY", "secret-key")
    client = _build_client(db_path)

    missing = client.get("/companies")
    assert missing.status_code == 401
    assert missing.json()["detail"] == "Invalid API key"

    wrong = client.get("/companies", headers={"X-API-Key": "wrong"})
    assert wrong.status_code == 401
    assert wrong.json()["detail"] == "Invalid API key"

    allowed = client.get("/companies", headers={"X-API-Key": "secret-key"})
    assert allowed.status_code == 200
    payload = allowed.json()
    assert payload["total"] == 1
    assert payload["items"][0]["cik"] == 1234
