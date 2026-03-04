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
- Also supports manual refresh via `workflow_dispatch` (recommended before interview demos)

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

## Signal Detectors
Run detectors against the local DB:
```bash
python src/detection/nt_detection.py
python src/detection/friday_detection.py
python src/detection/spike_8k_detection.py
python src/detection/run_all.py
```

## Review Priority Scoring
Build issuer-level review-priority scores from existing alerts:
```bash
python src/analysis/build_risk_scores.py
```

Optional as-of date:
```bash
python src/analysis/build_risk_scores.py --as-of-date 2026-02-23
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

Pre-migration SQLite baseline export:
```bash
python scripts/export_sqlite_baseline.py --db-path data/sec_anomaly.db
```

## Review Priority API Endpoints
- `GET /risk/top` - ranked issuer review-priority scores (latest date by default)
- `GET /risk/{cik}/history` - historical scores for one issuer
- `GET /risk/{cik}/explain` - latest or date-specific evidence payload for one issuer

`/risk/top` defaults to `limit=50` for interview-friendly ranked output.
Compatibility note: endpoint paths remain `/risk/*` during this phase to avoid client breakage.

## Outcome Label Import + Evaluation
```bash
python src/analysis/generate_outcome_candidates.py --output data/outcomes_candidates.csv
python src/analysis/verify_outcomes.py --input data/outcomes_candidates.csv --review-output data/outcomes_reviewed.csv --verified-output data/outcomes.csv --min-confidence-for-export HIGH
python src/analysis/import_outcomes.py --input data/outcomes.csv
python src/analysis/evaluate_review_priority.py
```

## Demo URL + Interview Quick Check
Set your API URL (local or hosted):

```bash
export DEMO_URL="http://127.0.0.1:8000"
```

Quick 2-minute check before interviews:

```bash
# 1) API health and docs
curl -sS "$DEMO_URL/health" && echo
echo "$DEMO_URL/docs"

# 2) Pull latest top ranking and assert non-empty response
curl -sS "$DEMO_URL/risk/top" > /tmp/risk_top.json
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

## Notebooks
- `notebooks/01_signal_qc.ipynb` - signal quality checks and exploratory analysis
- `notebooks/02_risk_backtest.ipynb` - validation and backtesting workflow

## Documentation
- `docs/CodebaseSummary.md`
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
