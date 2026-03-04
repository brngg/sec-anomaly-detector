"""Generate likely adverse outcome candidates from filing_events."""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable

import requests

if __package__ in {None, ""}:
    repo_root = Path(__file__).resolve().parents[2]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

from src.analysis.verify_outcomes import (
    CONFIDENCE_MEDIUM,
    CONFIDENCE_ORDER,
    ITEM_BY_FAMILY,
    STATUS_REJECTED,
    _build_url_candidates,
    _fetch_filing_text,
    _to_accession_with_dashes,
    _verify_text,
)
from src.db import db_utils

DEFAULT_FORMS = ("8-K", "8-K/A", "10-K/A", "10-Q/A")


def _normalize_forms(raw: Iterable[str]) -> tuple[str, ...]:
    normalized = sorted({value.strip().upper() for value in raw if value and value.strip()})
    return tuple(normalized)


def _normalize_confidence(raw: str) -> str:
    value = (raw or "").strip().upper()
    if value not in CONFIDENCE_ORDER:
        raise ValueError(f"Unsupported confidence band: {raw}")
    return value


def _fetch_filings(
    conn,
    forms: tuple[str, ...],
    date_from: str | None,
    date_to: str | None,
    max_rows: int | None,
    exclude_existing_outcomes: bool,
) -> list[dict[str, Any]]:
    if not forms:
        return []

    placeholders = ",".join("?" for _ in forms)
    where = [f"UPPER(f.filing_type) IN ({placeholders})"]
    params: list[Any] = [*forms]

    if date_from:
        where.append("f.filed_date >= ?")
        params.append(date_from)
    if date_to:
        where.append("f.filed_date <= ?")
        params.append(date_to)
    if exclude_existing_outcomes:
        where.append(
            """
            NOT EXISTS (
                SELECT 1
                FROM outcome_events o
                WHERE o.accession_id = f.accession_id
                   OR REPLACE(COALESCE(o.accession_id, ''), '-', '') = REPLACE(f.accession_id, '-', '')
            )
            """
        )

    where_sql = " AND ".join(where)
    sql = f"""
        SELECT
            f.cik,
            f.filed_date AS event_date,
            f.filing_type AS form,
            f.accession_id,
            COALESCE(f.primary_document, '') AS primary_document
        FROM filing_events f
        WHERE {where_sql}
        ORDER BY f.filed_date DESC, f.accession_id DESC
    """
    if max_rows is not None and max_rows > 0:
        sql += " LIMIT ?"
        params.append(max_rows)

    rows = conn.execute(sql, tuple(params)).fetchall()
    return [dict(row) for row in rows]


