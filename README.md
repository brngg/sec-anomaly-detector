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
