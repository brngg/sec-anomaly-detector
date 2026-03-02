# Demo Runbook (Interview-Ready Daily Ranking)

This runbook is the fast checklist for keeping the public demo healthy.

## Goal
- Maintain a daily-updated issuer ranking demo from abnormal SEC filing behavior.
- Keep logic unchanged; only verify freshness and availability.

## Primary Demo URLs
- API docs: `http://127.0.0.1:8000/docs` (local default)
- Ranking endpoint: `http://127.0.0.1:8000/risk/top`

Set once in terminal:

```bash
export DEMO_URL="http://127.0.0.1:8000"
```

## Manual Refresh in GitHub Actions
Use this before interviews or if the daily run failed.

1. Open GitHub repository Actions tab.
2. Select workflow `SEC Daily Demo Refresh`.
3. Click `Run workflow`.
4. Select branch `main`.
5. Click `Run workflow` and wait for completion.

Expected pipeline order:
1. Run ingestion poller (`src/ingestion/poll.py` with inline analysis disabled)
2. Run analysis (`src/analysis/run_analysis.py`)
3. Quality gate (`issuer_risk_scores > 0`)
4. Commit/push updated `data/sec_anomaly.db`

## 2-Minute Freshness Check
Run these commands:

```bash
# 1) API health check
curl -sS "$DEMO_URL/health" && echo

# 2) Pull latest ranking payload
curl -sS "$DEMO_URL/risk/top" > /tmp/risk_top.json

# 3) Validate freshness signals
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
print("top_ticker:", top.get("company_ticker"))
PY
```

Pass criteria:
- `/health` returns HTTP 200.
- `/risk/top` returns HTTP 200.
- `as_of_date` is populated.
- `returned_items` is greater than 0 and at most 50 by default.
- Top record includes `cik`, `risk_score`, and `risk_rank`.

## Local Fallback (if hosting is unavailable)
Use this sequence to run demo locally from current repo state.

```bash
# Activate venv
source venv/bin/activate

# Ingestion only
POLL_ENABLE_INLINE_ANALYSIS=0 \
SEC_IDENTITY="Your Name you@example.com" \
python src/ingestion/poll.py

# Analysis
python src/analysis/run_analysis.py

# Serve API locally
uvicorn src.api.main:app --host 0.0.0.0 --port 8000
```

In another terminal:

```bash
curl -sS "http://127.0.0.1:8000/risk/top"
```

## Failure Handling
- If workflow fails at quality gate:
  - open workflow logs and confirm analysis step ran successfully.
  - confirm DB has scores:
    - `sqlite3 data/sec_anomaly.db "SELECT COUNT(*) FROM issuer_risk_scores;"`
  - rerun workflow manually after fixing any upstream issue.
- If API returns empty ranking:
  - confirm latest workflow run passed.
  - if hosted, confirm your deployment platform has pulled latest `main`.

## Pre-Interview Dry Run
1. Trigger a manual workflow refresh.
2. Confirm workflow success and non-empty `issuer_risk_scores`.
3. Open `/docs` and execute `/risk/top` live.
4. Keep `DEMO_URL`, `/docs`, and one sample `/risk/top` response ready for screen share.
