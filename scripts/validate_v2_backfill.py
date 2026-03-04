#!/usr/bin/env python3
"""Validate v2 monthly-abnormal score coverage and sample issuer trends."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

from dotenv import load_dotenv

from src.db import db_utils

REPO_ROOT = Path(__file__).resolve().parents[1]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate coverage and continuity for a risk-score model version."
    )
    parser.add_argument(
        "--model-version",
        default=os.getenv("RISK_DEFAULT_MODEL_VERSION", "v2_monthly_abnormal"),
        help="Model version to validate.",
    )
    parser.add_argument(
        "--expected-issuers",
        type=int,
        default=None,
        help="Expected issuer count per as_of_date. Defaults to tracked companies count.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero if daily issuer coverage gaps are found.",
    )
    parser.add_argument(
        "--case-tickers",
        default="COF,UBER,AMD",
        help="Comma-separated tickers for MoM case-study output.",
    )
    parser.add_argument(
        "--latest-days",
        type=int,
        default=14,
        help="How many most recent as_of_date rows to include in daily coverage summary.",
    )
    return parser.parse_args()


def _to_iso(value: Any) -> str:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _month_key(as_of: str) -> str:
    return as_of[:7]


def _build_case_studies(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        ticker = row.get("ticker") or "UNKNOWN"
        grouped[ticker].append(
            {
                "as_of_date": _to_iso(row["as_of_date"]),
                "risk_score": float(row["risk_score"]),
                "risk_rank": int(row["risk_rank"]) if row["risk_rank"] is not None else None,
            }
        )

    result: dict[str, list[dict[str, Any]]] = {}
    for ticker, series in grouped.items():
        series.sort(key=lambda item: item["as_of_date"])
        latest_per_month: list[dict[str, Any]] = []
        by_month: dict[str, dict[str, Any]] = {}
        for item in series:
            by_month[_month_key(item["as_of_date"])] = item

        for month in sorted(by_month.keys()):
            latest_per_month.append({**by_month[month], "month": month})

        for index, item in enumerate(latest_per_month):
            if index == 0:
                item["mom_delta"] = None
                continue
            prev = latest_per_month[index - 1]
            item["mom_delta"] = round(float(item["risk_score"]) - float(prev["risk_score"]), 6)

        result[ticker] = latest_per_month[-12:]
    return result


def main() -> int:
    load_dotenv(REPO_ROOT / ".env", override=False)
    args = _parse_args()
    case_tickers = [ticker.strip().upper() for ticker in args.case_tickers.split(",") if ticker.strip()]

    with db_utils.get_conn(path=None) as conn:
        model_row = conn.execute(
            """
            SELECT
                COUNT(*) AS rows_total,
                COUNT(DISTINCT as_of_date) AS days_total,
                MIN(as_of_date) AS min_as_of,
                MAX(as_of_date) AS max_as_of
            FROM issuer_risk_scores
            WHERE model_version = ?
            """,
            (args.model_version,),
        ).fetchone()

        if model_row is None or int(model_row["rows_total"] or 0) == 0:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "error": f"No rows found for model_version={args.model_version}",
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            return 1

        expected_issuers = args.expected_issuers
        if expected_issuers is None:
            expected_issuers = int(
                conn.execute("SELECT COUNT(*) AS n FROM companies").fetchone()["n"]
            )

        coverage_rows = conn.execute(
            """
            SELECT as_of_date, COUNT(*) AS issuer_count
            FROM issuer_risk_scores
            WHERE model_version = ?
            GROUP BY as_of_date
            ORDER BY as_of_date
            """,
            (args.model_version,),
        ).fetchall()

        coverage_daily = [
            {
                "as_of_date": _to_iso(row["as_of_date"]),
                "issuer_count": int(row["issuer_count"]),
            }
            for row in coverage_rows
        ]
        days_with_gaps = [
            row for row in coverage_daily if int(row["issuer_count"]) != int(expected_issuers)
        ]

        non_zero_rows = conn.execute(
            """
            SELECT
                as_of_date,
                COUNT(*) FILTER (WHERE risk_score > 0) AS non_zero_scores,
                COUNT(*) AS total_issuers
            FROM issuer_risk_scores
            WHERE model_version = ?
            GROUP BY as_of_date
            ORDER BY as_of_date DESC
            LIMIT ?
            """,
            (args.model_version, args.latest_days),
        ).fetchall()
        non_zero_daily = [
            {
                "as_of_date": _to_iso(row["as_of_date"]),
                "non_zero_scores": int(row["non_zero_scores"]),
                "total_issuers": int(row["total_issuers"]),
            }
            for row in non_zero_rows
        ]

        case_rows: list[dict[str, Any]] = []
        if case_tickers:
            placeholders = ",".join("?" for _ in case_tickers)
            case_rows = [
                dict(row)
                for row in conn.execute(
                    f"""
                    SELECT
                        s.cik,
                        c.ticker,
                        s.as_of_date,
                        s.risk_score,
                        s.risk_rank
                    FROM issuer_risk_scores s
                    JOIN companies c ON c.cik = s.cik
                    WHERE s.model_version = ?
                      AND c.ticker IN ({placeholders})
                    ORDER BY c.ticker, s.as_of_date
                    """,
                    (args.model_version, *case_tickers),
                ).fetchall()
            ]

        report = {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "ok": not days_with_gaps,
            "model_version": args.model_version,
            "expected_issuers_per_day": expected_issuers,
            "coverage": {
                "rows_total": int(model_row["rows_total"]),
                "days_total": int(model_row["days_total"]),
                "min_as_of": _to_iso(model_row["min_as_of"]),
                "max_as_of": _to_iso(model_row["max_as_of"]),
                "days_with_issuer_gaps": days_with_gaps,
                "latest_daily_counts": coverage_daily[-args.latest_days :],
            },
            "non_zero_distribution_latest_days": non_zero_daily,
            "case_studies_month_end": _build_case_studies(case_rows),
        }
        print(json.dumps(report, indent=2, sort_keys=True))

    if args.strict and days_with_gaps:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
