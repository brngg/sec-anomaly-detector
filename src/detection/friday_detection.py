"""Detect Friday after-hours filings (aka "Friday burying")."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.db import db_utils
from src.detection.alerts import insert_alert


ANOMALY_TYPE = "FRIDAY_BURYING"
DEFAULT_SEVERITY = 0.65

# Restrict to 8-Ks for MVP. Add "10-K" / "10-Q" if you want broader coverage.
TARGET_FORMS = {"8-K", "8-K/A", "10-K", "10-K/A", "10-Q", "10-Q/A"}

ET = ZoneInfo("US/Eastern")


@dataclass(frozen=True)
class FridayFiling:
    accession_id: str
    cik: int
    filing_type: str
    filed_at: str
    filed_date: str
    company_name: Optional[str]
    company_ticker: Optional[str]


def _parse_utc(ts: str) -> datetime:
    """Parse ISO-ish timestamps into UTC datetimes."""
    if not ts:
        return datetime.now(timezone.utc)
    text = ts.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _is_friday_burying(filed_at: str) -> bool:
    dt_utc = _parse_utc(filed_at)
    dt_et = dt_utc.astimezone(ET)
    is_friday = dt_et.weekday() == 4  # Monday=0 ... Friday=4
    is_after_hours = dt_et.hour >= 16  # 4pm ET or later
    return is_friday and is_after_hours


def score_friday_burying() -> float:
    """Simple fixed score for MVP."""
    return DEFAULT_SEVERITY


def fetch_friday_filings(conn) -> List[FridayFiling]:
    rows = conn.execute(
        """
        SELECT
            f.accession_id,
            f.cik,
            f.filing_type,
            f.filed_at,
            f.filed_date,
            c.name AS company_name,
            c.ticker AS company_ticker
        FROM filing_events f
        LEFT JOIN companies c ON c.cik = f.cik
        WHERE f.filing_type IN ({})
        ORDER BY f.filed_at DESC
        """.format(",".join("?" * len(TARGET_FORMS))),
        tuple(TARGET_FORMS),
    ).fetchall()

    filings: List[FridayFiling] = []
    for row in rows:
        if not _is_friday_burying(row["filed_at"]):
            continue

        filing = FridayFiling(
            accession_id=row["accession_id"],
            cik=row["cik"],
            filing_type=row["filing_type"],
            filed_at=row["filed_at"],
            filed_date=row["filed_date"],
            company_name=row["company_name"],
            company_ticker=row["company_ticker"],
        )
        filings.append(filing)

    return filings


def run_friday_detection() -> Tuple[int, int]:
    """Insert alerts for Friday after-hours filings."""
    with db_utils.get_conn() as conn:
        filings = fetch_friday_filings(conn)

        inserted = 0
        for filing in filings:
            severity = score_friday_burying()
            details = {
                "cik": filing.cik,
                "company_name": filing.company_name,
                "company_ticker": filing.company_ticker,
                "filing_type": filing.filing_type,
                "filed_at": filing.filed_at,
                "filed_date": filing.filed_date,
            }
            description = "Friday after-hours filing (US/Eastern)"

            if insert_alert(
                conn,
                accession_id=filing.accession_id,
                anomaly_type=ANOMALY_TYPE,
                severity_score=severity,
                description=description,
                details=details,
            ):
                inserted += 1

    return len(filings), inserted


def print_friday_summary(limit: int = 10) -> None:
    with db_utils.get_conn() as conn:
        rows = conn.execute(
            """
            SELECT
                f.cik,
                c.ticker,
                c.name,
                f.filed_at
            FROM filing_events f
            LEFT JOIN companies c ON c.cik = f.cik
            WHERE f.filing_type IN ({})
            ORDER BY f.filed_at DESC
            """.format(",".join("?" * len(TARGET_FORMS))),
            tuple(TARGET_FORMS),
        ).fetchall()

    counts = {}
    for row in rows:
        if not _is_friday_burying(row["filed_at"]):
            continue
        key = (row["cik"], row["ticker"], row["name"])
        counts[key] = counts.get(key, 0) + 1

    top = sorted(counts.items(), key=lambda item: item[1], reverse=True)[:limit]
    print("Top companies by Friday after-hours filings:")
    for (cik, ticker, name), count in top:
        print(f"  {ticker or 'N/A'} | {name or 'Unknown'} | {count}")


if __name__ == "__main__":
    total, inserted = run_friday_detection()
    print(f"Friday after-hours filings found: {total}")
    print(f"Alerts inserted: {inserted}")
    print_friday_summary()
