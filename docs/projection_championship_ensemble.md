# FSFFL Projection Championship & Ensemble

## Objective

Improve every projection-dependent FSFFL application by selecting or blending the best available preseason projection evidence at the **position × raw-stat** level, while continuing to develop FSFFL Native as an independent projection system.

This workstream is calibration/research infrastructure. It does not change production projections until a separate governed promotion decision is made.

## Why category-level rather than provider-level

A projection system can be excellent at one target and mediocre at another. FSFFL therefore evaluates categories such as:

- QB passing attempts, passing yards, passing TDs, interceptions, rushing volume and rushing production;
- RB carries, rushing production, targets, receptions and receiving production;
- WR targets, receptions, receiving production and rushing production;
- TE targets, receptions and receiving production.

One source is not presumed to be best across all categories.

## Current evidence

The existing FSFFL Native challenger beats prior-year persistence on aggregate rolling holdouts, but a verified common-cohort benchmark against FFToday found FFToday materially stronger overall, especially at QB. Native TE won the limited verified TE comparison, but that evidence covers only one eligible season.

This means Native contains real signal but still misses preseason information captured by established external systems.

## V1 championship methods

For every position/stat category and eligible holdout season:

1. Admit only demonstrably preseason-frozen projections.
2. Match every compared source to the exact same player cohort and actual outcome.
3. Learn from seasons strictly earlier than the holdout.
4. Compare:
   - each individual source;
   - equal-weight blend;
   - prior-season training champion;
   - inverse-MAE blend shrunk toward equal weights.
5. Score on the held-out season.
6. Record the winner without changing production.

The shrinkage step is deliberately conservative. Small historical samples must not produce extreme source weights.

## FSFFL Native learning policy

External projections are **not** labels for training FSFFL Native.

They are used to:

- identify categories where Native underperforms;
- analyze residual errors;
- motivate preseason-known feature candidates;
- test whether Native contributes independent information to an ensemble.

Native remains trained against realized NFL outcomes using only information available before the target season.

## Source/deployment policy

Accuracy, access cost, independence, and usage rights are separate gates.

### Personal research

A source may be included when the contemplated private, noncommercial use is permitted or explicitly approved. Its provenance and commercial replacement status remain recorded.

### Commercial

A personal-use source does not automatically qualify for commercial FSFFL. Commercial deployment requires explicit applicable rights or a separate license. Otherwise the source must be replaced or excluded.

The architecture is intentionally source-swappable so licensing changes do not require redesigning the projection model.

## Initial source board

- **FSFFL Native** — independent internal challenger; not an external vote.
- **FFToday** — verified historical preseason snapshots already benchmarked.
- **Razzball** — current 2026 raw-stat source; historical frozen-series research required.
- **FantasyPros** — personal/noncommercial API access exists; historical frozen projection availability must be verified before calibration use.
- **ESPN** — retrieval mechanism identified; historical snapshot immutability remains unverified.
- **Additional sources** — may be added only through the governed candidate registry.

## Promotion standard

No category switches production because it wins one season or one aggregate metric.

A challenger or ensemble must:

- win leakage-safe later-season holdouts;
- preserve adequate player coverage;
- avoid material calibration degradation;
- show enough seasons/rows to avoid sample-size artifacts;
- pass source-independence and deployment-rights gates; and
- receive a separate governed production-promotion decision.

## Downstream impact

Once promoted, improved raw projections flow through the existing authoritative chain:

**raw player projections → FSFFL scoring → weekly/season projection layer → Simulator → GM/Trade/Opportunity/League Intelligence → reports**

The downstream applications consume the improved projection authority; they do not independently reweight or reinterpret the projection ensemble.