def generate_outcome_candidates(
    output_csv: Path,
    db_path: Path | None = None,
    forms: Iterable[str] = DEFAULT_FORMS,
    date_from: str | None = None,
    date_to: str | None = None,
    lookback_days: int = 120,
    max_rows: int | None = None,
    min_confidence: str = CONFIDENCE_MEDIUM,
    sec_identity: str = "",
    timeout_seconds: int = 20,
    sleep_seconds: float = 0.2,
    exclude_existing_outcomes: bool = True,
) -> dict[str, Any]:
    normalized_forms = _normalize_forms(forms)
    normalized_confidence = _normalize_confidence(min_confidence)

    if not date_to:
        date_to = date.today().isoformat()
    if not date_from:
        date_from = (date.fromisoformat(date_to) - timedelta(days=max(1, lookback_days))).isoformat()

    session = requests.Session()
    user_agent = sec_identity.strip() or "ReviewPriorityCandidateGenerator/1.0 (local)"
    session.headers.update({"User-Agent": user_agent})

    with db_utils.get_conn(path=db_path) as conn:
        filings = _fetch_filings(
            conn,
            forms=normalized_forms,
            date_from=date_from,
            date_to=date_to,
            max_rows=max_rows,
            exclude_existing_outcomes=exclude_existing_outcomes,
        )

    output_rows: list[dict[str, str]] = []
    rows_scanned = 0
    skipped_fetch_error = 0
    skipped_rejected = 0
    skipped_low_confidence = 0
    form_counts: dict[str, int] = {}
    confidence_counts: dict[str, int] = {}
    family_counts: dict[str, int] = {}

    for filing in filings:
        rows_scanned += 1
        cik = int(filing["cik"])
        event_date = str(filing["event_date"])
        form = str(filing["form"])
        accession_id = str(filing["accession_id"])
        accession_nodash = accession_id.replace("-", "")
        primary_document = str(filing.get("primary_document") or "")

        try:
            text, resolved_url = _fetch_filing_text(
                session=session,
                url_candidates=_build_url_candidates(
                    cik=cik,
                    accession_nodash=accession_nodash,
                    primary_document=primary_document,
                    existing_url="",
                ),
                timeout_seconds=timeout_seconds,
            )
        except requests.RequestException:
            skipped_fetch_error += 1
            if sleep_seconds > 0:
                time.sleep(sleep_seconds)
            continue

        status, confidence_band, outcome_family, outcome_type, _, reason = _verify_text(
            text=text,
            filing_form=form,
        )
        if status == STATUS_REJECTED or not confidence_band or not outcome_family or not outcome_type:
            skipped_rejected += 1
            if sleep_seconds > 0:
                time.sleep(sleep_seconds)
            continue
        if CONFIDENCE_ORDER[confidence_band] < CONFIDENCE_ORDER[normalized_confidence]:
            skipped_low_confidence += 1
            if sleep_seconds > 0:
                time.sleep(sleep_seconds)
            continue

        item = ITEM_BY_FAMILY.get(outcome_family, "")
        dedupe_key = f"{outcome_type}:{cik}:{event_date}:{accession_nodash}"
        description = (
            f"Candidate {form} matched {outcome_family} cues "
            f"({confidence_band}); verify before import"
        )
        output_rows.append(
            {
                "cik": str(cik),
                "event_date": event_date,
                "outcome_type": outcome_type,
                "source": "SEC EDGAR (candidate-prefiltered)",
                "description": description,
                "dedupe_key": dedupe_key,
                "form": form,
                "item": item,
                "url": resolved_url,
                "accession_id": _to_accession_with_dashes(accession_nodash),
                "primary_document": primary_document,
                "confidence_band": confidence_band,
                "outcome_family": outcome_family,
                "prefilter_reason": reason,
            }
        )
        form_counts[form] = form_counts.get(form, 0) + 1
        confidence_counts[confidence_band] = confidence_counts.get(confidence_band, 0) + 1
        family_counts[outcome_family] = family_counts.get(outcome_family, 0) + 1

        if sleep_seconds > 0:
            time.sleep(sleep_seconds)

    fieldnames = [
        "cik",
        "event_date",
        "outcome_type",
        "source",
        "description",
        "dedupe_key",
        "form",
        "item",
        "url",
        "accession_id",
        "primary_document",
        "confidence_band",
        "outcome_family",
        "prefilter_reason",
    ]
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    with output_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(output_rows)

    return {
        "output_csv": str(output_csv),
        "db_path": str(db_path) if db_path is not None else "env:DATABASE_URL",
        "rows_scanned": rows_scanned,
        "rows_written": len(output_rows),
        "skipped_fetch_error": skipped_fetch_error,
        "skipped_rejected": skipped_rejected,
        "skipped_low_confidence": skipped_low_confidence,
        "date_from": date_from,
        "date_to": date_to,
        "forms": list(normalized_forms),
        "min_confidence": normalized_confidence,
        "user_agent": user_agent,
        "form_counts": form_counts,
        "confidence_counts": confidence_counts,
        "outcome_family_counts": family_counts,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate likely adverse filing candidates for outcome verification."
    )
    parser.add_argument("--output", default="data/outcomes_candidates.csv", help="Output candidate CSV path")
    parser.add_argument(
        "--db-path",
        default=None,
        help="Optional sqlite DB path override. Leave unset to use DB_BACKEND + DATABASE_URL env.",
    )
    parser.add_argument(
        "--forms",
        default=",".join(DEFAULT_FORMS),
        help="Comma-separated filing forms to scan (default: 8-K,8-K/A,10-K/A,10-Q/A)",
    )
    parser.add_argument("--date-from", default=None, help="Start filed_date (YYYY-MM-DD)")
    parser.add_argument("--date-to", default=None, help="End filed_date (YYYY-MM-DD)")
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=120,
        help="Used only when --date-from omitted; scans [date_to - lookback_days, date_to].",
    )
    parser.add_argument("--max-rows", type=int, default=None, help="Optional max filings to scan")
    parser.add_argument(
        "--min-confidence",
        default=CONFIDENCE_MEDIUM,
        choices=sorted(CONFIDENCE_ORDER.keys()),
        help="Minimum confidence needed for candidate inclusion.",
    )
    parser.add_argument(
        "--sec-identity",
        default="",
        help="SEC-compliant User-Agent identity (or pass SEC_IDENTITY env value here)",
    )
    parser.add_argument("--timeout-seconds", type=int, default=20)
    parser.add_argument("--sleep-seconds", type=float, default=0.2)
    parser.add_argument(
        "--include-existing-outcomes",
        action="store_true",
        help="Include filings already present in outcome_events (disabled by default).",
    )
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    stats = generate_outcome_candidates(
        output_csv=Path(args.output),
        db_path=Path(args.db_path) if args.db_path else None,
        forms=tuple(x.strip() for x in args.forms.split(",") if x.strip()),
        date_from=args.date_from,
        date_to=args.date_to,
        lookback_days=args.lookback_days,
        max_rows=args.max_rows,
        min_confidence=args.min_confidence,
        sec_identity=args.sec_identity,
        timeout_seconds=args.timeout_seconds,
        sleep_seconds=args.sleep_seconds,
        exclude_existing_outcomes=not args.include_existing_outcomes,
    )
    print(json.dumps(stats, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
