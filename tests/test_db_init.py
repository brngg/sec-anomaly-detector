import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.db.db_utils import get_conn, upsert_company, update_watermark
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
