# SEC Review Priority Early Warning Index

Public-data system for monitoring issuer filing review priority using SEC EDGAR filings.

## Positioning
This project does not attempt to establish legal proof of fraud.  
It produces auditable, pre-enforcement review-priority signals to help prioritize issuer review.

## Status
Pivot in progress from event-level anomaly alerts to issuer-level review-priority monitoring.

## Current Capabilities
- SEC ingestion and polling for tracked issuers
- Event-level signal generation:
  - non-timely (NT) filings
  - Friday after-hours filings
  - 8-K monthly spike signals
- Alert storage, deduplication, and API retrieval

## Target Capabilities (Pivot)
- Issuer-level review-priority score (ranked watchlist)
- Evidence payload for score explainability
- Forward-outcome backtesting using only public SEC data

## Setup
```bash
# Clone repository
git clone https://github.com/YOUR_USERNAME/sec-disclosure-risk-monitor.git
cd sec-disclosure-risk-monitor

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Test installation
python scripts/test_setup.py
```

If your GitHub repo is still named `sec-anomaly-detector`, rename it first in GitHub
Settings, then update your local remote URL:

```bash
git remote set-url origin git@github.com:YOUR_USERNAME/sec-disclosure-risk-monitor.git
git remote -v
```

## Ingestion Config
Backfill reads a company list from `data/companies.csv` by default (header: `ticker`).

Environment variables:
- `DB_BACKEND` - backend toggle: `postgres` or `sqlite`
- `DATABASE_URL` - RW DSN when `DB_BACKEND=postgres` (Supabase pooler URL, `sslmode=require`)
- `API_DATABASE_URL` - optional RO DSN when `DB_BACKEND=postgres`
- `API_AUTH_ENABLED` - set to `1` to require `X-API-Key` on all non-health API routes
- `API_KEY` - shared API key expected in `X-API-Key` when auth is enabled
- `DEMO_API_KEY` - optional helper env used by `scripts/demo_api_snapshot.py`
- `RISK_SCORING_MODE` - scoring mode (`monthly_abnormal` default, `alert_composite` legacy)
- `RISK_DEFAULT_MODEL_VERSION` - API default model selector (default `v2_monthly_abnormal`)
- `RISK_MONTHLY_HISTORY_MONTHS` - optional history window for monthly baseline (unset = all available history)
- `SEC_IDENTITY` - SEC identity string (recommended)
- `COMPANIES_CSV` - Optional path to a custom CSV file
- `BACKFILL_START_DATE` - optional explicit backfill start date (`YYYY-MM-DD`)
- `BACKFILL_DAYS` - optional window in days when `BACKFILL_START_DATE` is unset (default `730`)
- `DRY_RUN` - Set to `1` or `true` to skip DB writes while still fetching

Backend toggle examples:
```bash
# Hosted/Supabase profile
DB_BACKEND=postgres
DATABASE_URL="postgresql://app_rw.<project_ref>:<password>@<pooler-host>:5432/postgres?sslmode=require"
API_DATABASE_URL="postgresql://app_ro.<project_ref>:<password>@<pooler-host>:5432/postgres?sslmode=require"

# Local file profile
DB_BACKEND=sqlite
unset DATABASE_URL
unset API_DATABASE_URL
```

Quick smoke test (no DB writes):
```bash
cat > /tmp/companies_smoke.csv <<'EOF'
ticker
AAPL
MSFT
AMZN
EOF

DB_BACKEND=postgres \
DATABASE_URL="postgresql://app_rw:***@db.example.supabase.co:6543/postgres?sslmode=require" \
COMPANIES_CSV=/tmp/companies_smoke.csv \
DRY_RUN=1 \
SEC_IDENTITY="Your Name you@example.com" \
python src/ingestion/backfill.py
```

## Polling
GitHub Actions runs a daily full refresh pipeline (ingestion + analysis) against Supabase Postgres.
No SQLite DB artifact is committed by the workflow.

Workflow:
- `.github/workflows/poll.yml`
- Schedule: daily at `14:00 UTC` (morning US time zones)
- Also supports manual refresh via `workflow_dispatch` (recommended before live demos)

Weekly retention/maintenance:
- `.github/workflows/maintenance.yml`
- Schedule: Mondays at `13:00 UTC`
- Also supports manual refresh via `workflow_dispatch`

