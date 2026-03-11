# SEC Review Priority Monitor — Unified Codebase Summary

**Last updated:** 2026-03-11

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
6. Weekly maintenance (`scripts/prune_postgres_data.py`) trims legacy score rows / old feature snapshots and emits a retention report artifact.
7. Streamlit dashboard (`app.py`) provides a leaderboard-first UI over the current API.

## 5) Three Operating Lanes (Important)

Backend profile (set once per shell before running lane commands):

```bash
# Hosted/Supabase profile
export DB_BACKEND=postgres
export DATABASE_URL="postgresql://app_rw.<project_ref>:<password>@<pooler-host>:5432/postgres?sslmode=require"
export API_DATABASE_URL="postgresql://app_ro.<project_ref>:<password>@<pooler-host>:5432/postgres?sslmode=require"

# Accepted aliases in code/workflows:
# export DATABASE_URL_RW="$DATABASE_URL"
# export DATABASE_URL_RO="$API_DATABASE_URL"

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
- `GET /risk/{cik}/history` (trend view)
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

### Lane C: Weekly Maintenance
Use this to keep hosted Postgres storage under control and preserve a retention report.

```bash
./venv/bin/python scripts/prune_postgres_data.py \
  --feature-retention-days 120 \
  --apply \
  --output docs/reports/retention/local_retention_report.json
