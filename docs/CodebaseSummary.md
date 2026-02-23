# SEC Disclosure-Risk Monitor — Codebase Summary (Snapshot)

**Date:** 2026-02-23  
**Purpose:** Build an auditable, pre-enforcement disclosure-risk monitor using public SEC filing data.

---

## Current State
- Ingestion is production-like for MVP scope: backfill + hybrid poller + watermark tracking.
- Signal detectors are implemented and writing event-level alerts.
- FastAPI endpoints exist for companies, filings, and alerts.
- Pivot is now in progress from event-level anomaly alerts to issuer-level risk ranking.

---

## Current Implemented Components
- **DB schema:** `companies`, `filing_events`, `watermarks`, `alerts`, `poll_state`
- **Ingestion:** `src/ingestion/backfill.py`, `src/ingestion/poll.py`
- **Detectors:**
  - `src/detection/nt_detection.py`
  - `src/detection/friday_detection.py`
  - `src/detection/spike_8k_detection.py`
  - shared alert insert helper in `src/detection/alerts.py`
- **Detector runner:** `src/detection/run_all.py`
- **API:** `src/api/main.py`, `src/api/routes/*`
- **Notebook workflow:**
  - `notebooks/01_signal_qc.ipynb`
  - `notebooks/02_risk_backtest.ipynb`

---

## Data Model (Implemented)
- **Database:** `data/sec_anomaly.db` (SQLite)
- **Tables:**
  - `companies` - issuer metadata
  - `filing_events` - normalized filing event stream
  - `watermarks` - ingestion state per issuer
  - `poll_state` - poller control state
  - `alerts` - event-level signal outputs (deduped with `dedupe_key`)

---

## Pivot Direction
Current alerts are treated as **signal features** feeding a future issuer-level score.

Planned additions:
1. `feature_snapshots` table for issuer/date feature vectors
2. `issuer_risk_scores` table for normalized composite scores and ranks
3. `outcome_events` table for forward validation labels
4. Scoring and backtest jobs under `src/analysis/`

---

## Evidence and Claims Boundary
- System output is for triage and prioritization.
- It does not assert legal conclusions.
- Performance claims should be framed as forward predictive association (for example precision at K, lift), not causal proof.

---

## Near-Term Priorities
1. Add feature aggregation from existing alert types.
2. Add issuer-level scoring job and API endpoints.
3. Add outcome-label ingestion and walk-forward backtesting.
4. Add reproducible evaluation reports for interview-ready evidence.
