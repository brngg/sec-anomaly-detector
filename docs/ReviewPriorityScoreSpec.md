# Review Priority Score Specification

## Purpose
`Review Priority Score` ranks issuers by near-term filing review priority using public SEC filing behavior anomalies.

This is a triage signal for analyst attention.

## What It Is Not
- Not fraud probability
- Not legal-liability determination
- Not business quality/credit risk score

## Allowed Claim
Higher review-priority scores are associated with higher rates of future adverse disclosure outcomes in walk-forward evaluation.

## Model Version
- `model_version`: `v1_alert_composite`
- Score range: `[0.0, 1.0]`
- Scoring cadence: daily `as_of_date`

## Inputs
- Alert anomaly types:
  - `NT_FILING`
  - `FRIDAY_BURYING`
  - `8K_SPIKE`
- Alert severity (`[0,1]`)
- Alert timestamp recency

## Equations
1. Recency decay:
   - `recency_weight(age_days) = exp(-ln(2) * age_days / 30)`
2. Per-signal weighted severity in a window:
   - `weighted_severity(signal, window) = sum(severity_i * recency_weight_i)`
3. Per-signal component:
   - `component(signal, window) = min(weighted_severity / signal_scale, 1.0)`
4. Window score:
   - `window_score(window) = sum(component * anomaly_weight) / sum(anomaly_weight)`
5. Final score:
   - `review_priority_score = sum(window_score * window_weight) / sum(window_weight)`

## Parameters
- Lookback windows: `30d`, `90d`
- Window weights:
  - `30d`: `0.65`
  - `90d`: `0.35`
- Anomaly weights:
  - `NT_FILING`: `0.45`
  - `FRIDAY_BURYING`: `0.20`
  - `8K_SPIKE`: `0.35`
- Saturation scales:
  - `NT_FILING`: `1.5`
  - `FRIDAY_BURYING`: `2.5`
  - `8K_SPIKE`: `1.2`

## Evidence Contract
Each score record stores evidence with:
- `window_scores`
- `top_signals_30d`
- `source_alerts_90d`
- `component_breakdown` (signal-level math per window)
- `score_math` (formulas and constants)
- `top_contributing_alerts_30d` (source-linked alert contributors)
- `reason_summary`
- optional `calibrated_review_priority`

## Communication Bands (Operational)
- `0.00-0.25`: low review urgency
- `0.25-0.50`: watch
- `0.50-0.75`: elevated
- `0.75-1.00`: highest priority for immediate review

Bands are communication aids and do not change ranking logic.

## Manager Script
- Use: "This score prioritizes what to review first based on unusual filing patterns."
- Avoid: "This proves wrongdoing" or "This company is legally risky."
