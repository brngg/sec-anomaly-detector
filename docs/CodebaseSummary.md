# SEC Disclosure-Risk Monitor — Codebase Summary (Snapshot)

**Date:** 2026-02-23  
**Purpose:** Build an auditable, pre-enforcement disclosure-risk monitor using public SEC filing data.

---

## Current State
- Ingestion is production-like for MVP scope: backfill + hybrid poller + watermark tracking.
- Event-level detectors are implemented and writing alerts.
- Issuer-level risk scoring is implemented and persisted.
- API endpoints exist for companies, filings, alerts, and risk outputs.
- Runtime is now documented for a decoupled operating model:
  - `poll.py` ingests filings only.
  - `run_analysis.py` runs detectors and risk scoring separately.

---

## Current Implemented Components
- **DB schema:** `companies`, `filing_events`, `watermarks`, `poll_state`, `alerts`, `feature_snapshots`, `issuer_risk_scores`, `outcome_events`
- **Ingestion:**
  - `src/ingestion/backfill.py`
  - `src/ingestion/poll.py`
- **Detectors:**
  - `src/detection/nt_detection.py`
  - `src/detection/friday_detection.py`
  - `src/detection/spike_8k_detection.py`
  - shared alert helper in `src/detection/alerts.py`
- **Detector runner:** `src/detection/run_all.py`
- **Risk scoring:**
  - `src/analysis/build_risk_scores.py`
  - `src/analysis/run_analysis.py` (detectors + optional scoring entrypoint)
- **API:** `src/api/main.py`, `src/api/routes/*`
- **Notebook workflow:**
  - `notebooks/01_signal_qc.ipynb`
  - `notebooks/02_risk_backtest.ipynb`

---

## Data Model (Implemented)
- **Database:** `data/sec_anomaly.db` (SQLite)
- **Tables:**
  - `companies` - issuer metadata
  - `filing_events` - normalized filing event stream (dedupe by `accession_id`)
  - `watermarks` - ingestion state per issuer (`last_seen_filed_at`, run metadata)
  - `poll_state` - poller control state (`last_poll_at`, `last_catchup_at`)
  - `alerts` - event-level detector outputs (`dedupe_key` unique)
  - `feature_snapshots` - issuer lookback window features
  - `issuer_risk_scores` - issuer score/rank/percentile snapshots
  - `outcome_events` - optional forward validation labels

---

## Runtime Architecture (Decoupled)

### 1) Ingestion Runtime (`src/ingestion/poll.py`)
**Intended scope in decoupled mode:**
- Pull current SEC feed pages and insert matching filings.
- Run stale-company catch-up for issuers with old/missing watermark state.
- Update `watermarks` and `poll_state`.
- Exit without running detector/scoring logic.

**How to enforce ingestion-only behavior:**
- Set `POLL_ENABLE_INLINE_ANALYSIS=0`.
- Keep detector/scoring execution in a separate scheduled step (`run_analysis.py`).
- Note: code default is still `POLL_ENABLE_INLINE_ANALYSIS=1` for backward compatibility; deployment config must override to `0` for strict decoupling.

**Operational safeguards now in place:**
- File lock to prevent concurrent pollers:
  - `POLL_LOCK_PATH` (default `.poller.lock`)
  - `POLL_LOCK_TIMEOUT_SECONDS` (default `0`, non-blocking)
- Incremental commits:
  - Per feed page commit checkpoint.
  - Per company catch-up commit checkpoint.
  - Poll state commit after catch-up marker update.
- This reduces lost progress if a run is interrupted.

### 2) Analysis Runtime (`src/analysis/run_analysis.py`)
**Scope:**
- Run all anomaly detectors (`run_all_detections`).
- Run issuer risk scoring (`run_risk_scoring`) unless disabled.

**Flag behavior:**
- Uses `POLL_ENABLE_RISK_SCORING` to gate risk scoring.
- Does not ingest filings.

---

## Decoupled Scheduling Contract

### Recommended execution order
1. Ingestion job runs first (`poll.py` with `POLL_ENABLE_INLINE_ANALYSIS=0`).
2. Analysis job runs after ingestion completes (same schedule or staggered schedule).

### Why this split is preferred
- Prevents ingestion failures from being caused by detector/scoring regressions.
- Limits job runtime variance for the ingestion path.
- Reduces overlap risk and duplicate external calls in cron/scheduler environments.
- Creates clearer failure domains and easier retry semantics.

### Example CLI model
```bash
# ingestion only
POLL_ENABLE_INLINE_ANALYSIS=0 SEC_IDENTITY="Your Name you@example.com" python src/ingestion/poll.py

# analysis only
python src/analysis/run_analysis.py
```

---

## Environment Variables (Operationally Relevant)

### Ingestion (`poll.py`)
- `SEC_IDENTITY` - SEC API identity string (required in non-dry runs).
- `DRY_RUN` - if truthy, skips DB writes.
- `POLL_ENABLE_INLINE_ANALYSIS` - set `0` for strict decoupling.
- `POLL_ENABLE_CATCHUP` - enable stale watermark catch-up path.
- `POLL_CATCHUP_DAYS` - stale threshold window.
- `POLL_CATCHUP_COOLDOWN_HOURS` - cooldown between catch-up sweeps.
- `POLL_CURRENT_PAGE_SIZE` - current filings page size.
- `POLL_FEED_BUFFER_HOURS` - buffer when deriving feed cutoff.
- `POLL_LOOKBACK_DAYS` - fallback lookback when watermark missing.
- `POLL_LOCK_PATH` - file lock location for singleton execution.
- `POLL_LOCK_TIMEOUT_SECONDS` - lock wait duration before graceful exit.

### Analysis (`run_analysis.py`)
- `POLL_ENABLE_RISK_SCORING` - if `0`, detectors run but risk scoring is skipped.

---

## Failure and Recovery Semantics
- If ingestion fails mid-run, incremental commits keep already-processed work.
- Watermark run status is updated per issuer (`SUCCESS`/`FAIL`) in `watermarks`.
- Lock contention returns a clean exit in ingestion (`poll.py`) rather than overlapping work.
- If analysis fails, ingestion state remains preserved; analysis can be retried independently.

---

## Evidence and Claims Boundary
- System output is for triage and prioritization.
- It does not assert legal conclusions.
- Performance claims should be framed as forward predictive association (for example precision at K, lift), not causal proof.

---

## Near-Term Priorities
1. Update scheduler/workflow defaults to enforce decoupled mode in production (`POLL_ENABLE_INLINE_ANALYSIS=0`).
2. Add explicit analysis scheduling cadence and alerting for failed analysis runs.
3. Add integration tests for decoupled polling + analysis orchestration.
4. Add reproducible evaluation reports for interview-ready evidence.
