import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

if __package__ in {None, ""}:
    repo_root = Path(__file__).resolve().parents[2]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

from edgar import Company, iter_current_filings_pages, set_identity
from dotenv import load_dotenv
from filelock import FileLock, Timeout as FileLockTimeout

from src.analysis.build_risk_scores import run_risk_scoring
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
FORM_TYPES_SET = set(FORM_TYPES)
DEFAULT_LOOKBACK_DAYS = int(os.getenv("POLL_LOOKBACK_DAYS", "14"))
CATCHUP_DAYS = int(os.getenv("POLL_CATCHUP_DAYS", "2"))
ENABLE_CATCHUP = os.getenv("POLL_ENABLE_CATCHUP", "1").strip().lower() not in {"0", "false", "no", "n"}
CATCHUP_COOLDOWN_HOURS = int(os.getenv("POLL_CATCHUP_COOLDOWN_HOURS", "48"))
STALE_RUN_HOURS = int(os.getenv("POLL_STALE_RUN_HOURS", "6"))
STALE_RUN_THRESHOLD_PCT = float(os.getenv("POLL_STALE_RUN_THRESHOLD_PCT", "0.8"))
CURRENT_PAGE_SIZE = int(os.getenv("POLL_CURRENT_PAGE_SIZE", "100"))
FEED_BUFFER_HOURS = int(os.getenv("POLL_FEED_BUFFER_HOURS", "6"))
SLEEP_SECONDS = float(os.getenv("POLL_SLEEP_SECONDS", "0.11"))
LOCK_TIMEOUT_SECONDS = float(os.getenv("POLL_LOCK_TIMEOUT_SECONDS", "0"))
LOCK_PATH = Path(os.getenv("POLL_LOCK_PATH", str(REPO_ROOT / ".poller.lock")))


def _parse_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "y"}


def _is_disabled(value: str) -> bool:
    return value.strip().lower() in {"0", "false", "no", "n"}


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


def _coerce_dt(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time(), tzinfo=timezone.utc)
    if isinstance(value, str):
        return _parse_dt(value)
    return _parse_dt(str(value))


def _is_stale(last_seen: str | None, cutoff: datetime) -> bool:
    if not last_seen:
        return True
    parsed = _parse_dt(last_seen)
    if not parsed:
        return True
    return parsed < cutoff


