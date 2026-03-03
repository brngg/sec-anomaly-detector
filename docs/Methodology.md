# Methodology: Review Priority Index

## Objective
Rank issuers by near-term **review priority** using public SEC filing behavior.

## Scope
- This is a triage-prioritization system.
- It does not claim to prove securities fraud or legal violations.

## Data Sources
- SEC EDGAR filing metadata and form types
- Public issuer metadata (CIK, ticker, name)
- Publicly observable future outcomes used for validation labels

## Modeling Unit
- Issuer-time snapshot (daily as-of date per CIK)

## Signal Families
1. Timeliness signals
   Example: NT form events with recency weighting.
2. Timing-behavior signals
   Example: Friday after-hours filing behavior.
3. Frequency-shift signals
   Example: company-vs-self 8-K spike behavior.

## Feature Construction
- Fixed lookback windows (30/90 days).
- Recency decay with 30-day half-life.
- Signal normalization to comparable component ranges.
- Persisted feature snapshots and evidence payloads for auditability.

## Score Construction
- Weighted composite score in `[0, 1]`.
- Heuristic weights/scales in v1 with walk-forward validation and calibration.
- Evidence includes:
  - top signals
  - component-level math
  - top contributing source alerts
  - as-of timestamp and model metadata

## Output Contract
- Ranked issuer list by review-priority score
- Per-issuer trend history
- Per-issuer evidence payload linking score to source filing events

## Claims and Communication
- "Higher review-priority score is associated with higher rate of adverse future disclosure outcomes."

Not claming that:
- "Model proves fraud."
- "Model establishes legal liability."
- "Company is fundamentally risky as a business."
