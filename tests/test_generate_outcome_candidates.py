import csv
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.analysis.generate_outcome_candidates import generate_outcome_candidates
from src.db.db_utils import get_conn, insert_filing, upsert_company
from src.db.init_db import create_db


def test_generate_outcome_candidates_prefilters_forms_and_text(monkeypatch, tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    output_csv = tmp_path / "outcomes_candidates.csv"
    create_db(path=db_path, reset=False)

    with get_conn(path=db_path) as conn:
        for cik in (1000, 2000, 3000, 4000, 5000):
            upsert_company(conn, cik=cik, name=f"Co {cik}", ticker=f"T{cik}", industry="Tech")

        insert_filing(
            conn,
            accession_id="0000001000-26-000001",
            cik=1000,
            filing_type="8-K",
            filed_at="2026-02-01T10:00:00",
            filed_date="2026-02-01",
            primary_document="good8k.htm",
        )
        insert_filing(
            conn,
            accession_id="0000002000-26-000002",
            cik=2000,
            filing_type="8-K",
            filed_at="2026-02-02T10:00:00",
            filed_date="2026-02-02",
            primary_document="plain8k.htm",
        )
        insert_filing(
            conn,
            accession_id="0000003000-26-000003",
            cik=3000,
            filing_type="10-Q/A",
            filed_at="2026-02-03T10:00:00",
            filed_date="2026-02-03",
            primary_document="amend10q.htm",
        )
        insert_filing(
            conn,
            accession_id="0000004000-26-000004",
            cik=4000,
            filing_type="10-K",
            filed_at="2026-02-04T10:00:00",
            filed_date="2026-02-04",
            primary_document="regular10k.htm",
        )
        insert_filing(
            conn,
            accession_id="0000005000-26-000005",
            cik=5000,
            filing_type="8-K/A",
            filed_at="2026-02-05T10:00:00",
            filed_date="2026-02-05",
            primary_document="missing8ka.htm",
        )

    mock_text = {
        "000000100026000001": (
            "Item 4.02 Non-reliance on previously issued financial statements.",
            "https://www.sec.gov/Archives/edgar/data/1000/000000100026000001/good8k.htm",
        ),
        "000000200026000002": (
            "This filing discusses ordinary operations and no adverse item cues.",
            "https://www.sec.gov/Archives/edgar/data/2000/000000200026000002/plain8k.htm",
        ),
        "000000300026000003": (
            "This Form 10-Q/A includes restatement adjustments for prior periods.",
            "https://www.sec.gov/Archives/edgar/data/3000/000000300026000003/amend10q.htm",
        ),
    }

    def _fake_fetch(session, url_candidates, timeout_seconds):  # noqa: ARG001
        joined = " ".join(url_candidates)
        for accession_nodash, payload in mock_text.items():
            if accession_nodash in joined:
                return payload
        raise requests.RequestException("404")

    monkeypatch.setattr("src.analysis.generate_outcome_candidates._fetch_filing_text", _fake_fetch)

    stats = generate_outcome_candidates(
        output_csv=output_csv,
        db_path=db_path,
        date_from="2026-02-01",
        date_to="2026-02-28",
        sleep_seconds=0.0,
    )

    assert stats["rows_scanned"] == 4  # excludes regular 10-K by default form filter
    assert stats["rows_written"] == 2
    assert stats["skipped_rejected"] == 1
    assert stats["skipped_fetch_error"] == 1

    with output_csv.open("r", newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    assert {row["accession_id"] for row in rows} == {
        "0000001000-26-000001",
        "0000003000-26-000003",
    }
    assert {row["confidence_band"] for row in rows} == {"HIGH", "MEDIUM"}


def test_generate_outcome_candidates_min_confidence_high(monkeypatch, tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    output_csv = tmp_path / "outcomes_candidates.csv"
    create_db(path=db_path, reset=False)

    with get_conn(path=db_path) as conn:
        upsert_company(conn, cik=1000, name="Co 1000", ticker="T1000", industry="Tech")
        upsert_company(conn, cik=3000, name="Co 3000", ticker="T3000", industry="Tech")
        insert_filing(
            conn,
            accession_id="0000001000-26-000001",
            cik=1000,
            filing_type="8-K",
            filed_at="2026-02-01T10:00:00",
            filed_date="2026-02-01",
            primary_document="good8k.htm",
        )
        insert_filing(
            conn,
            accession_id="0000003000-26-000003",
            cik=3000,
            filing_type="10-Q/A",
            filed_at="2026-02-03T10:00:00",
            filed_date="2026-02-03",
            primary_document="amend10q.htm",
        )

    def _fake_fetch(session, url_candidates, timeout_seconds):  # noqa: ARG001
        joined = " ".join(url_candidates)
        if "000000100026000001" in joined:
            return (
                "Item 4.02 Non-reliance on previously issued financial statements.",
                "https://www.sec.gov/Archives/edgar/data/1000/000000100026000001/good8k.htm",
            )
        return (
            "This Form 10-Q/A includes restatement adjustments for prior periods.",
            "https://www.sec.gov/Archives/edgar/data/3000/000000300026000003/amend10q.htm",
        )

    monkeypatch.setattr("src.analysis.generate_outcome_candidates._fetch_filing_text", _fake_fetch)

    stats = generate_outcome_candidates(
        output_csv=output_csv,
        db_path=db_path,
        date_from="2026-02-01",
        date_to="2026-02-28",
        min_confidence="HIGH",
        sleep_seconds=0.0,
    )

    assert stats["rows_written"] == 1
    assert stats["skipped_low_confidence"] == 1

    with output_csv.open("r", newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1
    assert rows[0]["confidence_band"] == "HIGH"
