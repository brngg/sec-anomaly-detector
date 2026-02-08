# SEC Filing Anomaly Detection Platform

Automated detection of suspicious filing patterns in SEC Edgar data.

## Status
🚧 **In Development** - Week 1 Setup Complete

## Overview
This system monitors multiple public companies for anomalies in SEC filing behavior:
- Late filings
- Unusual 8-K bursts
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
