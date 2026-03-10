# Demo Runbook

## Objective
Demonstrate a defensible SEC review-priority system with:
1. Methodological rigor (clear signal semantics and score logic)
2. Operational reliability (automated daily pipeline)
3. Explainability (evidence-backed issuer ranking decisions)

## Pre-Demo Checklist
1. Confirm latest code on `main` is deployed in GitHub Actions.
2. Verify `DATABASE_URL_RW`, `SEC_IDENTITY`, `API_AUTH_ENABLED`, and `API_KEY` secrets are present.
3. Ensure latest workflow run on `main` is green:
   - ingestion
   - analysis
   - quality gate
4. Ensure weekly maintenance has run at least once and produced a retention artifact.
5. Ensure `v2_monthly_abnormal` backfill coverage is complete.

## Required Commands
### 1) Full v2 backfill (24 months)
```bash
caffeinate -dimsu ./venv/bin/python src/analysis/backfill_risk_scores.py \
  --start-date 2024-03-03 \
  --end-date 2026-03-04 \
  --scoring-mode monthly_abnormal \
  --model-version v2_monthly_abnormal \
  --progress-every 25
```

### 2) Coverage and continuity validation
```bash
python scripts/validate_v2_backfill.py --model-version v2_monthly_abnormal --strict
```

### 3) API snapshot for talking points
```bash
export DEMO_URL="http://127.0.0.1:8000"
export DEMO_API_KEY="your-shared-key"
python scripts/demo_api_snapshot.py --base-url "$DEMO_URL" --limit 10 --api-key "$DEMO_API_KEY"
```

## Live Demo Flow (5-8 minutes)
1. Open `/risk/top?include_evidence=false`
   - explain "review-priority, not fraud score"
   - show top ranked issuers and model version
2. Pick one issuer and open `/risk/{cik}/history?include_evidence=false`
   - show trend persistence/spikes over time
3. Open `/risk/{cik}/explain`
   - show top drivers and baseline-relative evidence
   - highlight uncertainty/calibration metadata
4. Close with guardrails:
   - triage signal, not legal conclusion

## Suggested Case Study Mix
1. Persistent high-priority issuer
2. Newly elevated issuer
3. Stable low-priority issuer

## Issuer Verification Workflow
1. Identify the issuer `cik` from the dashboard or `/risk/top?include_evidence=false`.
2. Fetch `/risk/{cik}/explain`.
3. Inspect:
   - `reason_summary`
   - `top_signals_monthly`
   - `monthly_baseline`
   - `top_contributing_alerts_30d`
4. Fetch `/alerts?cik=<CIK>` to see the supporting anomaly rows.
5. Fetch `/companies/{cik}/filings?filing_type=8-K`, `10-K`, or `10-Q` as needed to inspect source filing rows.
6. Compare SQL directly if there is still a discrepancy.

Useful API commands:
```bash
curl -H "X-API-Key: $API_KEY" "http://127.0.0.1:8000/risk/<CIK>/explain" | jq .
curl -H "X-API-Key: $API_KEY" "http://127.0.0.1:8000/alerts?cik=<CIK>&limit=200" | jq .
curl -H "X-API-Key: $API_KEY" "http://127.0.0.1:8000/companies/<CIK>/filings?filing_type=8-K&limit=200" | jq .
```

Useful SQL checks:
```sql
SELECT accession_id, filing_type, filed_at, filed_date
FROM filing_events
WHERE cik = <CIK>
ORDER BY filed_at DESC;

SELECT a.alert_id, a.anomaly_type, a.severity_score, a.event_at, f.accession_id, f.filing_type
FROM alerts a
JOIN filing_events f ON f.accession_id = a.accession_id
WHERE f.cik = <CIK>
ORDER BY COALESCE(a.event_at, a.created_at) DESC;

SELECT as_of_date, risk_score, risk_rank, percentile, evidence
FROM issuer_risk_scores
WHERE cik = <CIK> AND model_version = 'v2_monthly_abnormal'
ORDER BY as_of_date DESC
LIMIT 10;
```

## Acceptance Gates
1. 3 consecutive green daily workflow runs on `main`.
2. Full expected daily issuer coverage for `v2_monthly_abnormal`.
3. `8K_SPIKE` no longer replays stale historical month as current.
4. API snapshot script returns non-empty `/risk/top` and valid explain payload.
5. Authenticated calls with `X-API-Key` succeed on non-health routes.

## Communication Guardrails
Use:
- "This ranks filing-behavior anomalies relative to issuer history."

Avoid:
- "This proves fraud."
- "This determines legal liability."

## Troubleshooting
- Zero signal-stack rows do not mean historical filings are missing.
- They mean the issuer has no active modeled alerts in the current score windows.
- This is expected for low-priority issuers and for periods with no qualifying anomalies.
- The 24-month backfill reconstructs historical score snapshots; it does not mean today's score directly uses 24 months of raw filings as a live input window.
