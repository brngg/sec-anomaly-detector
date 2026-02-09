# SEC Filing Anomaly Detector — Codebase Summary (Snapshot)

**Date:** 2026-02-09  
**Purpose:** Automated detection of suspicious filing patterns in SEC EDGAR data (late filings, 8‑K bursts, suspicious timing).

---

## 🔧 Project status (high level)
- Week‑1 completed: DB schema implemented, SQLite DB created, DB utilities added, FastAPI dependency wired, ingestion backfill implemented.
- Polling implemented: global “current filings” poller + GitHub Actions cron (every 15 minutes) that commits DB updates.
- Next priorities: detection algorithms, basic API endpoints, and alerting logic.

---

## 📁 Repository layout (key files)
- `README.md` — project overview & setup
- `requirements.txt` — pinned deps (includes `edgartools`, `fastapi`)
- `data/` — runtime DB: `sec_anomaly.db` (tracked in git for MVP polling)
- `src/`
  - `src/db/init_db.py` — creates DB schema
  - `src/db/db_utils.py` — connection helper + CRUD helpers
  - `src/ingestion/backfill.py` — backfill implementation (CSV + env config)
  - `src/ingestion/poll.py` — global poller using current filings + DB filters
  - `src/api/deps.py` — FastAPI `get_db` dependency
  - `src/detection/`, `src/analysis/`, `src/api/` — scaffolds for next steps
- `docs/` — documentation (this file)
- `.github/workflows/poll.yml` — scheduled poller (every 15 minutes)

---

## 🗄️ Database schema (implemented)
- **DB:** `data/sec_anomaly.db` (SQLite)

Tables:
- `companies`
  - `cik` INTEGER PRIMARY KEY, `name`, `ticker`, `industry`, `updated_at` (ISO timestamp default)
- `filing_events`
  - `accession_id` TEXT PRIMARY KEY, `cik` FK → `companies(cik)`, `filing_type` NOT NULL, `filed_at` NOT NULL (ISO timestamp), `filed_date` NOT NULL, `primary_document`
  - Indexes: `idx_filing_events_cik_type_filed_at`, `idx_filing_events_filed_at`
- `watermarks`
  - `cik` PRIMARY KEY, `last_seen_filed_at`, `updated_at`, `last_run_at`, `last_run_status`, `last_error`
- `alerts`
  - `alert_id` PK, `accession_id` FK → `filing_events(accession_id)`, `anomaly_type`, `severity_score`, `description`, `details` (JSON text), `status`, `dedupe_key` UNIQUE, `created_at`

Timestamps are stored as ISO‑8601 `TEXT` (SQLite `datetime('now')` default) for portability.

---

## 🧩 DB helpers (`src/db/db_utils.py`)
- `get_conn()` — context manager that yields a `sqlite3.Connection`, sets `PRAGMA foreign_keys = ON`, commits on success and rollbacks on error.
- `upsert_company(conn, ...)`, `insert_filing(conn, ...)`, `update_watermark(conn, ...)` — central CRUD helpers to use from ingestion and API.
- `foreign_key_check(conn)` — helper to verify referential integrity.

**Usage pattern:** Always use `with get_conn() as conn:` in ingestion or API code to ensure FK enforcement and consistent transaction handling.

---

## 🔁 Ingestion / Backfill (current)
- `src/ingestion/backfill.py` implements:
  1. CSV‑driven ticker list (`data/companies.csv` by default)
  2. `SEC_IDENTITY` env‑var configuration (fallback with warning)
  3. Fetch filings for past 6 months via `edgartools`
  4. Insert filings (deduped on `accession_id`)
  5. Update `watermarks` per company
- Supports throttling, retries/backoff, and `DRY_RUN=1`.

## 🛰️ Polling (current)
- `src/ingestion/poll.py` implements:
  1. Loads tracked CIKs from `companies`
  2. Fetches global current filings via `get_current_filings`
  3. Filters to `8-K`, `10-Q`, `10-K` (+ amendments)
  4. Inserts new filings (deduped by `accession_id`)
  5. Updates `watermarks` per company
- GitHub Actions runs every 15 minutes and commits DB updates to the repo.

---

## ✅ Verification checklist (before backfill)
- `ls -l data/sec_anomaly.db` — DB file exists
- `sqlite3 data/sec_anomaly.db ".tables"` — tables present
- `python3 -c "from src.db.db_utils import get_conn; with get_conn() as c: print(c.execute('PRAGMA foreign_keys;').fetchone()[0])"` → should print `1`
- `sqlite3 data/sec_anomaly.db "PRAGMA integrity_check;"` → `ok`
- `sqlite3 data/sec_anomaly.db "PRAGMA foreign_key_check;"` → empty (no violations)
- Smoke test: upsert a company then insert a filing via `get_conn()` helpers and confirm rows.

---

## 🚀 Next implementation priorities
1. Implement detection algorithms in `src/detection/` (frequency spike, size outlier) and write alerts to `alerts` table.
2. Add basic API endpoints (`/health`, `/companies`, `/companies/{cik}/filings`, `/filings/{accession}`).
3. Add comprehensive tests and prepare a Postgres migration plan.
