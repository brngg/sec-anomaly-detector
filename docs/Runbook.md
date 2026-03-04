# Demo Runbook

## Objective
Demonstrate a defensible SEC review-priority system with:
1. Methodological rigor (clear signal semantics and score logic)
2. Operational reliability (automated daily pipeline)
3. Explainability (evidence-backed issuer ranking decisions)

## Pre-Demo Checklist
1. Confirm latest code on `main` is deployed in GitHub Actions.
2. Verify `DATABASE_URL_RW` and `SEC_IDENTITY` secrets are present.
3. Ensure latest workflow run on `main` is green:
   - ingestion
   - analysis
   - quality gate
4. Ensure `v2_monthly_abnormal` backfill coverage is complete.

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
python scripts/demo_api_snapshot.py --base-url "$DEMO_URL" --limit 10
```

## Live Demo Flow (5-8 minutes)
1. Open `/risk/top`
   - explain "review-priority, not fraud score"
   - show top ranked issuers and model version
2. Pick one issuer and open `/risk/{cik}/history`
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

## Acceptance Gates
1. 3 consecutive green daily workflow runs on `main`.
2. Full expected daily issuer coverage for `v2_monthly_abnormal`.
3. `8K_SPIKE` no longer replays stale historical month as current.
4. API snapshot script returns non-empty `/risk/top` and valid explain payload.

## Communication Guardrails
Use:
- "This ranks filing-behavior anomalies relative to issuer history."

Avoid:
- "This proves fraud."
- "This determines legal liability."
