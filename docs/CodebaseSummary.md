# SEC Review Priority Monitor — Unified Codebase Summary

**Last updated:** 2026-03-03

## 1) What This Project Is
- A public-data SEC filing triage system.
- Output is a **Review Priority leaderboard** for issuers.
- It helps answer: "Who should I review first today?"

## 2) What It Is Not
- Not fraud probability.
- Not legal liability determination.
- Not a credit/business quality score.

## 3) How it works
- Raw ranking is generated from filing anomaly signals and runs daily without outcome labels.
- Outcome labels are only for validation and calibration claims.
- If labels are sparse, leaderboard still works, but validation/calibration claims are weak.

## 4) Workflows
1. Ingestion (`src/ingestion/poll.py`) inserts new filings into `filing_events`.
2. Detection (`src/detection/run_all.py`) creates event-level `alerts`.
3. Scoring (`src/analysis/build_risk_scores.py`) writes issuer leaderboard rows into `issuer_risk_scores`.
4. API (`src/api/routes/risk.py`) serves `/risk/top`, `/risk/{cik}/history`, `/risk/{cik}/explain`.
5. Validation lane (optional but recommended) populates `outcome_events`, evaluates lift/precision/recall, and writes calibration artifacts.

## 5) Two Operating Lanes (Important)

Backend profile (set once per shell before running lane commands):

```bash
# Hosted/Supabase profile
export DB_BACKEND=postgres
export DATABASE_URL="postgresql://app_rw.<project_ref>:<password>@<pooler-host>:5432/postgres?sslmode=require"
export API_DATABASE_URL="postgresql://app_ro.<project_ref>:<password>@<pooler-host>:5432/postgres?sslmode=require"

# Local file profile
# export DB_BACKEND=sqlite
# unset DATABASE_URL
# unset API_DATABASE_URL
```

### Lane A: Daily Leaderboard (production path)
Use this to keep leaderboard fresh. This is your primary workflow.

```bash
# step 1: ingest only
POLL_ENABLE_INLINE_ANALYSIS=0 SEC_IDENTITY="Your Name you@example.com" ./venv/bin/python src/ingestion/poll.py

# step 2: run analysis
./venv/bin/python src/analysis/run_analysis.py
```

What this updates:
- `filing_events`
- `alerts`
- `feature_snapshots`
- `issuer_risk_scores`

How users consume it:
- `GET /risk/top` (leaderboard)
- `GET /risk/{cik}/explain` (evidence drilldown)

### Lane B: Validation + Calibration (weekly/periodic)
Use this to test defensibility of ranking claims.

```bash
# 1) generate likely adverse candidates
SEC_IDENTITY="Your Name you@example.com" ./venv/bin/python src/analysis/generate_outcome_candidates.py \
  --output data/outcomes_candidates.csv \
  --min-confidence MEDIUM

# 2) verify against SEC filing text
SEC_IDENTITY="Your Name you@example.com" ./venv/bin/python src/analysis/verify_outcomes.py \
  --input data/outcomes_candidates.csv \
  --review-output data/outcomes_reviewed.csv \
  --verified-output data/outcomes.csv \
  --min-confidence-for-export HIGH

# 3) import labels
./venv/bin/python src/analysis/import_outcomes.py --input data/outcomes.csv --min-confidence HIGH

# 4) evaluate strict + broad tracks
./venv/bin/python src/analysis/evaluate_review_priority.py \
  --output-dir docs/reports/validation \
  --emit-confidence-splits
```

Validation tracks:
- **STRICT:** `VERIFIED_HIGH`
- **BROAD:** `VERIFIED_HIGH + VERIFIED_MEDIUM`

## 6) Current Data Model
Database backend is runtime-selectable via `DB_BACKEND`:
- `postgres`: external Postgres/Supabase source-of-truth
- `sqlite`: local file mode (`data/sec_anomaly.db`)

- `companies`: issuer metadata
- `filing_events`: normalized filings (dedupe by `accession_id`)
- `watermarks`: ingestion state per issuer
- `poll_state`: poller runtime markers
- `alerts`: detector outputs with unique `dedupe_key`
- `feature_snapshots`: scoring features by issuer/date/lookback
- `issuer_risk_scores`: leaderboard rows (score, rank, percentile, evidence)
- `outcome_events`: optional forward outcomes for validation/calibration

Key note:
- `outcome_events` is not required for leaderboard generation.

## 7) Scoring Summary (v1)
Model version: `v1_alert_composite`

Signals used:
- `NT_FILING`
- `FRIDAY_BURYING`
- `8K_SPIKE`

Scoring approach:
- recency decay (30-day half-life)
- two windows (30d, 90d)
- weighted aggregation into `risk_score` in `[0,1]`
- deterministic ordering by rank/score/cik

Evidence payload includes:
- component math and top contributors
- rank stability diagnostics
- uncertainty band
- optional calibrated score + calibration metadata

## 8) API Contract (What Frontend Needs)
Primary endpoints:
- `GET /risk/top`
- `GET /risk/{cik}/history`
- `GET /risk/{cik}/explain`
- `GET /alerts` (filters for drilldown)

Essential leaderboard fields:
- `cik`, `company_name`, `company_ticker`
- `risk_score`, `risk_rank`, `percentile`
- `calibrated_review_priority` (nullable)
- `as_of_date`, `model_version`

## 9) Operational Environment Variables
Database backend:
- `DB_BACKEND` (`postgres` or `sqlite`)
- `DATABASE_URL` (required for `postgres` job/runtime writes)
- `API_DATABASE_URL` (optional read-only DSN for API runtime in `postgres` mode)

Hosted profile (Supabase):
- Use `DB_BACKEND=postgres` with pooler DSNs.
- Scheduler remains GitHub Actions (`.github/workflows/poll.yml`) with secrets:
  - `DATABASE_URL_RW`
  - `DATABASE_URL_RO` (optional)
  - `SEC_IDENTITY`

Ingestion:
- `SEC_IDENTITY`
- `POLL_ENABLE_INLINE_ANALYSIS` (set `0` for decoupled mode)
- `POLL_ENABLE_CATCHUP`
- `POLL_ADVISORY_LOCK_NAME` (Postgres advisory lock key)
- `POLL_LOCK_PATH`, `POLL_LOCK_TIMEOUT_SECONDS` (sqlite file-lock settings)

Analysis:
- `POLL_ENABLE_RISK_SCORING` (controls scoring inside `run_analysis.py`)

Validation fetchers:
- `SEC_IDENTITY` for candidate generation and verification

## 10) Common Failure Modes and Meaning
- No rows in `/risk/top`: analysis lane did not run or no alerts/scores written.
- Many `FETCH_ERROR` in verification: SEC retrieval/path/network issue.
- All validation metrics at `0.0`: label yield/coverage is too sparse for claims.
- Calibration unavailable in evidence: missing/stale/insufficient calibration artifacts.

## 11) Practical Recommendation for Current Stage
- Ship and operate **Lane A** daily for a functioning leaderboard.
- Run **Lane B** weekly to accumulate evidence.
- Keep claims scoped to triage until strict/broad coverage is healthy.

## 12) Legacy Docs Status (Consolidated Here)
The following docs were split views of the same system and are now consolidated into this file:
- `docs/DashboardDataContract.md`
- `docs/DEMO_RUNBOOK.md`
- `docs/OutcomeLabels.md`
- `docs/ReviewPriorityScoreSpec.md`

They are retained as lightweight pointers for backward links only.