Run locally:
```bash
DB_BACKEND=postgres \
DATABASE_URL="postgresql://app_rw:***@db.example.supabase.co:6543/postgres?sslmode=require" \
DRY_RUN=1 \
SEC_IDENTITY="Your Name you@example.com" \
python src/ingestion/poll.py
```

Optional polling flags:
- `POLL_ENABLE_CATCHUP` - enable/disable stale-company catch-up (default `1`)
- `POLL_ENABLE_RISK_SCORING` - run issuer risk scoring after detections when new filings are inserted (default `1`)
- `POLL_ENABLE_INLINE_ANALYSIS` - run detections/scoring inside `poll.py` (default `1`)
- `POLL_ADVISORY_LOCK_NAME` - Postgres advisory lock name used to prevent overlapping runs (default `sec-daily-refresh`)
- `POLL_LOCK_PATH` - sqlite-only lock file path when `DB_BACKEND=sqlite` (default `.poller.lock`)
- `POLL_LOCK_TIMEOUT_SECONDS` - lock acquire timeout; `0` means non-blocking exit when another run holds the lock

## Apply Foundation Changes
For an existing Postgres deployment, apply the current foundation layer before switching public traffic to the API:

```bash
DB_BACKEND=postgres \
DATABASE_URL="postgresql://app_rw:***@db.example.supabase.co:6543/postgres?sslmode=require" \
./venv/bin/python src/db/init_db.py
```

This is idempotent and ensures the latest schema/index definitions are present, including the newer `issuer_risk_scores` read indexes.

Then set the runtime/deploy environment:
- `API_AUTH_ENABLED=1`
- `API_KEY=<strong shared secret>`
- `API_DATABASE_URL=<optional RO DSN for API reads>`
- `DEMO_API_KEY=<same key when using demo helpers>`

Client updates:
- Send `X-API-Key` on all non-health routes when auth is enabled.
- Use `include_evidence=false` on `/risk/top` and `/risk/{cik}/history` for watchlists and charts to avoid pulling large evidence blobs.

## Signal Detectors
Run detectors against the local DB:
```bash
python src/detection/nt_detection.py
python src/detection/friday_detection.py
python src/detection/spike_8k_detection.py
python src/detection/run_all.py
```

`8K_SPIKE` policy note:
- current daily runs evaluate the **current UTC month** only
- baseline remains strict (`5` prior months, `> mean + 2*std`, min `3` active baseline months)
- this avoids repeatedly surfacing stale historical spike months as "current"

## Review Priority Scoring
Build issuer-level review-priority scores from existing alerts:
```bash
python src/analysis/build_risk_scores.py
```

Default mode is `monthly_abnormal` (`v2_monthly_abnormal`):
- each issuer gets a score for the current trailing 30-day interval
- that interval is compared against the issuer's own prior-month average/std baseline
- ranking is driven by abnormal month-over-month lift (with Friday-burying and 8-K spike components included)

## What Is Actually Scored
Operational data lineage is:
- ingestion writes normalized filings into `filing_events`
- detectors read `filing_events` and write anomaly rows into `alerts`
- scoring reads `alerts` joined to `filing_events` and writes issuer rows into `issuer_risk_scores`
- `/risk/*` serves those persisted issuer score rows

Important scope notes:
- live leaderboard scoring does **not** use filing-text extraction
- live leaderboard scoring does **not** parse specific 8-K items like `4.02`
- outcome verification and filing-text review are validation-only workflows, not live ranking inputs

Detector eligibility:

| Signal | Eligible filings | Operational rule |
| --- | --- | --- |
| `NT_FILING` | any filing where `filing_type LIKE 'NT %'` | timeliness anomaly based on NT form type |
| `FRIDAY_BURYING` | `8-K`, `8-K/A`, `10-K`, `10-K/A`, `10-Q`, `10-Q/A` | filing timestamp lands on Friday at or after `4:00 PM` US/Eastern |
| `8K_SPIKE` | `8-K`, `8-K/A` only | current UTC month evaluated against prior 5 months using a 6-month filing-history window |

### Why Zero Counts Are Expected
- all tracked issuers receive a score row each day, even when they have no active anomaly signals
- signal-stack counts are counts of modeled anomaly alerts in the current scoring context, not raw filing totals over 2 years
- an issuer can have many historical filings in `filing_events` and still show `component 0.000 | count 0` if it has no qualifying current-window alerts
- `/risk/top` and `/risk/{cik}/history` can therefore return issuers with zero active signals; that is normal behavior, not a data-corruption signal

