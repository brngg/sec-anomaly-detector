# Postgres Migration Change Log

Date: 2026-03-04

## Scope
Implemented backend migration plumbing to support `DB_BACKEND` toggling between `postgres` and `sqlite`, with Supabase Postgres as hosted path, while preserving `/risk/*` API contract.

## Runtime and Config
- Added backend-aware connection layer and env-driven DSN resolution in `src/db/db_utils.py`.
- Added support for `DATABASE_URL` / `DATABASE_URL_RW` and optional `API_DATABASE_URL` / `DATABASE_URL_RO`.
- Added Postgres advisory lock helpers for poller scheduling safety.

## Schema Bootstrap and Migrations
- Added Postgres schema bootstrap path in `src/db/init_db.py`.
- Added/ensured `alerts.event_at` and migration/backfill logic from filing event time.
- Added cross-backend bootstrap/migration behavior for normalized outcome columns.
- Added `.env` loading support for direct `init_db.py` execution.

## SQL Portability and API
- Refactored API DB dependency to backend-agnostic connection usage (`src/api/deps.py`).
- Updated route modules (`alerts`, `companies`, `filings`, `risk`) to avoid sqlite-only assumptions.
- Replaced sqlite date-time filtering with Python-computed date boundaries where needed.
- Preserved route contract for:
  - `/risk/top`
  - `/risk/{cik}/history`
  - `/risk/{cik}/explain`

## Detection and Scoring Semantics
- Updated detectors to write alert `event_at` based on filing event time.
- Fixed Postgres temporal type handling (`datetime` / `date`) in detectors.
- Updated risk scoring to use `event_at` for recency/lookback logic.
- Added JSON serialization hardening for native temporal objects in evidence payloads.

## Ingestion and Scheduling
- Updated poller lock strategy:
  - Postgres mode: advisory lock
  - sqlite mode: file lock fallback
- Updated GitHub workflow (`.github/workflows/poll.yml`) to:
  - use `DATABASE_URL_RW`
  - remove sqlite DB commit step
  - run quality gate via Postgres connection
  - upload validation artifacts

## Backfill and Tooling
- Parameterized ingestion backfill window via `BACKFILL_START_DATE` / `BACKFILL_DAYS`.
- Added daily score snapshot backfill utility: `src/analysis/backfill_risk_scores.py`.
- Added progress logging controls for score backfill (`--progress-every`).
- Added SQLite baseline snapshot exporter: `scripts/export_sqlite_baseline.py`.

## Validation and Calibration
- Updated evaluation logic to avoid sqlite date functions.
- Added calibration class-support guardrail metadata and statuses.

## Notebooks
- Updated notebooks for backend toggle support:
  - `notebooks/01_signal_qc.ipynb`
  - `notebooks/02_risk_backtest.ipynb`
- Added `.env` loading + SQLAlchemy connection path.
- Added automatic SQLAlchemy URL normalization to `postgresql+psycopg://...` for Postgres mode.
- Replaced sqlite-only query fragments in notebook backtest cells.

## Tests
- Added/updated tests for:
  - `event_at`-driven recency semantics
  - schema expectations including `alerts.event_at`
  - cross-backend behavior compatibility adjustments

## Documentation
- Updated `README.md` and `docs/CodebaseSummary.md` to reflect backend toggle and hosted Supabase profile.
- Clarified environment variables and execution modes for local vs hosted usage.

## Notes
- Runtime state/outputs (for example report artifacts under `docs/reports/*` and local DB snapshots) are intentionally excluded from migration commit scope.
