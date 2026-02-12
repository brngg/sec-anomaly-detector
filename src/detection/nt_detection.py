"""Detect non-timely (NT) filings and create alerts."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.db import db_utils


ANOMALY_TYPE = "NT_FILING"
DEFAULT_SEVERITY = 0.7  # Base severity for NT filings, can be adjusted based on filing type or company if desired.

# ADD-ON NOTE:
# - NT filings are flagged as anomalies as-is.
# - Potential add ons to consider in the future:
#   - Grace-period logic to reduce severity if a follow-up filing arrives quickly.
#   - Age-based decay applied in aggregation/UI (not during alert creation).


@dataclass(frozen=True)
class NTFiling:
    accession_id: str
    cik: int
    filing_type: str
    filed_at: str
    filed_date: str
    company_name: Optional[str]
    company_ticker: Optional[str]


def score_nt_filing(filing_type: str) -> float:
    """
    Score NT filings on 0.0-1.0 scale.
    
    Scoring philosophy:
    - 0.9-1.0: Critical (requires immediate attention)
    - 0.7-0.9: High (investigate within 24 hours)
    - 0.5-0.7: Medium (monitor closely)
    - 0.3-0.5: Low (informational)
    """
    base = {
        "NT 10-K": 0.90,  # Annual financials - CRITICAL
        "NT 10-Q": 0.75,  # Quarterly financials - HIGH
        "NT 20-F": 0.90,  # Foreign annual report - CRITICAL
        "NT-NCSR": 0.65,  # Investment company - MEDIUM
    }
    
    # Default for any other NT form
    return base.get(filing_type, 0.70)


def fetch_nt_filings(conn) -> List[NTFiling]:
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
        WHERE f.filing_type LIKE 'NT %'
        ORDER BY f.filed_at DESC
        """
    ).fetchall()

    return [
        NTFiling(
            accession_id=row["accession_id"],
            cik=row["cik"],
            filing_type=row["filing_type"],
            filed_at=row["filed_at"],
            filed_date=row["filed_date"],
            company_name=row["company_name"],
            company_ticker=row["company_ticker"],
        )
        for row in rows
    ]


def _insert_alert(conn, nt_filing: NTFiling, severity: float = DEFAULT_SEVERITY) -> bool:
    details = {
        "cik": nt_filing.cik,
        "company_name": nt_filing.company_name,
        "company_ticker": nt_filing.company_ticker,
        "filing_type": nt_filing.filing_type,
        "filed_at": nt_filing.filed_at,
        "filed_date": nt_filing.filed_date,
    }
    description = f"{nt_filing.filing_type} non-timely filing notice"
    dedupe_key = f"{ANOMALY_TYPE}:{nt_filing.accession_id}"

    changes_before = conn.total_changes
    conn.execute(
        """
        INSERT OR IGNORE INTO alerts (
            accession_id,
            anomaly_type,
            severity_score,
            description,
            details,
            status,
            dedupe_key
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            nt_filing.accession_id,
            ANOMALY_TYPE,
            severity,
            description,
            json.dumps(details, sort_keys=True),
            "OPEN",
            dedupe_key,
        ),
    )
    return conn.total_changes > changes_before


def run_nt_detection(severity: float = DEFAULT_SEVERITY) -> Tuple[int, int]:
    """Insert alerts for all NT filings. Returns (total_nt, inserted_alerts)."""
    with db_utils.get_conn() as conn:
        nt_filings = fetch_nt_filings(conn)

        inserted = 0
        for nt_filing in nt_filings:
            filing_severity = score_nt_filing(nt_filing.filing_type)
            if _insert_alert(conn, nt_filing, severity=filing_severity):
                inserted += 1

    return len(nt_filings), inserted


def print_nt_summary(limit: int = 10) -> None:
    with db_utils.get_conn() as conn:
        rows = conn.execute(
            """
            SELECT
                f.cik,
                c.ticker,
                c.name,
                COUNT(*) AS nt_count
            FROM filing_events f
            LEFT JOIN companies c ON c.cik = f.cik
            WHERE f.filing_type LIKE 'NT %'
            GROUP BY f.cik, c.ticker, c.name
            ORDER BY nt_count DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    print("Top companies by NT filing count:")
    for row in rows:
        print(f"  {row['ticker'] or 'N/A'} | {row['name'] or 'Unknown'} | {row['nt_count']}")


if __name__ == "__main__":
    total_nt, inserted = run_nt_detection()
    print(f"NT filings found: {total_nt}")
    print(f"Alerts inserted: {inserted}")
    print_nt_summary()
