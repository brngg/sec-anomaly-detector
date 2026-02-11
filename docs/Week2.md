# SEC Filing Project Plan — Week 2

Problem:
How can we differenciate what is normal for a specific company versus what is not normal? Each company has their own frequency of filling and we cannot compare equally or on a "flat number". Example company A files 16 filings on average while company B only files 7. Taking the average of multiple company filings the average might be 8 filings. Company A would be flagged but it is the case that 16 fillings is normal for company A.

Solutions:
Instead of comparing Company A to Company B, you compare Company A to Company A’s historical self.
    Normal: Anything within 2 "Standard Deviations" of its own history.
    Anomaly: Anything that breaks its own pattern, regardless of what other companies are doing.

    Mean ($\mu$): The average number of filings for this company (e.g., 16).Standard Deviation ($\sigma$): How much that number usually fluctuates (e.g., usually 14–18, so $\sigma = 2$).Threshold: A "Safety Zone" (usually $\mu + 2\sigma$).For Company A: $16 + (2 \times 2) = \mathbf{20}$.For Company B: $7 + (2 \times 1) = \mathbf{9}$.Result: If Company A files 19 times, it is Normal (below 20). If Company B files 10 times, it is an Anomaly (above 9). You have successfully differentiated them.



So what needs to be done
    Create a baseline that focuses on individual companies. This solves company A vs company B
    Create rules that will define what an "anomoly is" 
        (Friday burrying, any filing that happens after 4PM on a Friday)
        (NT Alert, automatically flag any form starting with "NT" (e.g., NT 10-K).)
        8-K Frequency Spike (Company vs. Self) 

## Baseline Calculation Details

**Time Window:** 6 months of historical data (matches our ingestion scope)
**Granularity:** Monthly counts (not weekly/daily to avoid noise)
**Minimum Data:** Require at least 3 months of history before flagging anomalies
   - Why? Can't calculate reliable μ and σ with only 1-2 data points
   - New companies or companies with sparse filings should be marked "INSUFFICIENT_DATA"

**Formula:**
- μ = mean monthly filing count for past 6 months
- σ = standard deviation of monthly counts
- Threshold = μ + 2σ
- Current month flagged if count > threshold

## Week 2 Implementation Order

**Day 1-2: NT Detector** (Easiest win)
- Detection: `filing_type.startswith('NT')`
- Severity: 0.9 (HIGH - missed deadline)
- Data needed: Just filing_type column
- No baseline required

**Day 2-3: Friday Burying Detector**
- Detection: `weekday == Friday AND hour >= 16:00 ET`
- Severity: 0.7 (MEDIUM - suspicious timing)
- Data needed: filed_at timestamp
- No baseline required
- Note: Check if edgartools provides time or just date

**Day 3-4: 8-K Frequency Spike Detector**
- Detection: Current month count > μ + 2σ for this company
- Severity: Variable based on how many σ above threshold
  - 2-3σ above: 0.6 (MEDIUM)
  - 3+ σ above: 0.8 (HIGH)
- Data needed: Historical filing_events for each CIK
- Requires baseline calculation per company

**Day 5: Integration & Testing**
- Wire all three detectors to poll.py
- Test with recent data
- Verify alert deduplication
- Document findings

## Edge Cases to Handle

**Frequency Spike Detector:**
- What if company has < 3 months of data?
  → Skip detector, mark as "INSUFFICIENT_BASELINE"
  
- What if σ = 0 (perfectly consistent filings)?
  → Use fallback: σ = μ × 0.1 (10% of mean)
  
- What about amendments (8-K/A)?
  → Count amendments WITH base form (8-K/A counts as 8-K)
  
- What about NT forms?
  → Do NOT count toward 8-K frequency (separate anomaly type)

**Friday Burying:**
- What if we only have date, not time?
  → Flag any Friday filing (less precise but still valuable)
  
- What about holidays (e.g., Good Friday)?
  → Friday + holiday = even more suspicious (future enhancement)

**NT Forms:**
- What if NT filing is followed by actual filing same day?
  → Both are flagged separately (NT = anomaly, actual filing = normal)