### 2-Year Backfill vs Live Score Windows
- the 24-month backfill reconstructs historical daily rows in `issuer_risk_scores`
- it does **not** mean today's score directly consumes 24 months of raw filings as one live input window
- current scoring still uses:
  - a trailing `30d` alert window
  - a trailing `90d` alert window
  - a monthly issuer baseline built from prior monthly scores
- the backfill gives you history to inspect; the live score still operates on recent alerts plus monthly baseline comparison

### How v2 Monthly-Abnormal Works
- short-window score comes from the current trailing 30-day alert mix
- the baseline is built from prior monthly issuer scores over available history unless `RISK_MONTHLY_HISTORY_MONTHS` is set
- `baseline_avg` is the average of prior monthly issuer scores
- `baseline_std` is the standard deviation of prior monthly issuer scores
- `relative_lift` measures how far the current interval score sits above the issuer's baseline average
- `zscore` measures how unusual the current interval score is relative to the issuer's baseline variability
- final formula: `0.35*current_interval_score_30d + 0.35*relative_lift_component + 0.30*zscore_component`
- this is not "same month last year versus this month"; it is current interval abnormality versus the issuer's prior monthly score distribution

Further reading:
- methodology/spec: [docs/Methodology.md](docs/Methodology.md)
- audit workflow: [docs/ScoreAuditGuide.md](docs/ScoreAuditGuide.md)

Optional as-of date:
```bash
python src/analysis/build_risk_scores.py --as-of-date 2026-02-23
```

Legacy mode (old recency-window composite) remains available:
```bash
python src/analysis/build_risk_scores.py --scoring-mode alert_composite --model-version v1_alert_composite
```

Run detectors + scoring as a separate scheduled analysis step:
```bash
python src/analysis/run_analysis.py
```

To fully split ingestion from analysis in cron:
```bash
# job 1: ingestion only
DB_BACKEND=postgres \
DATABASE_URL="postgresql://app_rw:***@db.example.supabase.co:6543/postgres?sslmode=require" \
POLL_ENABLE_INLINE_ANALYSIS=0 \
SEC_IDENTITY="Your Name you@example.com" \
python src/ingestion/poll.py

# job 2: analysis
DB_BACKEND=postgres DATABASE_URL="postgresql://app_rw:***@db.example.supabase.co:6543/postgres?sslmode=require" python src/analysis/run_analysis.py
```

Historical score reconstruction (daily snapshots across 24 months):
```bash
DB_BACKEND=postgres \
DATABASE_URL="postgresql://app_rw:***@db.example.supabase.co:6543/postgres?sslmode=require" \
BACKFILL_DAYS=730 \
python src/analysis/backfill_risk_scores.py
```

Monthly-abnormal backfill (default model):
```bash
DB_BACKEND=postgres \
DATABASE_URL="postgresql://app_rw:***@db.example.supabase.co:6543/postgres?sslmode=require" \
BACKFILL_DAYS=730 \
RISK_SCORING_MODE=monthly_abnormal \
python src/analysis/backfill_risk_scores.py
```

Recommended full-range reconstruction command:
```bash
caffeinate -dimsu ./venv/bin/python src/analysis/backfill_risk_scores.py \
  --start-date 2024-03-03 \
  --end-date 2026-03-04 \
  --scoring-mode monthly_abnormal \
  --model-version v2_monthly_abnormal \
  --progress-every 25
```

Pre-migration SQLite baseline export:
```bash
python scripts/export_sqlite_baseline.py --db-path data/sec_anomaly.db
```

## Review Priority API Endpoints
- `GET /risk/top` - ranked issuer review-priority scores (latest date by default)
- `GET /risk/{cik}/history` - historical scores for one issuer
- `GET /risk/{cik}/explain` - latest or date-specific evidence payload for one issuer
- `GET /alerts?cik=&anomaly_type=` - alert-level drilldown for score verification
- `GET /companies/{cik}/filings?filing_type=` - underlying filing rows for issuer verification

`/risk/top` defaults to `limit=50` for ranked output.
When `API_AUTH_ENABLED=1`, all routes except `/health` require `X-API-Key`.
Lean list/history mode:
- `/risk/top?include_evidence=false`
- `/risk/{cik}/history?include_evidence=false`
- In lean mode, `evidence` is returned as `null` and the endpoint skips loading the large evidence payload from storage.
- `GET /risk/{cik}/explain` always returns full evidence for one issuer/date.
- `/risk/top` and `/risk/{cik}/history` may include issuers with zero active signals; those rows are still valid score snapshots.
- evidence signal counts represent modeled anomaly-alert counts, not raw filing totals.
- filing-text verification is not part of the live score path.

