# Methodology: Disclosure-Risk Early Warning Index

## Objective
Rank issuers by near-term disclosure risk using only public SEC data.

## Scope
- This system is a risk-prioritization tool.
- It does not claim to prove securities fraud or legal violations.

## Data Sources
- SEC EDGAR filing metadata and form types
- Public issuer metadata (CIK, ticker, name)
- Publicly observable future outcomes used for evaluation labels

## Modeling Unit
- Issuer-time snapshot (for example daily or weekly per CIK), not only single filing events.

## Signal Families
1. Timeliness signals  
   Example: NT form events and recency.
2. Timing-behavior signals  
   Example: Friday after-hours filing density.
3. Frequency-shift signals  
   Example: company-vs-self 8-K spike behavior.

## Feature Construction
- Build fixed lookback windows (for example 30/90 days).
- Normalize signal magnitudes to comparable ranges.
- Store all raw feature values for reproducibility and auditability.

## Score Construction
- Weighted composite score in `[0, 1]`.
- Weights can start heuristic and later be tuned with walk-forward validation.
- Every score record must include explainability fields:
  - top contributing signals
  - underlying counts and lookback windows
  - as-of timestamp

## Output Contract
- Ranked issuer list by risk score
- Per-issuer risk trend history
- Per-issuer evidence payload linking score to source filing events

## Claims and Communication
Allowed:
- "Higher risk score is associated with higher rate of adverse future disclosure outcomes."

Not allowed:
- "Model proves fraud."
- "Model establishes legal liability."
