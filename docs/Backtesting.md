# Backtesting Plan: Disclosure-Risk Monitor

## Goal
Measure whether higher-ranked issuers show higher rates of future adverse disclosure outcomes.

## Label Policy
- Define labels before evaluation and keep them fixed during a test run.
- Labels must be public and timestamped.
- Example label categories:
  - restatement-related disclosure outcomes
  - enforcement-related public outcomes

## Time Discipline
- Use walk-forward testing only.
- No look-ahead leakage:
  - features at time `t`
  - outcomes measured in windows after `t` (for example +90 or +180 days)

## Evaluation Metrics
1. `Precision@K`  
   Fraction of labeled outcomes among top-K ranked issuers.
2. `Lift@K`  
   `Precision@K / base_rate`.
3. `Recall@K`  
   Coverage of labeled outcomes captured by top-K set.
4. Calibration checks  
   Compare score buckets against observed outcome rates.

## Baselines
- Random ranking baseline
- Single-signal baseline (for example NT-only)
- Equal-weight multi-signal baseline

## Minimum Report Contents
1. As-of date and universe size
2. Label definition and outcome window
3. Metric table versus baselines
4. Confidence interval or bootstrap summary
5. Failure cases and false-positive examples

## Reproducibility
- Store evaluation config and code commit SHA.
- Store feature snapshot timestamp and label cutoff timestamp.
- Keep deterministic query windows and sorting rules.

## Interpretation Guardrails
- Strong backtest results support usefulness for triage, not legal certainty.
- Report uncertainty explicitly.
