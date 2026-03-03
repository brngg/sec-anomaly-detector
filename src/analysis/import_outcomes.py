"""Import curated outcome labels into outcome_events for validation."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    repo_root = Path(__file__).resolve().parents[2]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

from src.db import db_utils

DEFAULT_OUTCOME_TYPE = "RESTATEMENT_DISCLOSURE"


def _parse_date(value: str) -> str:
    return date.fromisoformat(value.strip()).isoformat()


def _parse_cik(value: str) -> int:
    cik = int(value)
    if cik <= 0:
        raise ValueError("cik must be positive")
    return cik


def _row_metadata(row: dict[str, str], ignore: set[str]) -> dict[str, Any]:
    return {
        key: value
        for key, value in row.items()
        if key not in ignore and value not in {None, ""}
    }


def import_outcomes(
    csv_path: Path,
    path: Path = db_utils.DB_PATH,
    default_outcome_type: str = DEFAULT_OUTCOME_TYPE,
    default_source: str = "CURATED_CSV",
) -> dict[str, int | str]:
    required = {"cik", "event_date"}
    inserted = 0
    skipped = 0
    invalid = 0

    with csv_path.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError("CSV must include a header row")

        missing = required - set(reader.fieldnames)
        if missing:
            raise ValueError(f"CSV missing required columns: {sorted(missing)}")

        with db_utils.get_conn(path=path) as conn:
            for row in reader:
                try:
                    cik = _parse_cik(row.get("cik", ""))
                    event_date = _parse_date(row.get("event_date", ""))
                    outcome_type = (row.get("outcome_type") or default_outcome_type).strip()
                    source = (row.get("source") or default_source).strip()
                    description = (row.get("description") or "").strip() or None

                    metadata = _row_metadata(
                        row,
                        ignore={"cik", "event_date", "outcome_type", "source", "description", "dedupe_key"},
                    )
                    dedupe_key = (row.get("dedupe_key") or "").strip() or None
                    created = db_utils.insert_outcome_event(
                        conn,
                        cik=cik,
                        event_date=event_date,
                        outcome_type=outcome_type,
                        source=source,
                        description=description,
                        metadata=metadata,
                        dedupe_key=dedupe_key,
                    )
                    if created:
                        inserted += 1
                    else:
                        skipped += 1
                except Exception:
                    invalid += 1

    return {
        "csv_path": str(csv_path),
        "inserted": inserted,
        "skipped": skipped,
        "invalid": invalid,
        "default_outcome_type": default_outcome_type,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Import curated outcome labels into outcome_events.")
    parser.add_argument("--input", required=True, help="Path to CSV with at least cik,event_date columns")
    parser.add_argument(
        "--db-path",
        default=str(db_utils.DB_PATH),
        help="SQLite DB path (defaults to project DB)",
    )
    parser.add_argument(
        "--default-outcome-type",
        default=DEFAULT_OUTCOME_TYPE,
        help="Outcome type used when row value is missing",
    )
    parser.add_argument(
        "--default-source",
        default="CURATED_CSV",
        help="Source value used when row value is missing",
    )
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    stats = import_outcomes(
        csv_path=Path(args.input),
        path=Path(args.db_path),
        default_outcome_type=args.default_outcome_type,
        default_source=args.default_source,
    )
    print(json.dumps(stats, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