def _ensure_poll_state(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS poll_state (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """
    )


def _get_poll_state(conn, key: str) -> str | None:
    row = conn.execute("SELECT value FROM poll_state WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def _set_poll_state(conn, key: str, value: str) -> None:
    conn.execute(
        """
        INSERT INTO poll_state (key, value)
        VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET
            value = excluded.value
        """,
        (key, value),
    )


def _warn_if_stale_runs(tracked_ciks: set[int], last_run_map: dict[int, str | None], now_utc: datetime) -> None:
    stale_run_cutoff = now_utc - timedelta(hours=STALE_RUN_HOURS)
    stale_runs = 0
    for cik in tracked_ciks:
        last_run = last_run_map.get(cik)
        parsed = _parse_dt(last_run) if last_run else None
        if parsed is None or parsed < stale_run_cutoff:
            stale_runs += 1
    if tracked_ciks:
        stale_ratio = stale_runs / len(tracked_ciks)
        if stale_ratio >= STALE_RUN_THRESHOLD_PCT:
            print(
                "Warning: poller staleness detected. "
                f"{stale_runs}/{len(tracked_ciks)} companies have last_run_at "
                f"older than {STALE_RUN_HOURS}h."
            )


def _compute_feed_cutoff(last_seen_map: dict[int, str | None], now_utc: datetime) -> datetime | None:
    stale_cutoff = now_utc - timedelta(days=CATCHUP_DAYS)
    non_stale_last_seen: list[datetime] = []
    for last_seen in last_seen_map.values():
        parsed = _parse_dt(last_seen) if last_seen else None
        if parsed and parsed >= stale_cutoff:
            non_stale_last_seen.append(parsed)
    if not non_stale_last_seen:
        return None
    buffer_hours = max(FEED_BUFFER_HOURS, 0)
    feed_cutoff = min(non_stale_last_seen) - timedelta(hours=buffer_hours)
    print(f"Feed cutoff: {feed_cutoff.isoformat()} (buffer {buffer_hours}h)")
    return feed_cutoff


def _scan_current_feed(
    db_conn,
    tracked_ciks: set[int],
    last_seen_map: dict[int, str | None],
    page_size: int,
    feed_cutoff: datetime | None,
    dry_run: bool,
    stats: dict[str, int],
) -> tuple[dict[int, str | None], float]:
    max_seen_by_cik: dict[int, str | None] = {}
    feed_start = time.perf_counter()
    for page in iter_current_filings_pages(page_size=page_size):
        stats["feed_pages"] += 1
        page_entries = page.data.to_pylist()
        if not page_entries:
            continue

        break_after = False
        if feed_cutoff:
            oldest_dt = _coerce_dt(page_entries[-1].get("accepted")) or _coerce_dt(
                page_entries[-1].get("filing_date")
            )
            if oldest_dt and oldest_dt < feed_cutoff:
                break_after = True

        for entry in page_entries:
            if entry.get("form") not in FORM_TYPES_SET:
                continue
            stats["feed_seen"] += 1
            stats["total_seen"] += 1
            try:
                filing_cik = int(entry.get("cik"))
            except Exception:
                continue

            if filing_cik not in tracked_ciks:
                continue

            stats["feed_matched"] += 1
            filed_at = entry.get("accepted") or entry.get("filing_date")
            filed_at_str = _stringify_dt(filed_at)
            filed_date_str = _stringify_dt(entry.get("filing_date"))
            max_seen_by_cik[filing_cik] = _resolve_last_seen(
                max_seen_by_cik.get(filing_cik), filed_at_str
            )

            if dry_run:
                stats["feed_inserted"] += 1
                stats["total_inserted"] += 1
                continue

            try:
                changes_before_insert = db_conn.total_changes
                db_utils.insert_filing(
                    db_conn,
                    entry.get("accession_number"),
                    filing_cik,
                    entry.get("form"),
                    filed_at_str,
                    filed_date_str,
                    None,
                )
                if db_conn.total_changes > changes_before_insert:
                    stats["feed_inserted"] += 1
                    stats["total_inserted"] += 1
            except Exception as e:
                stats["total_errors"] += 1
                if not dry_run:
                    db_utils.update_watermark(
                        db_conn,
                        cik=filing_cik,
                        last_seen_filed_at=last_seen_map.get(filing_cik),
                        last_run_at=datetime.now().isoformat(),
                        last_run_status="FAIL",
                        last_error=str(e),
                    )
        if not dry_run:
            # Persist progress incrementally so long runs don't lose all ingestion work.
            db_conn.commit()

        if break_after:
            break
    feed_duration = time.perf_counter() - feed_start
    return max_seen_by_cik, feed_duration


def _apply_feed_watermarks(
    db_conn,
    last_seen_map: dict[int, str | None],
    max_seen_by_cik: dict[int, str | None],
    dry_run: bool,
) -> None:
    if dry_run:
        return
    for cik, max_seen in max_seen_by_cik.items():
        resolved = _resolve_last_seen(last_seen_map.get(cik), max_seen)
        last_seen_map[cik] = resolved
        db_utils.update_watermark(
            db_conn,
            cik=cik,
            last_seen_filed_at=resolved,
            last_run_at=datetime.now().isoformat(),
            last_run_status="SUCCESS",
            last_error=None,
        )
    db_conn.commit()


def _run_catchup(
    db_conn,
    rows,
    last_seen_map: dict[int, str | None],
    dry_run: bool,
    now_utc: datetime,
    stats: dict[str, int],
) -> tuple[bool, float]:
    if not ENABLE_CATCHUP:
        return False, 0.0

    last_catchup_at = _get_poll_state(db_conn, "last_catchup_at")
    if last_catchup_at:
        parsed = _parse_dt(last_catchup_at)
        if parsed:
            next_allowed = parsed + timedelta(hours=CATCHUP_COOLDOWN_HOURS)
            if now_utc < next_allowed:
                print(
                    "Catch-up skipped (cooldown). "
                    f"Next eligible after {next_allowed.isoformat()}"
                )
                return True, 0.0

    catchup_start = time.perf_counter()
    cutoff = datetime.now(timezone.utc) - timedelta(days=CATCHUP_DAYS)
    stale_rows = [
        row for row in rows if _is_stale(last_seen_map.get(int(row["cik"])), cutoff)
    ]
    if stale_rows:
        print(f"Catch-up companies: {len(stale_rows)} (stale > {CATCHUP_DAYS} days)")
    else:
        print("Catch-up companies: 0")

    for index, row in enumerate(stale_rows, 1):
        cik = int(row["cik"])
        ticker = row["ticker"]
        last_seen = last_seen_map.get(cik)
        since_date = _since_date(last_seen)
        label = ticker or str(cik)

        fetched = 0
        inserted = 0
        max_filed_at = None
        stats["catchup_companies"] += 1
        company_start = time.perf_counter()

        try:
            company = Company(label)
            filings = company.get_filings(form=FORM_TYPES).filter(date=f"{since_date}:")
            for filing in filings:
                fetched += 1
                stats["catchup_seen"] += 1
                stats["total_seen"] += 1
                filed_at = filing.acceptance_datetime or filing.filing_date
                filed_at_str = _stringify_dt(filed_at)
                filed_date_str = _stringify_dt(filing.filing_date)

                max_filed_at = _resolve_last_seen(max_filed_at, filed_at_str)

                if dry_run:
                    inserted += 1
                    stats["catchup_inserted"] += 1
                    stats["total_inserted"] += 1
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
                    stats["catchup_inserted"] += 1
                    stats["total_inserted"] += 1

            if not dry_run:
                resolved_last_seen = _resolve_last_seen(last_seen, max_filed_at)
                last_seen_map[cik] = resolved_last_seen
                db_utils.update_watermark(
                    db_conn,
                    cik=cik,
                    last_seen_filed_at=resolved_last_seen,
                    last_run_at=datetime.now().isoformat(),
                    last_run_status="SUCCESS",
                    last_error=None,
                )
                db_conn.commit()
        except Exception as e:
            stats["total_errors"] += 1
            if not dry_run:
                db_utils.update_watermark(
                    db_conn,
                    cik=cik,
                    last_seen_filed_at=last_seen,
                    last_run_at=datetime.now().isoformat(),
                    last_run_status="FAIL",
                    last_error=str(e),
                )
                db_conn.commit()
            print(f"Error processing {label}: {e}")
        finally:
            company_duration = time.perf_counter() - company_start
            print(
                f"[catch-up {index}/{len(stale_rows)}] {label}: "
                f"fetched={fetched} inserted={inserted} duration={company_duration:.2f}s"
            )
            time.sleep(SLEEP_SECONDS)

    catchup_duration = time.perf_counter() - catchup_start
    if not dry_run:
        _set_poll_state(db_conn, "last_catchup_at", now_utc.isoformat())
        db_conn.commit()
    return False, catchup_duration


def main() -> int:
    load_dotenv(REPO_ROOT / ".env", override=True)

    lock_path = Path(os.getenv("POLL_LOCK_PATH", str(LOCK_PATH)))
    lock_timeout_seconds = float(os.getenv("POLL_LOCK_TIMEOUT_SECONDS", str(LOCK_TIMEOUT_SECONDS)))
    enable_inline_analysis = not _is_disabled(os.getenv("POLL_ENABLE_INLINE_ANALYSIS", "1"))
    enable_risk_scoring = not _is_disabled(os.getenv("POLL_ENABLE_RISK_SCORING", "1"))

    sec_identity = os.getenv("SEC_IDENTITY", "").strip()
    if not sec_identity:
        print("SEC_IDENTITY not set. Please set it before running.")
        return 1

    set_identity(sec_identity)

    dry_run = _parse_bool(os.getenv("DRY_RUN", ""))

    lock = FileLock(str(lock_path))
    try:
        lock.acquire(timeout=max(0.0, lock_timeout_seconds))
    except FileLockTimeout:
        print(f"Another poller instance is already running (lock={lock_path}). Exiting.")
        return 0

    try:
        start_time = datetime.now()
        overall_start = time.perf_counter()
        now_utc = datetime.now(timezone.utc)
        print(f"Starting poll at {start_time.isoformat()}")

        stats = {
            "total_seen": 0,
            "total_inserted": 0,
            "total_errors": 0,
            "feed_seen": 0,
            "feed_matched": 0,
            "feed_inserted": 0,
            "feed_pages": 0,
            "catchup_companies": 0,
            "catchup_seen": 0,
            "catchup_inserted": 0,
        }

        with db_utils.get_conn() as db_conn:
            _ensure_poll_state(db_conn)
            rows = db_conn.execute(
                """
                SELECT c.cik, c.ticker, w.last_seen_filed_at, w.last_run_at
                FROM companies c
                LEFT JOIN watermarks w ON w.cik = c.cik
                ORDER BY c.cik
                """
            ).fetchall()

            if not rows:
                print("No tracked companies found in DB. Exiting.")
                return 1

            _set_poll_state(db_conn, "last_poll_at", now_utc.isoformat())

            print(f"Tracked companies: {len(rows)}")

            tracked_ciks = {int(row["cik"]) for row in rows}
            last_seen_map = {int(row["cik"]): row["last_seen_filed_at"] for row in rows}
            last_run_map = {int(row["cik"]): row["last_run_at"] for row in rows}
            _warn_if_stale_runs(tracked_ciks, last_run_map, now_utc)

            # Fast path: current filings feed (last ~24 hours) with pagination
            page_size = CURRENT_PAGE_SIZE if CURRENT_PAGE_SIZE in {10, 20, 40, 80, 100} else 100

            now_utc = datetime.now(timezone.utc)
            feed_cutoff = _compute_feed_cutoff(last_seen_map, now_utc)

            print("Scanning current feed...")
            max_seen_by_cik, feed_duration = _scan_current_feed(
                db_conn,
                tracked_ciks,
                last_seen_map,
                page_size,
                feed_cutoff,
                dry_run,
                stats,
            )
            print(f"Finished feed scan in {feed_duration:.2f}s")

            _apply_feed_watermarks(db_conn, last_seen_map, max_seen_by_cik, dry_run)

            # Catch-up path: only for stale/missing watermarks
            catchup_skipped, catchup_duration = _run_catchup(
                db_conn,
                rows,
                last_seen_map,
                dry_run,
                now_utc,
                stats,
            )

        end_time = datetime.now()
        overall_duration = time.perf_counter() - overall_start
        elapsed = (end_time - start_time).total_seconds()
        print(f"Completed at {end_time.isoformat()} (elapsed {elapsed:.2f}s)")
        if ENABLE_CATCHUP and (stats["catchup_companies"] > 0 or catchup_skipped):
            print(
                "Summary: "
                f"seen={stats['total_seen']} inserted={stats['total_inserted']} errors={stats['total_errors']}"
            )
            print(
                "Feed summary: "
                f"seen={stats['feed_seen']} matched={stats['feed_matched']} inserted={stats['feed_inserted']}"
            )
            print(f"Feed pages scanned: {stats['feed_pages']} | duration={feed_duration:.2f}s")
            print(
                "Catch-up summary: "
                f"companies={stats['catchup_companies']} seen={stats['catchup_seen']} inserted={stats['catchup_inserted']}"
            )
            if catchup_skipped:
                print(f"Catch-up cooldown: {CATCHUP_COOLDOWN_HOURS}h (skipped)")
            elif stats["catchup_companies"] > 0:
                print(f"Catch-up duration: {catchup_duration:.2f}s")
            print(f"Total runtime: {overall_duration:.2f}s")
        else:
            print(
                "Summary: "
                f"feed_seen={stats['feed_seen']} matched={stats['feed_matched']} "
                f"inserted={stats['feed_inserted']} errors={stats['total_errors']} "
                f"runtime={overall_duration:.2f}s"
            )
            print(f"Feed pages scanned: {stats['feed_pages']} | duration={feed_duration:.2f}s")

        if not dry_run and stats["total_inserted"] > 0:
            if not enable_inline_analysis:
                print("New filings inserted; inline analysis disabled by POLL_ENABLE_INLINE_ANALYSIS.")
            else:
                print("New filings inserted; running detectors...")
                run_all_detections()

                if enable_risk_scoring:
                    print("Running issuer risk scoring...")
                    try:
                        score_stats = run_risk_scoring()
                        print(
                            "Risk scoring summary: "
                            f"issuers_scored={score_stats['issuers_scored']} "
                            f"snapshots_upserted={score_stats['snapshots_upserted']} "
                            f"scores_upserted={score_stats['scores_upserted']} "
                            f"source_alerts={score_stats['source_alerts']} "
                            f"as_of_date={score_stats['as_of_date']}"
                        )
                    except Exception as e:
                        print(f"Risk scoring failed: {e}")
                else:
                    print("Risk scoring disabled by POLL_ENABLE_RISK_SCORING.")
        else:
            print("No new filings inserted; skipping detectors and risk scoring.")

        return 0 if stats["total_errors"] == 0 else 1
    finally:
        if lock.is_locked:
            lock.release()


if __name__ == "__main__":
    sys.exit(main())
