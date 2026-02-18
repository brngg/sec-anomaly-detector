# SEC Filing Anomaly Detection Platform

Automated detection of suspicious filing patterns in SEC Edgar data.

## Status
🚧 **In Development** - Week 2 Detection MVP in progress

## Overview
This system monitors public companies for anomalies in SEC filing behavior.

Current detections:
- Non-timely (NT) filings
- Friday after-hours filings (Friday burying)
- 8-K monthly spike alerts (company baseline)

Planned detections:
- Unusual 8-K bursts (spike detector)
- Suspicious timing patterns

## Setup
```bash
# Clone repository
git clone https://github.com/YOUR_USERNAME/sec-anomaly-detector.git
cd sec-anomaly-detector

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Test installation
python scripts/test_setup.py
```

## Ingestion Config
Backfill reads a company list from `data/companies.csv` by default (header: `ticker`).

Environment variables:
- `SEC_IDENTITY` — SEC identity string (recommended). If not set, a fallback identity is used with a warning.
- `COMPANIES_CSV` — Optional path to a custom CSV file.
- `DRY_RUN` — Set to `1` or `true` to skip DB writes while still fetching.

To persist env vars locally, copy `.env.example` to `.env` and edit values.

Example CSV:
```csv
ticker
AAPL
MSFT
AMZN
```

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

## Polling (cron)
This repo includes a GitHub Actions workflow that runs a poller every 15 minutes and commits
updates to `data/sec_anomaly.db` back to the repo.

Setup:
1. In GitHub, go to repo **Settings → Secrets and variables → Actions**.
2. Add a secret named `SEC_IDENTITY` with your SEC identity string.

Run locally (no DB writes):
```bash
DRY_RUN=1 SEC_IDENTITY="Your Name you@example.com" python src/ingestion/poll.py
```

### Poller behavior (hybrid)
The poller is designed for both speed and correctness:
- **Fast path:** Scans the full current filings feed (all pages), filters to tracked CIKs + forms.
- **Catch-up path:** For companies with stale/missing watermarks, queries filings since last seen.

Key env vars (optional):
- `POLL_ENABLE_CATCHUP` (default `1`)
- `POLL_CATCHUP_DAYS` (default `2`)
- `POLL_CATCHUP_COOLDOWN_HOURS` (default `48`) — prevents catch-up from running every poll
- `POLL_CURRENT_PAGE_SIZE` (default `100`)
- `POLL_FEED_BUFFER_HOURS` (default `6`) — safety buffer before ending feed scan early
- `POLL_LOOKBACK_DAYS` (default `14`) — used when watermark is missing

Note: the poller updates `watermarks` (and `poll_state`) even if no new filings are inserted,
so `data/sec_anomaly.db` can change on runs with `inserted=0`.

## Detections (local)
Run detectors against the local SQLite DB:
```bash
python src/detection/nt_detection.py
python src/detection/friday_detection.py
python src/detection/spike_8k_detection.py
```

## Validation (notebook)
Use the validation notebook to sanity-check alerts:
```
/Users/bcheng/Projects/sec-anomoly-detector/notebooks/validation.ipynb
```

## Project Structure
```
sec-anomaly-detector/
├── src/          # Source code
│   ├── db/           # Database modules
│   ├── ingestion/    # Data collection
│   ├── detection/    # Anomaly detection algorithms
│   ├── analysis/     # Analytics and backtesting
│   └── api/          # REST API
├── scripts/      # Executable scripts
├── tests/        # Unit tests
├── notebooks/    # Jupyter notebooks for exploration
├── docs/         # Documentation
└── data/         # Database and files (gitignored)
```

## Author
Brandon Cheng
