import csv
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from edgar import Company, set_identity
from dotenv import load_dotenv
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from src.db import db_utils

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_COMPANIES_CSV = REPO_ROOT / "data" / "companies.csv"


def _parse_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "y"}


def _stringify_dt(value) -> str:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def load_tickers(csv_path: Path) -> list[str]:
    if not csv_path.exists():
        print(f"❌ Companies CSV not found: {csv_path}")
        sys.exit(1)

    with csv_path.open("r", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames or "ticker" not in reader.fieldnames:
            print("❌ Companies CSV must include a 'ticker' header.")
            sys.exit(1)

        tickers: list[str] = []
        seen: set[str] = set()
        for row in reader:
            raw = (row.get("ticker") or "").strip()
            if not raw:
                continue
            normalized = raw.upper()
            if normalized in seen:
                continue
            seen.add(normalized)
            tickers.append(normalized)

    if not tickers:
        print(f"❌ No valid tickers found in {csv_path}")
        sys.exit(1)

    return tickers


@retry(
    retry=retry_if_exception_type(Exception),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=4),
    reraise=True,
)
def fetch_company(ticker: str) -> Company:
    return Company(ticker)


@retry(
    retry=retry_if_exception_type(Exception),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=4),
    reraise=True,
)
def fetch_filings(company: Company, date_filter: str):
    return company.get_filings(form=["8-K", "10-K", "10-Q"]).filter(date=date_filter)


def main() -> int:
    load_dotenv()
    sec_identity = os.getenv("SEC_IDENTITY", "").strip()
    if sec_identity:
        set_identity(sec_identity)
    else:
        print("⚠️  SEC_IDENTITY not set. Falling back to default identity.")
        set_identity("Brandon Cheng chengbr3@gmail.com")

    companies_csv = Path(os.getenv("COMPANIES_CSV", str(DEFAULT_COMPANIES_CSV)))
    tickers = load_tickers(companies_csv)

    dry_run = _parse_bool(os.getenv("DRY_RUN", ""))

    six_months_ago = datetime.now() - timedelta(days=180)
    date_filter = f"{six_months_ago.strftime('%Y-%m-%d')}:"

    total = len(tickers)
    start_time = datetime.now()
    print(f"Starting backfill at {start_time.isoformat()}")
    print(f"Companies: {total} | Dry run: {dry_run}")

    total_fetched = 0
    total_inserted = 0
    failures: list[str] = []

    def run_for_ticker(conn, ticker: str, index: int) -> tuple[int, int]:
        print(f"[{index}/{total}] Syncing {ticker}...")
        start = datetime.now()

        company = fetch_company(ticker)

        if not dry_run:
            db_utils.update_watermark(
                conn,
                cik=company.cik,
                last_run_at=start.isoformat(),
                last_run_status="RUNNING",
                last_error=None,
            )

        try:
            if not dry_run:
                db_utils.upsert_company(
                    conn,
                    company.cik,
                    company.name,
                    ticker,
                    company.industry,
                )

            filings = fetch_filings(company, date_filter)

            fetched = 0
            inserted = 0
            max_filed_at = None
            for filing in filings:
                fetched += 1
                filed_at = _stringify_dt(filing.acceptance_datetime)
                if max_filed_at is None or filed_at > max_filed_at:
                    max_filed_at = filed_at

                if dry_run:
                    inserted += 1
                    continue

                before = conn.total_changes
                db_utils.insert_filing(
                    conn,
                    filing.accession_no,
                    company.cik,
                    filing.form,
                    filed_at,
                    _stringify_dt(filing.filing_date),
                    filing.primary_document or None,
                )
                inserted += conn.total_changes - before
        except Exception as e:
            if not dry_run:
                db_utils.update_watermark(
                    conn,
                    cik=company.cik,
                    last_run_at=datetime.now().isoformat(),
                    last_run_status="FAIL",
                    last_error=str(e),
                )
            raise

        end = datetime.now()
        duration = (end - start).total_seconds()
        print(f"  -> fetched={fetched} inserted={inserted} duration={duration:.2f}s")

        if not dry_run:
            db_utils.update_watermark(
                conn,
                cik=company.cik,
                last_seen_filed_at=max_filed_at,
                last_run_at=end.isoformat(),
                last_run_status="SUCCESS",
                last_error=None,
            )

        return fetched, inserted

    if dry_run:
        for i, ticker in enumerate(tickers, 1):
            try:
                fetched, inserted = run_for_ticker(None, ticker, i)
                total_fetched += fetched
                total_inserted += inserted
            except Exception as e:
                failures.append(f"{ticker}: {e}")
            finally:
                time.sleep(0.11)
    else:
        with db_utils.get_conn() as conn:
            for i, ticker in enumerate(tickers, 1):
                try:
                    fetched, inserted = run_for_ticker(conn, ticker, i)
                    total_fetched += fetched
                    total_inserted += inserted
                except Exception as e:
                    failures.append(f"{ticker}: {e}")
                finally:
                    time.sleep(0.11)

    end_time = datetime.now()
    print(f"Completed at {end_time.isoformat()} (elapsed {(end_time - start_time).total_seconds():.2f}s)")
    print(f"Total fetched: {total_fetched} | Total inserted: {total_inserted}")
    if failures:
        print("Failures:")
        for failure in failures:
            print(f"  - {failure}")
        return 1

    print("✅ Backfill complete!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
                
                
