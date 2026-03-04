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
from src.db.init_db import create_db

DEFAULT_OUTCOME_TYPE = "RESTATEMENT_DISCLOSURE"
CONFIDENCE_ORDER = {
    "LOW": 1,
    "MEDIUM": 2,
    "HIGH": 3,
}


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


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _row_confidence_band(row: dict[str, str]) -> str | None:
    explicit = _optional_text(row.get("confidence_band"))
    if explicit:
        upper = explicit.upper()
        if upper in CONFIDENCE_ORDER:
            return upper

    status = (_optional_text(row.get("verification_status")) or "").upper()
    if status == "VERIFIED_HIGH":
        return "HIGH"
    if status == "VERIFIED_MEDIUM":
        return "MEDIUM"
    if status == "POSSIBLE":
        return "LOW"
    return None


def _meets_min_confidence(row: dict[str, str], min_confidence: str | None) -> bool:
    if min_confidence is None:
        return True
    confidence = _row_confidence_band(row)
    if confidence is None:
        return False
    return CONFIDENCE_ORDER[confidence] >= CONFIDENCE_ORDER[min_confidence.upper()]


def import_outcomes(
    csv_path: Path,
    path: Path | None = None,
    default_outcome_type: str = DEFAULT_OUTCOME_TYPE,
    default_source: str = "CURATED_CSV",
    min_confidence: str | None = None,
) -> dict[str, int | str]:
    required = {"cik", "event_date"}
    inserted = 0
    skipped = 0
    invalid = 0
    filtered = 0

    # Ensure latest schema/migrations exist for normalized outcome columns.
    create_db(path=path, reset=False)

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
                    if not _meets_min_confidence(row, min_confidence=min_confidence):
                        filtered += 1
                        continue

                    cik = _parse_cik(row.get("cik", ""))
                    event_date = _parse_date(row.get("event_date", ""))
                    outcome_type = (row.get("outcome_type") or default_outcome_type).strip()
                    source = (row.get("source") or default_source).strip()
                    description = (row.get("description") or "").strip() or None
                    form = _optional_text(row.get("form"))
                    item = _optional_text(row.get("item"))
                    accession_id = _optional_text(row.get("accession_id"))
                    filing_url = _optional_text(row.get("filing_url")) or _optional_text(row.get("url"))
                    verification_status = _optional_text(row.get("verification_status"))
                    verification_reason = _optional_text(row.get("verification_reason"))

                    metadata = _row_metadata(
                        row,
                        ignore={
                            "cik",
                            "event_date",
                            "outcome_type",
                            "source",
                            "description",
                            "dedupe_key",
                            "form",
                            "item",
                            "accession_id",
                            "url",
                            "filing_url",
                            "verification_status",
                            "verification_reason",
                            "confidence_band",
                            "outcome_family",
                        },
                    )
                    if form is not None:
                        metadata["form"] = form
                    if item is not None:
                        metadata["item"] = item
                    if accession_id is not None:
                        metadata["accession_id"] = accession_id
                    if filing_url is not None:
                        metadata["url"] = filing_url
                    if verification_status is not None:
                        metadata["verification_status"] = verification_status
                    if verification_reason is not None:
                        metadata["verification_reason"] = verification_reason

                    dedupe_key = (row.get("dedupe_key") or "").strip() or None
                    created = db_utils.insert_outcome_event(
                        conn,
                        cik=cik,
                        event_date=event_date,
                        outcome_type=outcome_type,
                        source=source,
                        description=description,
                        form=form,
                        item=item,
                        accession_id=accession_id,
                        filing_url=filing_url,
                        verification_status=verification_status,
                        verification_reason=verification_reason,
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
        "filtered": filtered,
        "min_confidence": min_confidence,
        "default_outcome_type": default_outcome_type,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Import curated outcome labels into outcome_events.")
    parser.add_argument("--input", required=True, help="Path to CSV with at least cik,event_date columns")
    parser.add_argument(
        "--db-path",
        default=None,
        help="Optional sqlite DB path override. Leave unset to use DB_BACKEND + DATABASE_URL env.",
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
    parser.add_argument(
        "--min-confidence",
        default=None,
        choices=["HIGH", "MEDIUM", "LOW"],
        help="Only import rows at or above this confidence band",
    )
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    stats = import_outcomes(
        csv_path=Path(args.input),
        path=Path(args.db_path) if args.db_path else None,
        default_outcome_type=args.default_outcome_type,
        default_source=args.default_source,
        min_confidence=args.min_confidence,
    )
    print(json.dumps(stats, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
