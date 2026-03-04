#!/usr/bin/env python3
"""Export a pre-migration SQLite baseline snapshot."""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = REPO_ROOT / "data" / "sec_anomaly.db"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "docs" / "reports" / "baseline"
CORE_TABLES = [
    "companies",
    "filing_events",
    "watermarks",
    "alerts",
    "feature_snapshots",
    "issuer_risk_scores",
    "outcome_events",
    "poll_state",
]


def _row_counts(conn: sqlite3.Connection) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table in CORE_TABLES:
        try:
            counts[table] = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        except sqlite3.Error:
            counts[table] = -1
    return counts


def _latest_top_10(conn: sqlite3.Connection) -> dict[str, Any]:
    row = conn.execute("SELECT MAX(as_of_date) FROM issuer_risk_scores").fetchone()
    as_of_date = row[0] if row else None
    if not as_of_date:
        return {"as_of_date": None, "top10": []}

    top_rows = conn.execute(
        """
        SELECT cik, model_version, risk_score, risk_rank, percentile
        FROM issuer_risk_scores
        WHERE as_of_date = ?
        ORDER BY
            CASE WHEN risk_rank IS NULL THEN 1 ELSE 0 END,
            risk_rank ASC,
            risk_score DESC,
            cik ASC
        LIMIT 10
        """,
        (as_of_date,),
    ).fetchall()
    top = [
        {
            "cik": int(row[0]),
            "model_version": row[1],
            "risk_score": float(row[2]),
            "risk_rank": int(row[3]) if row[3] is not None else None,
            "percentile": float(row[4]) if row[4] is not None else None,
        }
        for row in top_rows
    ]
    return {"as_of_date": as_of_date, "top10": top}


def _latest_validation_reports(repo_root: Path, limit: int = 10) -> list[str]:
    report_roots = [repo_root / "docs" / "reports", repo_root / "docs" / "reports" / "validation"]
    paths: list[Path] = []
    for root in report_roots:
        if not root.exists():
            continue
        paths.extend([p for p in root.rglob("*.json")])
        paths.extend([p for p in root.rglob("*.md")])
    unique: dict[str, Path] = {str(path.resolve()): path for path in paths}
    sorted_paths = sorted(unique.values(), key=lambda p: p.stat().st_mtime, reverse=True)
    return [str(path.relative_to(repo_root)) for path in sorted_paths[:limit]]


def export_baseline(db_path: Path, output_path: Path | None = None) -> dict[str, Any]:
    conn = sqlite3.connect(db_path)
    try:
        payload = {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "db_path": str(db_path),
            "row_counts": _row_counts(conn),
            "latest_risk_snapshot": _latest_top_10(conn),
            "latest_validation_report_paths": _latest_validation_reports(REPO_ROOT),
        }
    finally:
        conn.close()

    if output_path is None:
        DEFAULT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        output_path = DEFAULT_OUTPUT_DIR / f"sqlite_baseline_{stamp}.json"
    else:
        output_path.parent.mkdir(parents=True, exist_ok=True)

    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    payload["output_path"] = str(output_path)
    return payload


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export baseline metrics from sqlite archive DB.")
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH), help="Path to sqlite archive DB")
    parser.add_argument("--output", default=None, help="Optional output json path")
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    payload = export_baseline(
        db_path=Path(args.db_path),
        output_path=Path(args.output) if args.output else None,
    )
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
