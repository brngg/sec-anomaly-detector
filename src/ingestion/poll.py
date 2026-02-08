import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from edgar import get_current_filings, set_identity
from dotenv import load_dotenv

from src.db import db_utils

REPO_ROOT = Path(__file__).resolve().parents[2]


def _parse_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "y"}


def _stringify_dt(value) -> str:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


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
    total_matched = 0
    total_inserted = 0
    total_errors = 0

    with db_utils.get_conn() as db_conn:
        tracked_ciks = {int(row[0]) for row in db_conn.execute("SELECT cik FROM companies")}

        if not tracked_ciks:
            print("No tracked companies found in DB. Exiting.")
            return 1

        current_filings = get_current_filings(
            form=["8-K", "10-K", "10-Q", "8-K/A", "10-Q/A", "10-K/A"],
            page_size=None,
        )

        if not current_filings:
            print("No recent filings found.")
            return 0

        print(f"Tracked companies: {len(tracked_ciks)}")

        for current_filing in current_filings:
            total_seen += 1
            try:
                filing_cik = int(current_filing.cik)
            except Exception:
                continue

            if filing_cik not in tracked_ciks:
                continue

            total_matched += 1

            filed_at = current_filing.acceptance_datetime or current_filing.filing_date
            filed_at_str = _stringify_dt(filed_at)
            filed_date_str = _stringify_dt(current_filing.filing_date)

            if dry_run:
                total_inserted += 1
                print(f"DRY_RUN would insert {current_filing.accession_no} ({filing_cik})")
                continue

            try:
                changes_before_insert = db_conn.total_changes

                db_utils.insert_filing(
                    db_conn,
                    current_filing.accession_no,
                    filing_cik,
                    current_filing.form,
                    filed_at_str,
                    filed_date_str,
                    current_filing.primary_document or None,
                )

                if db_conn.total_changes > changes_before_insert:
                    total_inserted += 1
                    db_utils.update_watermark(
                        db_conn,
                        cik=filing_cik,
                        last_seen_filed_at=filed_date_str,
                        last_run_at=datetime.now().isoformat(),
                        last_run_status="SUCCESS",
                        last_error=None,
                    )
                    print(f"Inserted {current_filing.accession_no} ({filing_cik})")
            except Exception as e:
                total_errors += 1
                db_utils.update_watermark(
                    db_conn,
                    cik=filing_cik,
                    last_run_at=datetime.now().isoformat(),
                    last_run_status="FAIL",
                    last_error=str(e),
                )
                print(f"Error processing {current_filing.accession_no} ({filing_cik}): {e}")

    end_time = datetime.now()
    elapsed = (end_time - start_time).total_seconds()
    print(f"Completed at {end_time.isoformat()} (elapsed {elapsed:.2f}s)")
    print(
        "Summary: "
        f"seen={total_seen} matched={total_matched} inserted={total_inserted} errors={total_errors}"
    )

    return 0 if total_errors == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
