# Methodology: SEC Review Priority Monitor

## Objective
Build a defensible SEC filing triage system that ranks issuers by **review priority** (not fraud probability), with auditable evidence and stable daily operation.

## Scope
- This system is for triage/prioritization.
- It does not establish legal liability.
- It does not estimate "fraud probability."

## Data Inputs
- SEC EDGAR filings (`filing_events`) and form metadata.
- Issuer registry (`companies`).
- Derived detector alerts (`alerts`) for:
  - `NT_FILING`
  - `FRIDAY_BURYING`
  - `8K_SPIKE`
- Optional forward outcomes (`outcome_events`) for validation/calibration only.

## Signal Semantics
- `NT_FILING`: timeliness anomaly.
- `FRIDAY_BURYING`: Friday after-hours filing behavior.
- `8K_SPIKE`: company-vs-self frequency anomaly for 8-K/8-KA.
  - Current policy: evaluate **current UTC month** only in daily runs.
  - Baseline: prior 5 months.
  - Trigger: `current_count > mean + 2*std`.
  - Baseline sufficiency: at least 3 active baseline months.

## Scoring Models
### Default model (production): `v2_monthly_abnormal`
- Daily issuer score computed from the current trailing 30-day interval.
- Compared against each issuer's own prior-month baseline (mean/std).
- Final ranking score blends:
  - current interval level
  - relative lift vs baseline
  - z-score vs baseline variability
- Output stored in `issuer_risk_scores`.

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

## Validation and Calibration
- Validation lane uses outcome labels to evaluate lift/precision/recall.
- Calibration is applied only when sufficient class support exists; otherwise marked unavailable.

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
