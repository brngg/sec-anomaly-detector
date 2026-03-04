"""Backfill daily issuer risk score snapshots across a historical date range."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import date, timedelta, timezone, datetime
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    repo_root = Path(__file__).resolve().parents[2]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

from dotenv import load_dotenv

from src.analysis.build_risk_scores import run_risk_scoring

REPO_ROOT = Path(__file__).resolve().parents[2]


def _resolve_start_date(explicit_start: str | None, explicit_days: int | None, end_day: date) -> date:
    if explicit_start:
        return date.fromisoformat(explicit_start)

    env_start = (os.getenv("BACKFILL_START_DATE") or "").strip()
    if env_start:
        return date.fromisoformat(env_start)

    if explicit_days is not None:
        if explicit_days <= 0:
            raise ValueError("--backfill-days must be positive")
        return end_day - timedelta(days=explicit_days)

    env_days = (os.getenv("BACKFILL_DAYS") or "").strip()
    if env_days:
        days = int(env_days)
        if days <= 0:
            raise ValueError("BACKFILL_DAYS must be positive")
        return end_day - timedelta(days=days)

    # Explicit default window for initial reconstruction.
    return end_day - timedelta(days=730)


def backfill_daily_scores(
    start_date: date,
    end_date: date,
    db_path: Path | None = None,
    progress_every: int = 25,
    scoring_mode: str | None = None,
    model_version: str | None = None,
    monthly_history_months: int | None = None,
) -> dict[str, Any]:
    if start_date > end_date:
        raise ValueError("start_date cannot be after end_date")

    total_days = (end_date - start_date).days + 1
    started_at = datetime.now(timezone.utc)
    perf_start = time.perf_counter()

    print(
        f"[backfill] starting range={start_date.isoformat()}..{end_date.isoformat()} "
        f"({total_days} days) progress_every={progress_every}",
        flush=True,
    )

    current = start_date
    days_processed = 0
    total_scores = 0
    last_stats: dict[str, Any] | None = None

    while current <= end_date:
        stats = run_risk_scoring(
            path=db_path,
            as_of_date=current.isoformat(),
            scoring_mode=scoring_mode,
            model_version=model_version,
            monthly_history_months=monthly_history_months,
        )
        days_processed += 1
        scores_upserted = int(stats.get("scores_upserted", 0))
        total_scores += scores_upserted
        last_stats = stats

        if progress_every > 0 and (
            days_processed == 1
            or days_processed % progress_every == 0
            or days_processed == total_days
        ):
            elapsed = time.perf_counter() - perf_start
            rate = days_processed / elapsed if elapsed > 0 else 0.0
            remaining_days = max(total_days - days_processed, 0)
            eta_seconds = int(remaining_days / rate) if rate > 0 else 0
            percent = (days_processed / total_days) * 100.0
            print(
                f"[backfill] {days_processed}/{total_days} ({percent:.1f}%) "
                f"last_as_of={stats.get('as_of_date')} "
                f"scores_upserted_today={scores_upserted} "
                f"elapsed={elapsed:.1f}s eta={eta_seconds}s",
                flush=True,
            )

        current += timedelta(days=1)

    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "started_at_utc": started_at.isoformat(),
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "total_days": total_days,
        "days_processed": days_processed,
        "total_scores_upserted": total_scores,
        "elapsed_seconds": round(time.perf_counter() - perf_start, 3),
        "last_day_stats": last_stats or {},
        "db_path": str(db_path) if db_path else "env:DATABASE_URL",
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Backfill daily issuer risk score snapshots.")
    parser.add_argument("--start-date", default=None, help="Start as_of date (YYYY-MM-DD)")
    parser.add_argument("--end-date", default=None, help="End as_of date (YYYY-MM-DD), default today UTC")
    parser.add_argument("--backfill-days", type=int, default=None, help="Alternative to start-date")
    parser.add_argument(
        "--db-path",
        default=None,
        help="Optional sqlite DB path override. Leave unset to use DB_BACKEND + DATABASE_URL env.",
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=25,
        help="Print progress every N processed days (0 to disable).",
    )
    parser.add_argument(
        "--scoring-mode",
        default=None,
        help="Optional scoring mode override (monthly_abnormal or alert_composite).",
    )
    parser.add_argument(
        "--model-version",
        default=None,
        help="Optional explicit model version label.",
    )
    parser.add_argument(
        "--monthly-history-months",
        type=int,
        default=None,
        help="Optional monthly history window; unset means all available history.",
    )
    return parser


def main() -> int:
    load_dotenv(REPO_ROOT / ".env", override=False)
    args = _build_parser().parse_args()
    end_day = date.fromisoformat(args.end_date) if args.end_date else datetime.now(timezone.utc).date()
    start_day = _resolve_start_date(args.start_date, args.backfill_days, end_day)

    summary = backfill_daily_scores(
        start_date=start_day,
        end_date=end_day,
        db_path=Path(args.db_path) if args.db_path else None,
        progress_every=args.progress_every,
        scoring_mode=args.scoring_mode,
        model_version=args.model_version,
        monthly_history_months=args.monthly_history_months,
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