Compatibility note: endpoint paths remain `/risk/*` during this phase to avoid client breakage.

## Leaderboard Dashboard
Minimal Streamlit dashboard entrypoint:

```bash
export DASHBOARD_API_BASE_URL="http://127.0.0.1:8000"
export DASHBOARD_API_KEY="$DEMO_API_KEY"  # optional; required when API auth is enabled
./venv/bin/streamlit run app.py
```

Current dashboard scope:
- live leaderboard via `/risk/top?include_evidence=false`
- issuer trend view via `/risk/{cik}/history?include_evidence=false`
- issuer explainability via `/risk/{cik}/explain`

## Outcome Label Import + Evaluation
```bash
python src/analysis/generate_outcome_candidates.py --output data/outcomes_candidates.csv
python src/analysis/verify_outcomes.py --input data/outcomes_candidates.csv --review-output data/outcomes_reviewed.csv --verified-output data/outcomes.csv --min-confidence-for-export HIGH
python src/analysis/import_outcomes.py --input data/outcomes.csv
python src/analysis/evaluate_review_priority.py
```

## Demo URL + Quick Check
Set your API URL (local or hosted):

```bash
export DEMO_URL="http://127.0.0.1:8000"
export DEMO_API_KEY="your-shared-key"  # optional; required when API_AUTH_ENABLED=1
```

Quick 2-minute check before demos:

```bash
# 1) API health and docs
curl -sS "$DEMO_URL/health" && echo
echo "$DEMO_URL/docs"

# 2) Pull latest top ranking and assert non-empty response
# If auth is disabled locally, omit the X-API-Key header.
curl -sS -H "X-API-Key: $DEMO_API_KEY" "$DEMO_URL/risk/top?include_evidence=false" > /tmp/risk_top.json
python - <<'PY'
import json
from pathlib import Path

payload = json.loads(Path("/tmp/risk_top.json").read_text())
items = payload.get("items", [])
print("as_of_date:", payload.get("as_of_date"))
print("total:", payload.get("total"))
print("returned_items:", len(items))
if not items:
    raise SystemExit("ERROR: /risk/top returned no ranking items")
top = items[0]
print("top_cik:", top.get("cik"))
print("top_score:", top.get("risk_score"))
print("top_rank:", top.get("risk_rank"))
PY
```

API snapshot (top list + issuer history + explain):
```bash
python scripts/demo_api_snapshot.py --base-url "$DEMO_URL" --limit 10 --api-key "$DEMO_API_KEY"
```

Backfill/coverage integrity report for `v2_monthly_abnormal`:
```bash
python scripts/validate_v2_backfill.py --model-version v2_monthly_abnormal --strict
```

Postgres storage prune (dry-run, defaults to removing `v1_alert_composite` rows):
```bash
python scripts/prune_postgres_data.py
```

Apply prune:
```bash
python scripts/prune_postgres_data.py --apply
```

Optional: also trim old feature snapshots (example keeps last 120 days):
```bash
python scripts/prune_postgres_data.py --feature-retention-days 120 --apply
```

Optional: persist the prune report to a file:
```bash
python scripts/prune_postgres_data.py --feature-retention-days 120 --apply --output docs/reports/retention/local_retention_report.json
```

## Notebooks
- `notebooks/01_signal_qc.ipynb` - signal quality checks and exploratory analysis
- `notebooks/02_risk_backtest.ipynb` - validation and backtesting workflow

## Documentation
- `docs/CodebaseSummary.md`
- `docs/Runbook.md`
- `docs/Methodology.md` (optional deep dive)
- `docs/Backtesting.md` (optional deep dive)
- `docs/Week1.md` and `docs/Week2.md` (historical planning notes)

Note: as of 2026-03-03, operational/runbook/spec details are unified in `docs/CodebaseSummary.md`.

## Project Structure
```text
sec-disclosure-risk-monitor/
├── src/
│   ├── db/
│   ├── ingestion/
│   ├── detection/
│   ├── analysis/
│   └── api/
├── scripts/
├── tests/
├── notebooks/
├── docs/
└── data/
```

## Author
Brandon Cheng
