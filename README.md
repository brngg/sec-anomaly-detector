# SEC Disclosure-Risk Early Warning Index

Public-data system for monitoring issuer disclosure risk using SEC EDGAR filings.

## Positioning
This project does not attempt to establish legal proof of fraud.  
It produces auditable, pre-enforcement risk signals to help prioritize issuer review.

## Status
Pivot in progress from event-level anomaly alerts to issuer-level risk monitoring.

## Current Capabilities
- SEC ingestion and polling for tracked issuers
- Event-level signal generation:
  - non-timely (NT) filings
  - Friday after-hours filings
  - 8-K monthly spike signals
- Alert storage, deduplication, and API retrieval

## Target Capabilities (Pivot)
- Issuer-level disclosure-risk score (ranked watchlist)
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
- `SEC_IDENTITY` - SEC identity string (recommended)
- `COMPANIES_CSV` - Optional path to a custom CSV file
- `DRY_RUN` - Set to `1` or `true` to skip DB writes while still fetching

Quick smoke test (no DB writes):
```bash
cat > /tmp/companies_smoke.csv <<'EOF'
ticker
AAPL
MSFT
AMZN
EOF

COMPANIES_CSV=/tmp/companies_smoke.csv \
DRY_RUN=1 \
SEC_IDENTITY="Your Name you@example.com" \
python src/ingestion/backfill.py
```

## Polling
GitHub Actions runs the poller every 15 minutes and commits DB updates to `data/sec_anomaly.db`.

Run locally:
```bash
DRY_RUN=1 SEC_IDENTITY="Your Name you@example.com" python src/ingestion/poll.py
```

Optional polling flags:
- `POLL_ENABLE_CATCHUP` - enable/disable stale-company catch-up (default `1`)
- `POLL_ENABLE_RISK_SCORING` - run issuer risk scoring after detections when new filings are inserted (default `1`)
- `POLL_ENABLE_INLINE_ANALYSIS` - run detections/scoring inside `poll.py` (default `1`)
- `POLL_LOCK_PATH` - lock file path used to prevent overlapping poll runs (default `.poller.lock`)
- `POLL_LOCK_TIMEOUT_SECONDS` - lock acquire timeout; `0` means non-blocking exit when another run holds the lock

## Signal Detectors
Run detectors against the local DB:
```bash
python src/detection/nt_detection.py
python src/detection/friday_detection.py
python src/detection/spike_8k_detection.py
python src/detection/run_all.py
```

## Risk Scoring
Build issuer-level disclosure-risk scores from existing alerts:
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
POLL_ENABLE_INLINE_ANALYSIS=0 SEC_IDENTITY="Your Name you@example.com" python src/ingestion/poll.py

# job 2: analysis
python src/analysis/run_analysis.py
```

## Risk API Endpoints
- `GET /risk/top` - ranked issuer risk scores (latest date by default)
- `GET /risk/{cik}/history` - historical risk scores for one issuer
- `GET /risk/{cik}/explain` - latest or date-specific evidence payload for one issuer

## Notebooks
- `notebooks/01_signal_qc.ipynb` - signal quality checks and exploratory analysis
- `notebooks/02_risk_backtest.ipynb` - validation and backtesting workflow

## Documentation
- `docs/CodebaseSummary.md`
- `docs/Methodology.md`
- `docs/Backtesting.md`
- `docs/Week1.md` and `docs/Week2.md` (historical planning notes)

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
