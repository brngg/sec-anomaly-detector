# Dashboard Data Contract (Pre-Platform)

## Goal
Drive a ranking dashboard with evidence-backed drilldown while keeping backend endpoints stable.

## Endpoint Surfaces
- `GET /risk/top`
- `GET /risk/{cik}/history`
- `GET /risk/{cik}/explain`
- `GET /alerts` (with `cik`, `date_from`, `date_to` filters)

## Ranking Table Fields (`/risk/top`)
Required:
- `cik`
- `company_name`
- `company_ticker`
- `risk_score` (review-priority score)
- `risk_rank`
- `percentile`
- `calibrated_review_priority` (nullable)
- `as_of_date`
- `model_version`
- `evidence.reason_summary`

## Evidence Detail Fields (`/risk/{cik}/explain`)
Required:
- `evidence.window_scores`
- `evidence.top_signals_30d`
- `evidence.component_breakdown`
- `evidence.top_contributing_alerts_30d`
- `evidence.score_math`
- `evidence.source_alerts_90d`

## Drilldown Behavior
1. User selects issuer row.
2. Dashboard calls `/risk/{cik}/explain` and renders top contributors.
3. Dashboard calls `/alerts?cik={cik}&date_from={as_of_date-30d}&date_to={as_of_date}`.
4. Dashboard cross-links contributor `alert_id` to alert cards.

## Stability Rules
- `/risk/*` paths are retained for compatibility.
- New evidence fields are additive and nullable-safe.
- Sorting is deterministic: `risk_rank`, then `risk_score`, then `cik`.

## Empty-State Rules
- If `/risk/top` has no items: show "No review-priority scores available yet."
- If contributors are empty: show "No high-contribution alerts in 30-day window."
- If calibrated score is null: hide probability badge and show "Calibration unavailable."
