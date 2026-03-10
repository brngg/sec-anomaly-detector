# Score Audit Guide

This guide explains how to audit one dashboard issuer from UI to API to database without reading the implementation code.

## Data Lineage
Live leaderboard lineage is:

`filing_events -> alerts -> issuer_risk_scores -> /risk API -> app.py`

What each stage means:
- `filing_events`: normalized SEC filing metadata ingested from EDGAR
- `alerts`: modeled anomaly detections derived from those filings
- `issuer_risk_scores`: persisted issuer-level score, rank, percentile, and evidence
- `/risk` API: read layer over `issuer_risk_scores`
- `app.py`: dashboard that consumes `/risk/top`, `/risk/{cik}/history`, and `/risk/{cik}/explain`

Important scope note:
- live leaderboard scoring uses alert rows and filing metadata
- live leaderboard scoring does **not** use filing-text verification
- live leaderboard scoring does **not** filter 8-K items like `4.02`

## Dashboard Provenance
Dashboard data sources:
- leaderboard table: `GET /risk/top?include_evidence=false`
- history panel: `GET /risk/{cik}/history?include_evidence=false`
- selected issuer detail: `GET /risk/{cik}/explain`
- raw JSON panel: the existing `Raw Explain Payload` expander in the dashboard UI

What the dashboard signal stack means:
- `component`: modeled contribution of that signal family in the current score context
- `count`: modeled anomaly-alert count in the current score context
- these are not raw filing totals over 2 years

## How To Audit One Issuer
### 1. Identify the issuer
From the dashboard, note:
- `cik`
- `company_ticker`
- `as_of_date`
- `risk_score`
- `risk_rank`

### 2. Fetch the explain payload
Use the exact issuer CIK:

```bash
curl -H "X-API-Key: $API_KEY" \
  "http://127.0.0.1:8000/risk/<CIK>/explain" | jq .
```

Inspect:
- `score.reason_summary`
- `score.evidence.top_signals_monthly`
- `score.evidence.monthly_baseline`
- `score.evidence.top_contributing_alerts_30d`
- `score.evidence.score_math`

### 3. Fetch alert-level support
This shows which anomaly rows support the score:

```bash
curl -H "X-API-Key: $API_KEY" \
  "http://127.0.0.1:8000/alerts?cik=<CIK>&limit=200" | jq .
```

Optional filter by anomaly type:

```bash
curl -H "X-API-Key: $API_KEY" \
  "http://127.0.0.1:8000/alerts?cik=<CIK>&anomaly_type=8K_SPIKE&limit=200" | jq .
```

### 4. Fetch underlying filings
Use filing filters to verify the source filing rows for the issuer:

```bash
curl -H "X-API-Key: $API_KEY" \
  "http://127.0.0.1:8000/companies/<CIK>/filings?filing_type=8-K&limit=200" | jq .
```

```bash
curl -H "X-API-Key: $API_KEY" \
  "http://127.0.0.1:8000/companies/<CIK>/filings?filing_type=10-K&limit=200" | jq .
```

```bash
curl -H "X-API-Key: $API_KEY" \
  "http://127.0.0.1:8000/companies/<CIK>/filings?filing_type=10-Q&limit=200" | jq .
```

### 5. Compare directly in SQL when needed
Underlying filing rows:

```sql
SELECT accession_id, filing_type, filed_at, filed_date, primary_document
FROM filing_events
WHERE cik = <CIK>
ORDER BY filed_at DESC;
```

Alert support rows:

```sql
SELECT
  a.alert_id,
  a.anomaly_type,
  a.severity_score,
  a.event_at,
  a.created_at,
  f.accession_id,
  f.filing_type,
  f.filed_at
FROM alerts a
JOIN filing_events f ON f.accession_id = a.accession_id
WHERE f.cik = <CIK>
ORDER BY COALESCE(a.event_at, a.created_at) DESC;
```

Persisted issuer scores:

```sql
SELECT as_of_date, model_version, risk_score, risk_rank, percentile, evidence
FROM issuer_risk_scores
WHERE cik = <CIK>
ORDER BY as_of_date DESC
LIMIT 10;
```

### 6. Reconcile the row
When reconciling a dashboard row:
- the leaderboard value should match `/risk/top`
- the history chart should match `/risk/{cik}/history`
- the signal stack and reason summary should match `/risk/{cik}/explain`
- the alert rows should explain non-zero current signal counts
- the filing rows should explain where those alerts originated

## FAQ
### Why do I see `component 0.000 | count 0`?
Because the issuer has no active modeled anomaly alerts in the current scoring windows. This does not mean the issuer has no historical filings.

### Are 8-K items like `4.02` specifically filtered today?
No. The live score path does not currently filter 8-K filings by specific item codes.

### Does the 2-year backfill mean today's score uses 2 years of raw filings?
No. The 24-month backfill reconstructs historical daily score rows. The live score still uses recent alert windows plus monthly baseline comparison.

### Is filing text part of the live score?
No. Filing-text verification and outcome workflows are validation-only. Live scoring reads `alerts` joined to `filing_events`.

### Why can a company with historical filings have a zero score today?
Because all tracked issuers receive daily score rows, and an issuer can be quiet in the current scoring windows even if it has many historical filings.
