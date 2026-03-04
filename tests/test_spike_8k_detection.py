from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.db.db_utils import get_conn, insert_filing, upsert_company
from src.db.init_db import create_db
from src.detection import spike_8k_detection
from src.detection.spike_8k_detection import SpikeFiling


def _month_start_iso(year: int, month: int) -> tuple[str, str]:
    ts = datetime(year, month, 1, 12, 0, 0, tzinfo=timezone.utc).isoformat()
    day = f"{year:04d}-{month:02d}-01"
    return ts, day


def _build_spike_filings_for_one_issuer(
    cik: int,
    month_counts: dict[str, int],
) -> list[SpikeFiling]:
    filings: list[SpikeFiling] = []
    accession_index = 0
    for month in sorted(month_counts.keys()):
        year, month_num = map(int, month.split("-"))
        filed_at, filed_date = _month_start_iso(year, month_num)
        for _ in range(month_counts[month]):
            accession_index += 1
            filings.append(
                SpikeFiling(
                    accession_id=f"acc-{cik}-{month}-{accession_index:03d}",
                    cik=cik,
                    filing_type="8-K",
                    filed_at=filed_at,
                    filed_date=filed_date,
                )
            )
    return filings


def test_detect_monthly_spikes_does_not_replay_old_month_when_current_month_empty() -> None:
    cik = 111001
    # Feb 2026 is a spike relative to Sep 2025-Jan 2026 baseline; March has no filings.
    filings = _build_spike_filings_for_one_issuer(
        cik=cik,
        month_counts={
            "2025-09": 1,
            "2025-10": 1,
            "2025-11": 1,
            "2025-12": 1,
            "2026-01": 1,
            "2026-02": 4,
        },
    )

    feb_spikes = spike_8k_detection.detect_monthly_spikes(filings, target_month="2026-02")
    mar_spikes = spike_8k_detection.detect_monthly_spikes(filings, target_month="2026-03")

    assert len(feb_spikes) == 1
    assert feb_spikes[0].month == "2026-02"
    assert feb_spikes[0].count == 4
    assert mar_spikes == []


def test_detect_monthly_spikes_uses_strict_greater_than_threshold() -> None:
    cik = 111002
    # Baseline is flat at 1, so threshold is exactly 1 with std=0.
    base_counts = {
        "2025-10": 1,
        "2025-11": 1,
        "2025-12": 1,
        "2026-01": 1,
        "2026-02": 1,
    }

    equal_threshold_filings = _build_spike_filings_for_one_issuer(
        cik=cik,
        month_counts={**base_counts, "2026-03": 1},
    )
    above_threshold_filings = _build_spike_filings_for_one_issuer(
        cik=cik,
        month_counts={**base_counts, "2026-03": 2},
    )

    equal_spikes = spike_8k_detection.detect_monthly_spikes(
        equal_threshold_filings, target_month="2026-03"
    )
    above_spikes = spike_8k_detection.detect_monthly_spikes(
        above_threshold_filings, target_month="2026-03"
    )

    assert equal_spikes == []
    assert len(above_spikes) == 1
    assert above_spikes[0].count == 2
    assert above_spikes[0].threshold == pytest.approx(1.0)


def test_run_8k_spike_detection_is_idempotent_with_dedupe_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "test.db"
    create_db(path=db_path, reset=False)

    cik = 111003
    with get_conn(path=db_path) as conn:
        upsert_company(conn, cik=cik, name="Spike Co", ticker="SPK", industry="Tech")

        month_counts = {
            "2025-10": 1,
            "2025-11": 1,
            "2025-12": 1,
            "2026-01": 1,
            "2026-02": 1,
            "2026-03": 3,
        }
        acc_index = 0
        for month, count in month_counts.items():
            year, month_num = map(int, month.split("-"))
            filed_at, filed_date = _month_start_iso(year, month_num)
            for _ in range(count):
                acc_index += 1
                accession_id = f"000111003-26-{acc_index:06d}"
                insert_filing(
                    conn,
                    accession_id=accession_id,
                    cik=cik,
                    filing_type="8-K",
                    filed_at=filed_at,
                    filed_date=filed_date,
                    primary_document=None,
                )

    monkeypatch.setattr(spike_8k_detection.db_utils, "get_conn", lambda: get_conn(path=db_path))

    first_total, first_inserted = spike_8k_detection.run_8k_spike_detection(target_month="2026-03")
    second_total, second_inserted = spike_8k_detection.run_8k_spike_detection(target_month="2026-03")

    assert first_total == 1
    assert first_inserted == 1
    assert second_total == 1
    assert second_inserted == 0

    with get_conn(path=db_path) as conn:
        rows = conn.execute(
            """
            SELECT anomaly_type, dedupe_key
            FROM alerts
            WHERE anomaly_type = '8K_SPIKE'
            """
        ).fetchall()

    assert len(rows) == 1
    assert rows[0]["dedupe_key"] == f"8K_SPIKE:{cik}:2026-03"
