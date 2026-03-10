# Methodology: SEC Review Priority Monitor

## Objective
Build a defensible SEC filing triage system that ranks issuers by **review priority** (not fraud probability), with auditable evidence and stable daily operation.

## Scope
- This system is for triage/prioritization.
- It does not establish legal liability.
- It does not estimate "fraud probability."

## Data Inputs
- SEC EDGAR filings and form metadata stored in `filing_events`. This is the filing source of truth for the live system.
- Issuer registry (`companies`).
- Derived detector alerts (`alerts`) for:
  - `NT_FILING`
  - `FRIDAY_BURYING`
  - `8K_SPIKE`
- `alerts` are the only direct live score inputs.
- Optional forward outcomes (`outcome_events`) for validation/calibration only.
- Filing-text verification and outcome generation are validation-only workflows. They do not feed the live leaderboard score.

## Signal Semantics
Ingestion form universe:
- `8-K`
- `8-K/A`
- `10-K`
- `10-K/A`
- `10-Q`
- `10-Q/A`
- `NT 10-K`
- `NT 10-Q`

Detector-specific form scope:
- `NT_FILING`
  - form filter: any filing where `filing_type LIKE 'NT %'`
  - semantics: timeliness anomaly based on late-notice form type
- `FRIDAY_BURYING`
  - form filter: `8-K`, `8-K/A`, `10-K`, `10-K/A`, `10-Q`, `10-Q/A`
  - semantics: Friday after-hours filing behavior based on filing timestamp
  - logic type: time-of-filing logic, not text logic
- `8K_SPIKE`
  - form filter: `8-K`, `8-K/A` only
  - semantics: company-vs-self frequency anomaly for 8-K activity
  - current policy: evaluate **current UTC month** only in daily runs
  - baseline: prior 5 months
  - trigger: `current_count > mean + 2*std`
  - filing-history window: 6 months of 8-K / 8-K/A filing history are used to decide whether the current month is abnormal
  - baseline sufficiency: at least 3 active baseline months

Important scope note:
- there is no item-level 8-K filtering in the current live score path
- specific 8-K items such as `4.02` are not currently filtered into or out of scoring

## Scoring Models
### Default model (production): `v2_monthly_abnormal`
- Daily issuer score is computed from live anomaly alerts, not filing text.
- Short and long modeled alert windows are `30d` and `90d`.
- Alert recency uses a `30 day` half-life.
- Weighted anomaly components:
  - `NT_FILING = 0.45`
  - `FRIDAY_BURYING = 0.20`
  - `8K_SPIKE = 0.35`
- Component scales:
  - `NT_FILING = 1.5`
  - `FRIDAY_BURYING = 2.5`
  - `8K_SPIKE = 1.2`
- Daily issuer score is anchored to the current trailing 30-day interval.
- That current interval is compared against the issuer's own prior monthly score history.
- Baseline terms:
  - `baseline_avg`: average of prior monthly issuer scores
  - `baseline_std`: standard deviation of prior monthly issuer scores
  - `relative_lift`: current interval score above baseline average, normalized by baseline level
  - `zscore`: current interval score above baseline average, normalized by baseline variability
- Final ranking score blends:
  - `0.35 * current_interval_score_30d`
  - `0.35 * relative_lift_component`
  - `0.30 * zscore_component`
- Baseline history uses all available prior monthly issuer scores unless `RISK_MONTHLY_HISTORY_MONTHS` is explicitly set.
- This is not a "same month last year versus this month" model; the comparison is against the issuer's prior monthly score distribution.
- Output is stored in `issuer_risk_scores`.

### Legacy model (fallback): `v1_alert_composite`
- 30/90-day recency-weighted alert composite.
- Retained for compatibility and side-by-side comparison.

## Explainability
Each score exposes evidence payloads used by `/risk/{cik}/explain`:
- top signal contributors
- component/breakdown math
- calibration metadata
- rank stability and uncertainty metadata
- monthly baseline diagnostics (default model)
- `top_signals_monthly` currently reflects the active signal mix used for the current modeled score context; it is not a raw 2-year filing-count report

## Validation and Calibration
- Validation lane uses outcome labels to evaluate lift/precision/recall.
- Calibration is applied only when sufficient class support exists; otherwise marked unavailable.

## Interpretation of Zero Rows
- Zero-signal rows mean "no active modeled anomaly alerts in the current scoring windows."
- They do **not** mean "no filings in issuer history."
- All tracked issuers receive score snapshots, so zero-score and zero-signal rows are expected for quiet issuers or quiet periods.

## Known Limits
- Public filings are delayed/noisy and not equivalent to internal issuer state.
- Alert sparsity can reduce short-term score separation.
- Current-month-only spike policy reduces stale replay but may underfire early in month.
- Outcome labels may lag, limiting immediate calibration confidence.

## Communication Guardrails
Safe claim:
- "Higher review-priority score indicates stronger filing-behavior anomalies relative to issuer history."

Unsafe claims to avoid:
- "This proves fraud."
- "This predicts legal guilt."
- "This is a business quality score."
