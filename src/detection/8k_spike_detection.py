"""Detect abnormal monthly spikes in 8-K filing volume per company."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.db import db_utils
from src.detection.alerts import insert_alert


ANOMALY_TYPE = "8K_SPIKE"
DEFAULT_SEVERITY = 0.7

TARGET_FORMS = {"8-K", "8-K/A"}
LOOKBACK_MONTHS = 6
BASELINE_MONTHS = 5
# Minimum baseline months with at least one filing (company-specific sufficiency check).
MIN_BASELINE_MONTHS = 3
STD_MULTIPLIER = 2.0


@dataclass(frozen=True)
class SpikeFiling:
    accession_id: str
    cik: int
    filing_type: str
    filed_at: str
    filed_date: str


@dataclass(frozen=True)
class SpikeEvent:
    accession_id: str
    cik: int
    month: str
    count: int
    baseline_mean: float
    baseline_std: float
    threshold: float


def _parse_dt(value: str) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _month_key(filed_date: str, filed_at: str) -> str:
    if filed_date:
        return filed_date[:7]
    return filed_at[:7]


def _add_months(year: int, month: int, delta: int) -> Tuple[int, int]:
    total = year * 12 + (month - 1) + delta
    new_year = total // 12
    new_month = total % 12 + 1
    return new_year, new_month


def _iter_months(end_month: str, months: int) -> List[str]:
    year, month = map(int, end_month.split("-"))
    month_list: List[str] = []
    offset = months - 1
    while offset >= 0:
        y, m = _add_months(year, month, -offset)
        month_list.append(f"{y:04d}-{m:02d}")
        offset -= 1
    return month_list


def _mean_std(values: List[int]) -> Tuple[float, float]:
    if not values:
        return 0.0, 0.0
    total = 0.0
    count = 0
    for value in values:
        total += value
        count += 1
    mean = total / count

    if count <= 1:
        return mean, mean * 0.1

    variance_sum = 0.0
    for value in values:
        diff = value - mean
        variance_sum += diff * diff

    variance = variance_sum / count
    std = variance ** 0.5
    return mean, std


def _extract_baseline_months(month_window: List[str]) -> List[str]:
    baseline: List[str] = []
    start_index = len(month_window) - (BASELINE_MONTHS + 1)
    end_index = len(month_window) - 1
    if start_index < 0:
        return baseline

    index = start_index
    while index < end_index:
        baseline.append(month_window[index])
        index += 1
    return baseline


def score_monthly_spike(count: int, mean: float, std: float) -> float:
    if std > 0:
        sigma = (count - mean) / std
    else:
        sigma = float("inf") if count > mean else 0.0

    if sigma >= 4.0:
        return 0.90
    if sigma >= 3.0:
        return 0.80
    if sigma >= 2.0:
        return 0.70
    return 0.60


def fetch_8k_filings(conn) -> List[SpikeFiling]:
    rows = conn.execute(
        """
        SELECT
            accession_id,
            cik,
            filing_type,
            filed_at,
            filed_date
        FROM filing_events
        WHERE filing_type IN ({})
          AND filed_date >= date('now', ?)
        ORDER BY filed_at DESC
        """.format(",".join("?" * len(TARGET_FORMS))),
        (*TARGET_FORMS, f"-{LOOKBACK_MONTHS} months"),
    ).fetchall()

    filings: List[SpikeFiling] = []
    for row in rows:
        filings.append(
            SpikeFiling(
                accession_id=row["accession_id"],
                cik=row["cik"],
                filing_type=row["filing_type"],
                filed_at=row["filed_at"],
                filed_date=row["filed_date"],
            )
        )
    return filings


def _build_monthly_counts(
    filings: List[SpikeFiling],
) -> Tuple[Dict[int, Dict[str, int]], Dict[int, Dict[str, str]]]:
    counts: Dict[int, Dict[str, int]] = {}
    latest_accession: Dict[int, Dict[str, str]] = {}
    latest_filed_at: Dict[int, Dict[str, datetime]] = {}

    for filing in filings:
        month = _month_key(filing.filed_date, filing.filed_at)
        if filing.cik not in counts:
            counts[filing.cik] = {}
            latest_accession[filing.cik] = {}
            latest_filed_at[filing.cik] = {}

        counts[filing.cik][month] = counts[filing.cik].get(month, 0) + 1

        filed_at = _parse_dt(filing.filed_at)
        current_latest = latest_filed_at[filing.cik].get(month)
        if current_latest is None or filed_at > current_latest:
            latest_filed_at[filing.cik][month] = filed_at
            latest_accession[filing.cik][month] = filing.accession_id

    return counts, latest_accession


def detect_monthly_spikes(filings: List[SpikeFiling]) -> List[SpikeEvent]:
    spikes: List[SpikeEvent] = []
    counts_by_company, latest_accession = _build_monthly_counts(filings)

    for cik, month_counts in counts_by_company.items():
        months_with_filings = list(month_counts.keys())
        months_with_filings.sort()
        if not months_with_filings:
            continue

        target_month = months_with_filings[-1]
        month_window = _iter_months(target_month, LOOKBACK_MONTHS)
        baseline_months = _extract_baseline_months(month_window)
        if len(baseline_months) < MIN_BASELINE_MONTHS:
            continue

        baseline_counts: List[int] = []
        active_months = 0
        for month in baseline_months:
            count = month_counts.get(month, 0)
            baseline_counts.append(count)
            if count > 0:
                active_months += 1
        if active_months < MIN_BASELINE_MONTHS:
            continue

        mean, std = _mean_std(baseline_counts)
        threshold = mean + STD_MULTIPLIER * std

        current_count = month_counts.get(target_month, 0)
        if current_count == 0:
            continue
        if current_count > threshold:
            accession_id = latest_accession.get(cik, {}).get(target_month)
            if not accession_id:
                continue

            spikes.append(
                SpikeEvent(
                    accession_id=accession_id,
                    cik=cik,
                    month=target_month,
                    count=current_count,
                    baseline_mean=mean,
                    baseline_std=std,
                    threshold=threshold,
                )
            )

    return spikes


def _fetch_company_map(conn) -> Dict[int, Tuple[Optional[str], Optional[str]]]:
    rows = conn.execute("SELECT cik, ticker, name FROM companies").fetchall()
    company_map: Dict[int, Tuple[Optional[str], Optional[str]]] = {}
    for row in rows:
        company_map[int(row["cik"])] = (row["ticker"], row["name"])
    return company_map


def run_8k_spike_detection() -> Tuple[int, int]:
    """Insert alerts for monthly 8-K spikes. Returns (total_spikes, inserted_alerts)."""
    with db_utils.get_conn() as conn:
        filings = fetch_8k_filings(conn)
        spikes = detect_monthly_spikes(filings)
        company_map = _fetch_company_map(conn)

        inserted = 0
        for spike in spikes:
            ticker, name = company_map.get(spike.cik, (None, None))
            severity = score_monthly_spike(spike.count, spike.baseline_mean, spike.baseline_std)
            details = {
                "cik": spike.cik,
                "company_name": name,
                "company_ticker": ticker,
                "month": spike.month,
                "count": spike.count,
                "baseline_mean": spike.baseline_mean,
                "baseline_std": spike.baseline_std,
                "threshold": spike.threshold,
                "forms": sorted(TARGET_FORMS),
            }
            description = f"8-K monthly spike: {spike.count} filings in {spike.month}"
            dedupe_key = f"{ANOMALY_TYPE}:{spike.cik}:{spike.month}"

            if insert_alert(
                conn,
                accession_id=spike.accession_id,
                anomaly_type=ANOMALY_TYPE,
                severity_score=severity,
                description=description,
                details=details,
                dedupe_key=dedupe_key,
            ):
                inserted += 1

    return len(spikes), inserted


def print_spike_summary(
    spikes: List[SpikeEvent],
    company_map: Dict[int, Tuple[Optional[str], Optional[str]]],
    limit: int = 10,
) -> None:
    spikes_sorted = list(spikes)
    spikes_sorted.sort(key=lambda spike: spike.count, reverse=True)
    print("Top 8-K monthly spikes:")
    shown = 0
    for spike in spikes_sorted:
        ticker, name = company_map.get(spike.cik, (None, None))
        print(
            f"  {ticker or 'N/A'} | {name or 'Unknown'} | {spike.month} | "
            f"count={spike.count} | mean={spike.baseline_mean:.2f} | "
            f"std={spike.baseline_std:.2f} | threshold={spike.threshold:.2f}"
        )
        shown += 1
        if shown >= limit:
            break


if __name__ == "__main__":
    total_spikes, inserted = run_8k_spike_detection()
    print(f"8-K monthly spikes found: {total_spikes}")
    print(f"Alerts inserted: {inserted}")

    with db_utils.get_conn() as conn:
        filings = fetch_8k_filings(conn)
        spikes = detect_monthly_spikes(filings)
        company_map = _fetch_company_map(conn)
    print_spike_summary(spikes, company_map)
