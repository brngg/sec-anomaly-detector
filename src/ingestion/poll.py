import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from edgar import Company, set_identity
from dotenv import load_dotenv

from src.db import db_utils
from src.detection.run_all import run_all_detections

REPO_ROOT = Path(__file__).resolve().parents[2]
FORM_TYPES = [
    "8-K",
    "10-K",
    "10-Q",
    "8-K/A",
    "10-Q/A",
    "10-K/A",
    "NT 10-K",
    "NT 10-Q",
]
DEFAULT_LOOKBACK_DAYS = int(os.getenv("POLL_LOOKBACK_DAYS", "14"))
SLEEP_SECONDS = float(os.getenv("POLL_SLEEP_SECONDS", "0.11"))


def _parse_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "y"}


def _stringify_dt(value) -> str:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)

def _parse_dt(value: str) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if len(text) == 10:
        dt = datetime.fromisoformat(text).replace(tzinfo=timezone.utc)
        return dt
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _resolve_last_seen(existing: str | None, new: str | None) -> str | None:
    if not existing:
        return new
    if not new:
        return existing
    existing_dt = _parse_dt(existing)
    new_dt = _parse_dt(new)
    if existing_dt is None:
        return new
    if new_dt is None:
        return existing
    return new if new_dt > existing_dt else existing


def _since_date(last_seen: str | None) -> str:
    if last_seen:
        parsed = _parse_dt(last_seen)
        if parsed:
            return parsed.date().isoformat()
        return last_seen[:10]
    fallback = datetime.now(timezone.utc) - timedelta(days=DEFAULT_LOOKBACK_DAYS)
    return fallback.date().isoformat()


def main() -> int:
    load_dotenv(REPO_ROOT / ".env", override=True)

    sec_identity = os.getenv("SEC_IDENTITY", "").strip()
    if not sec_identity:
        print("SEC_IDENTITY not set. Please set it before running.")
        return 1

    set_identity(sec_identity)

    dry_run = _parse_bool(os.getenv("DRY_RUN", ""))

    start_time = datetime.now()
    print(f"Starting poll at {start_time.isoformat()}")

    total_seen = 0
    total_inserted = 0
    total_errors = 0

    with db_utils.get_conn() as db_conn:
        rows = db_conn.execute(
            """
            SELECT c.cik, c.ticker, w.last_seen_filed_at
            FROM companies c
            LEFT JOIN watermarks w ON w.cik = c.cik
            ORDER BY c.cik
            """
        ).fetchall()

        if not rows:
            print("No tracked companies found in DB. Exiting.")
            return 1

        print(f"Tracked companies: {len(rows)}")

        for index, row in enumerate(rows, 1):
            cik = int(row["cik"])
            ticker = row["ticker"]
            last_seen = row["last_seen_filed_at"]
            since_date = _since_date(last_seen)
            label = ticker or str(cik)

            fetched = 0
            inserted = 0
            max_filed_at = None

            try:
                company = Company(label)
                filings = company.get_filings(form=FORM_TYPES).filter(date=f"{since_date}:")
                for filing in filings:
                    fetched += 1
                    total_seen += 1
                    filed_at = filing.acceptance_datetime or filing.filing_date
                    filed_at_str = _stringify_dt(filed_at)
                    filed_date_str = _stringify_dt(filing.filing_date)

                    max_filed_at = _resolve_last_seen(max_filed_at, filed_at_str)

                    if dry_run:
                        inserted += 1
                        total_inserted += 1
                        continue

                    changes_before_insert = db_conn.total_changes
                    db_utils.insert_filing(
                        db_conn,
                        filing.accession_no,
                        cik,
                        filing.form,
                        filed_at_str,
                        filed_date_str,
                        filing.primary_document or None,
                    )

                    if db_conn.total_changes > changes_before_insert:
                        inserted += 1
                        total_inserted += 1

                if not dry_run:
                    resolved_last_seen = _resolve_last_seen(last_seen, max_filed_at)
                    db_utils.update_watermark(
                        db_conn,
                        cik=cik,
                        last_seen_filed_at=resolved_last_seen,
                        last_run_at=datetime.now().isoformat(),
                        last_run_status="SUCCESS",
                        last_error=None,
                    )
            except Exception as e:
                total_errors += 1
                if not dry_run:
                    db_utils.update_watermark(
                        db_conn,
                        cik=cik,
                        last_seen_filed_at=last_seen,
                        last_run_at=datetime.now().isoformat(),
                        last_run_status="FAIL",
                        last_error=str(e),
                    )
                print(f"Error processing {label}: {e}")
            finally:
                print(f"[{index}/{len(rows)}] {label}: fetched={fetched} inserted={inserted}")
                time.sleep(SLEEP_SECONDS)

    end_time = datetime.now()
    elapsed = (end_time - start_time).total_seconds()
    print(f"Completed at {end_time.isoformat()} (elapsed {elapsed:.2f}s)")
    print(
        "Summary: "
        f"seen={total_seen} inserted={total_inserted} errors={total_errors}"
    )

    if not dry_run and total_inserted > 0:
        print("New filings inserted; running detectors...")
        run_all_detections()
    else:
        print("No new filings inserted; skipping detectors.")

    return 0 if total_errors == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