```

GitHub Actions automation:
- `.github/workflows/maintenance.yml`
- weekly schedule: Monday `13:00 UTC`
- manual trigger: `workflow_dispatch`

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
- live leaderboard generation uses `alerts` plus filing metadata, not filing-text verification.
- all tracked companies are scored daily, even when they have zero active anomaly signals.

## 7) Scoring Summary (Default)
Default model version: `v2_monthly_abnormal`

Signals used:
- `NT_FILING`
- `FRIDAY_BURYING`
- `8K_SPIKE`
  - detector policy: current UTC month only, compared to strict prior-month baseline

Scoring approach:
- monthly issuer score built from weighted signal components
- baseline from issuer's own prior months (average + std)
- current 30-day interval abnormality vs monthly baseline drives ranking
- blend formula: `0.35*current_interval_30d + 0.35*relative_lift + 0.30*zscore_component`
- monthly baseline uses all available history unless `RISK_MONTHLY_HISTORY_MONTHS` is explicitly set
- deterministic ordering by rank/score/cik

Operator notes:
- zero-score / zero-signal issuers are normal
- signal-stack counts are modeled anomaly-alert counts, not raw filing totals
- the default model does not use filing text or item-level 8-K parsing

Evidence payload includes:
- component math and top contributors
- monthly baseline diagnostics and month-over-month deltas
- rank stability diagnostics
- uncertainty band
- optional calibrated score + calibration metadata

Legacy scoring remains available:
- mode: `alert_composite`
- model: `v1_alert_composite`
- behavior: 30/90-day recency-weighted window composite

## 8) API Contract (What Frontend Needs)
Primary endpoints:
- `GET /risk/top`
- `GET /risk/{cik}/history`
- `GET /risk/{cik}/explain`
- `GET /alerts` (filters for drilldown)

Current API behavior:
- When `API_AUTH_ENABLED=1`, all routes except `/health` require `X-API-Key`.
- `GET /risk/top?include_evidence=false` returns leaderboard rows without the large `evidence` object.
- `GET /risk/{cik}/history?include_evidence=false` does the same for trend/history views.
- `GET /risk/{cik}/explain` always returns full evidence.
- `GET /alerts?cik=&anomaly_type=` supports alert-level drilldown.
- `GET /companies/{cik}/filings?filing_type=` supports filing-level drilldown.
- `/risk/top` and `/risk/{cik}/history` may return issuers with zero active signals.

Current dashboard scope:
- watchlist / leaderboard
- issuer history trend
- issuer explainability
- no filing document views yet

Essential leaderboard fields:
- `cik`, `company_name`, `company_ticker`
- `risk_score`, `risk_rank`, `percentile`
- `calibrated_review_priority` (nullable)
- `as_of_date`, `model_version`

## 9) Operational Environment Variables
Database backend:
- `DB_BACKEND` (`postgres` or `sqlite`)
- `DATABASE_URL` (required for `postgres` job/runtime writes)
- `DATABASE_URL_RW` (accepted alias for write DSN; used by GitHub Actions)
- `API_DATABASE_URL` (optional read-only DSN for API runtime in `postgres` mode)
- `DATABASE_URL_RO` (accepted alias for API read-only DSN)

Hosted profile (Supabase):
- Use `DB_BACKEND=postgres` with pooler DSNs.
- Scheduler remains GitHub Actions (`.github/workflows/poll.yml`) with secrets:
  - `DATABASE_URL_RW`
  - `DATABASE_URL_RO` (optional)
  - `SEC_IDENTITY`

API security:
- `API_AUTH_ENABLED=1` to enable shared-key auth
- `API_KEY` shared secret expected in `X-API-Key`
- `DEMO_API_KEY` optional helper env for scripts such as `scripts/demo_api_snapshot.py`

Ingestion:
- `SEC_IDENTITY`
- `POLL_ENABLE_INLINE_ANALYSIS` (set `0` for decoupled mode)
- `POLL_ENABLE_CATCHUP`
- `POLL_ADVISORY_LOCK_NAME` (Postgres advisory lock key)
- `POLL_LOCK_PATH`, `POLL_LOCK_TIMEOUT_SECONDS` (sqlite file-lock settings)

Analysis:
- `POLL_ENABLE_RISK_SCORING` (controls scoring inside `run_analysis.py`)
- `RISK_SCORING_MODE` (`monthly_abnormal` default, `alert_composite` legacy)
- `RISK_DEFAULT_MODEL_VERSION` (API default model selector; default `v2_monthly_abnormal`)
- `RISK_MODEL_VERSION` (optional explicit model-version label override when you do not want the mode-derived default)
- `RISK_MONTHLY_HISTORY_MONTHS` (optional baseline history window in months)

Validation fetchers:
- `SEC_IDENTITY` for candidate generation and verification

Additional poller controls:
- `POLL_CATCHUP_DAYS`
- `POLL_CATCHUP_COOLDOWN_HOURS`
- `POLL_LOOKBACK_DAYS`
- `POLL_CURRENT_PAGE_SIZE`
- `POLL_FEED_BUFFER_HOURS`
- `POLL_STALE_RUN_HOURS`
- `POLL_STALE_RUN_THRESHOLD_PCT`
- `POLL_SLEEP_SECONDS`

Dashboard runtime:
- `DASHBOARD_API_BASE_URL`
- `DASHBOARD_API_KEY`
- `DASHBOARD_DEFAULT_LIMIT`
- `DASHBOARD_REQUEST_TIMEOUT_SECONDS`

## 10) Foundation Changes To Apply On Existing Deployments
1. Run schema bootstrap against Postgres so the latest indexes exist:

```bash
DB_BACKEND=postgres \
DATABASE_URL="postgresql://app_rw.<project_ref>:<password>@<pooler-host>:5432/postgres?sslmode=require" \
./venv/bin/python src/db/init_db.py
```

2. Set deploy/runtime env:
- `API_AUTH_ENABLED=1`
- `API_KEY=<strong secret>`
- `API_DATABASE_URL=<optional RO DSN>`

3. Update API clients:
- send `X-API-Key` on non-health routes
- use `include_evidence=false` on `/risk/top` and `/risk/{cik}/history` for list views

4. Trigger the maintenance workflow once to confirm artifact upload and retention output.

5. For the dashboard UI, set:
- `DASHBOARD_API_BASE_URL`
- `DASHBOARD_API_KEY` (when auth is enabled)

Run locally:

```bash
./venv/bin/streamlit run app.py
```

## 11) Common Failure Modes and Meaning
- No rows in `/risk/top`: analysis lane did not run or no alerts/scores written.
- `401 Invalid API key`: auth is enabled and the client omitted or mismatched `X-API-Key`.
- Many `FETCH_ERROR` in verification: SEC retrieval/path/network issue.
- All validation metrics at `0.0`: label yield/coverage is too sparse for claims.
- Calibration unavailable in evidence: missing/stale/insufficient calibration artifacts.

## 12) Practical Recommendation for Current Stage
- Ship and operate **Lane A** daily for a functioning leaderboard.
- Run **Lane B** weekly to accumulate evidence.
- Run **Lane C** weekly to control storage and preserve retention reports.
- Keep claims scoped to triage until strict/broad coverage is healthy.
- Use [docs/ScoreAuditGuide.md](ScoreAuditGuide.md) when an operator needs to verify one issuer row from dashboard to API to DB.

## 13) Legacy Docs Status (Consolidated Here)
The following docs were split views of the same system and are now consolidated into this file:
- `docs/DashboardDataContract.md`
- `docs/DEMO_RUNBOOK.md`
- `docs/OutcomeLabels.md`
- `docs/ReviewPriorityScoreSpec.md`

They are retained as lightweight pointers for backward links only.
