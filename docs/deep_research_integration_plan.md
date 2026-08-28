# FSFFL Deep Research Integration Plan

## Purpose
Use deeper external research only where it can materially improve upstream model correctness, reduce uncertainty, or resolve a high-leverage design choice. Do not let research become a separate academic project or delay obvious fixes.

## Operating rule
For every research packet, answer five questions:
1. What model decision is being challenged?
2. What evidence would justify changing it?
3. What is the simplest defensible implementation?
4. Does the change materially affect downstream projections, simulations, values, or recommendations?
5. What refinement can safely be deferred?

A research packet ends in one of three actions: IMPLEMENT NOW, KEEP CURRENT / DOCUMENT, or DEFER FINAL 5%.

## Priority queue

### 1. Projection uncertainty and ensemble behavior — NOW
Decision targets:
- source aggregation method
- handling correlated/aggregate sources
- source disagreement as an uncertainty signal
- historical residual calibration by position/horizon
- current SD floors/CV fallbacks and related uncertainty constants

Implementation threshold:
Prefer simple equal-weight independent-source aggregation unless held-out evidence demonstrates a material improvement from a more complex method. Estimate uncertainty from forecast residuals where adequate history exists; retain bounded fallbacks only where data are insufficient.

### 2. Future-pick economics — NEXT
Decision targets:
- annual discounting
- time-to-draft appreciation
- expected pick-slot distributions
- class-strength adjustments
- liquidity/option value versus realized-player risk

Implementation threshold:
Use direct FSFFL transaction history/market evidence first where available, then broader dynasty-market evidence and analogous draft-capital research. Avoid assuming a universal discount rate if timing, pick range, or class quality materially changes the relationship.

### 3. Competitive-state / title-equity utility — AFTER FUTURE PICKS
Decision targets:
- whether contender/rebuilder utility should be linear, thresholded, or simulation-derived
- how marginal starter value translates into playoff/title equity
- whether strategic-state bonuses duplicate information already captured by simulation

Implementation threshold:
Prefer simulation-derived marginal championship/playoff equity when the simulator can produce it cheaply and reliably. External research should inform shape/diagnostics, not override direct league simulation evidence.

## Evidence hierarchy
1. FSFFL-specific direct evidence and time-frozen outcomes.
2. Relevant historical forecast/market data with temporal validation.
3. Strong external empirical evidence directly analogous to the model decision.
4. Research-supported proxy.
5. Bounded provisional assumption.

## Guardrails
- No output tuning to desired trades or rankings.
- No promotion of retrospectively retrieved data to strict out-of-sample status without pre-outcome certification.
- No double counting aggregate sources and their component sources as independent votes.
- No complex weighting merely because it is possible.
- Research should piggyback on validation already needed for implementation.
- Any upstream change receives propagation/sensitivity checks before merge.

## Initial external findings
- Long-horizon fantasy projection evidence across 2014-2025 finds simple aggregation more reliable than individual sources and no durable advantage from historical source weighting.
- Weekly 2015-2025 evidence similarly finds aggregation robust and weighted versus simple averages nearly interchangeable.
- Forecasting literature emphasizes calibration of predictive distributions and supports recalibration from historical residual behavior rather than treating realized outcome volatility as equivalent to forecast error.

These findings support the current 95%-now strategy: implement a governed independent-source ensemble and residual-based uncertainty before investing in sophisticated source weighting.
