import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.db.db_utils import get_conn
from src.db.init_db import create_db
from src.ingestion import backfill
from src.ingestion.backfill import load_tickers


def test_load_tickers_valid(tmp_path: Path) -> None:
    csv_path = tmp_path / "companies.csv"
    csv_path.write_text("ticker\nAAPL\nmsft\nAAPL\n\n")

    tickers = load_tickers(csv_path)
    assert tickers == ["AAPL", "MSFT"]


def test_load_tickers_missing_header(tmp_path: Path) -> None:
    csv_path = tmp_path / "companies.csv"
    csv_path.write_text("symbol\nAAPL\n")

    with pytest.raises(SystemExit):
        load_tickers(csv_path)


def test_backfill_new_company_upserts_before_watermark(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "test.db"
    create_db(path=db_path, reset=False)

    csv_path = tmp_path / "companies.csv"
    csv_path.write_text("ticker\nTEST\n", encoding="utf-8")
    monkeypatch.setenv("COMPANIES_CSV", str(csv_path))
    monkeypatch.setenv("DRY_RUN", "0")
    monkeypatch.delenv("SEC_IDENTITY", raising=False)

    monkeypatch.setattr(backfill.db_utils, "get_conn", lambda: get_conn(path=db_path))
    monkeypatch.setattr(backfill.time, "sleep", lambda _seconds: None)

    fake_company = SimpleNamespace(cik=123456, name="Test Co", industry="Tech")
    fake_filing = SimpleNamespace(
        accession_no="0000123456-26-000001",
        form="8-K",
        acceptance_datetime="2026-02-01T10:00:00",
        filing_date="2026-02-01",
        primary_document="d1.htm",
    )
    monkeypatch.setattr(backfill, "fetch_company", lambda _ticker: fake_company)
    monkeypatch.setattr(backfill, "fetch_filings", lambda _company, _date_filter: [fake_filing])

    assert backfill.main() == 0

    with get_conn(path=db_path) as conn:
        company = conn.execute("SELECT cik FROM companies WHERE cik = ?", (123456,)).fetchone()
        watermark = conn.execute(
            "SELECT last_run_status, last_seen_filed_at FROM watermarks WHERE cik = ?",
            (123456,),
        ).fetchone()
        filing = conn.execute(
            "SELECT accession_id FROM filing_events WHERE accession_id = ?",
            ("0000123456-26-000001",),
        ).fetchone()

    assert company is not None
    assert watermark is not None
    assert watermark["last_run_status"] == "SUCCESS"
    assert filing is not None
