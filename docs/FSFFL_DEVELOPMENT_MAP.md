# FSFFL Development Map

**Purpose:** Canonical roadmap for the FSFFL modeling system. This document distinguishes true unfinished development from approved functionality that remains empirically refinable.

**Last established:** 2026-08-29

## Status definitions

- **PRODUCTION / COMPLETE** — Built, sufficiently validated for its intended role, and part of the canonical system.
- **ACTIVE DEVELOPMENT** — Material rebuild, calibration, or integration is currently underway.
- **APPROVED / REFINABLE** — Architecture and functionality are approved for use now. Parameters may be improved later if stronger evidence demonstrates meaningful incremental value. This status does **not** mean unfinished.
- **PLANNED DEVELOPMENT** — Valuable capability or material improvement identified but not yet built.
- **RESEARCH / EXPERIMENTAL** — Worth investigating, but evidence or implementation path is not mature enough for authoritative use.

Commercial readiness is tracked separately from model-development status. A model component can be production-ready while an underlying external data dependency still requires replacement, permission, or licensing for commercial deployment.

## Governing development principles

1. Preserve useful functionality unless it is structurally wrong, materially double-counted, or demonstrably harmful.
2. Do not replace one arbitrary coefficient with another merely to remove a hard-coded value.
3. Use the project evidence hierarchy: rule-defined; historically/statistically estimated; evidence-based external anchor; research-supported proxy; bounded assumption-sensitive provisional.
4. Promote learned or replacement parameters only when appropriate validation supports improvement.
5. Prioritize high-impact upstream improvements over marginal refinement.
6. Treat sensitivity as evidence of leverage, not proof that an approved component is unfinished.
7. Keep qualified experimental/counterfactual functionality clearly separated from factual outputs.

# Current architecture

## 1. Data foundation

| Component | Status | Remaining work | Commercial readiness |
|---|---|---|---|
| Sleeper / league-state ingestion | PRODUCTION / COMPLETE | Routine maintenance and portability | External dependency requires commercial-rights review/replacement as applicable |
| League/scoring/roster rules | PRODUCTION / COMPLETE | Maintain sync and portability tests | Rule data itself is structural; source terms still governed |
| Historical league reconstruction | PRODUCTION / COMPLETE | Extend history as new seasons accrue | Review any external-source provenance |
| Model parameter registry / governance | PRODUCTION / COMPLETE | Keep synchronized with promoted/replaced parameters | Supports commercial provenance audit |
| External player/market data layer | APPROVED / REFINABLE | Replace restricted dependencies where necessary; improve provenance | **Commercial-readiness work remains** |

## 2. Player intelligence

| Component | Status | Remaining work |
|---|---|---|
| Native player projection system | ACTIVE DEVELOPMENT | Finish projection means, uncertainty, validation, source architecture and final integration |
| Market-value anchor | PRODUCTION / COMPLETE | Continue source-quality/provenance monitoring; avoid same-source repricing |
| Market momentum | APPROVED / REFINABLE | Keep diagnostic/zero incremental weight unless time-ordered evidence demonstrates value beyond current market price |
| Prospect / rookie intelligence | PRODUCTION / COMPLETE, REFINABLE | Future expansion of breakout/bust/sleeper identification belongs in next-generation development |

## 3. Simulator

| Component | Status | Remaining work |
|---|---|---|
| Simulator 1.0 core | PRODUCTION / COMPLETE | Consume final native projection distributions once projection work closes |
| Playoff/seeding architecture | PRODUCTION / COMPLETE | Maintain rule portability |
| Outcome distributions | ACTIVE DEVELOPMENT via projection layer | Final uncertainty calibration is owned by projection workstream |

## 4. GM / franchise intelligence

| Component | Status | Remaining work |
|---|---|---|
| GM 3.0 architecture | PRODUCTION / COMPLETE | Integrate upstream improvements as promoted |
| Franchise-state weighting | APPROVED / REFINABLE | Empirical calibration if future evidence demonstrates meaningful held-out improvement; current sensitivity did not justify blocking use |
| Strategic adjustments: need/window/preference/endowment/starter-depth/etc. | APPROVED / REFINABLE | Family-level ablation is future refinement, not a production blocker |
| Roster interaction / insurance | APPROVED / REFINABLE | Replace bounded coefficients only when better evidence exists |
| Behavioral Intelligence | APPROVED / REFINABLE | Improve with larger time-ordered manager-action sample; remain secondary/qualified |

## 5. Trade Decision system

| Component | Status | Remaining work |
|---|---|---|
| Full owned-asset candidate discovery | PRODUCTION / COMPLETE | Maintain recall and runtime tests |
| Roster-aware trade legalization / replacement | PRODUCTION / COMPLETE | Continue exact/top-k optimization where computationally useful |
| Automatic cut selection | APPROVED / REFINABLE | Existing heuristic is acceptable as search accelerator; improve only if exact optimization shows meaningful regret |
| Same-source elite/consolidation repricing | PRODUCTION / COMPLETE — REMOVED | Do not reintroduce without held-out residual evidence |
| Multi-asset package/consolidation economics | APPROVED / REFINABLE | Nonlinear architecture retained; exact curve remains a future calibration opportunity. Generic roster-slot double count is removed |
| Future-pick economics | ACTIVE DEVELOPMENT | Complete early/mid/late scenarios, time discount, round liquidity, empirical calibration and double-count review |
| Acceptance / bilateral plausibility | APPROVED / REFINABLE | Keep as qualified negotiation realism, not calibrated acceptance probability; improve if defensible choice-opportunity data becomes available |
| Trade prescreen scoring | APPROVED / REFINABLE | Major recall cap fixed; surviving plausibility scoring can be refined later |
| Final trade-ranking primitive weights | APPROVED / REFINABLE | Revisit only after upstream projection/pick work settles and only where evidence supports improvement |
| Competitive externalities | APPROVED / REFINABLE | Maintain bounded/qualified role; empirical refinement later if useful |

## 6. Advanced analytics

| Component | Status | Remaining work |
|---|---|---|
| Historical trade analysis | PRODUCTION / COMPLETE | Continue accumulating historical evidence |
| What-If / Alternate History | APPROVED / REFINABLE | Preserve functionality; clearly label modeled branches/probabilities as counterfactual rather than fact |
| Team Improvement | APPROVED / REFINABLE | Move toward canonical utility when evidence/benefit warrants it |
| Legacy Decision Lab paths | APPROVED LEGACY / NONCANONICAL | Maintain only where needed; do not prioritize over canonical production chain |

## 7. Reporting

| Component | Status | Remaining work |
|---|---|---|
| Trade reports / decision outputs | PRODUCTION / COMPLETE | Consume final upstream projection and pick-model improvements |
| Runtime optimization | PRODUCTION / COMPLETE | Maintain full-fidelity runtime target and regression tests |
| Evidence / qualification disclosure | PRODUCTION / COMPLETE, CONTINUOUS | Keep provenance and provisional-status language synchronized with model registry |

# Immediate development path

1. **Finish native projection system** — active projection workstream; do not reopen unrelated approved audit components while this is underway.
2. **Finish future-pick economics** — complete the active empirical investigation and ensure pick quality, uncertainty, time discount and liquidity are not double counted.
3. **Integration and closure audit** — rerun canonical regressions after projection and pick changes; review every registry item and assign one of: Resolved, Approved/Refinable, Actually Open.
4. **Freeze the next production baseline** — version the integrated model, evidence artifacts and parameter registry.
5. **Rank future refinements by expected decision impact / development effort** rather than automatically working every provisional coefficient to exhaustion.

# Refinement backlog — not production blockers

These items are intentionally retained as approved functionality unless stronger evidence supports a material improvement:

- Exact franchise-state weights.
- Exact package/consolidation curve magnitudes.
- Strategic GM adjustment magnitudes and family-level ablations.
- Roster-interaction/insurance coefficients.
- Behavioral blend weights and confidence curves.
- Acceptance-fit coefficients/bands, provided they remain negotiation-realism signals rather than claimed probabilities.
- Automatic-cut heuristic weights where exact optimization is unnecessary.
- Remaining trade-prescreen plausibility thresholds.
- Final primitive-channel trade-ranking weights.
- Team Improvement ranking refinements.
- Counterfactual branch probabilities in What-If / Alternate History.

# Next-generation development

After the current production baseline closes, prioritize new capabilities and large expected gains before polishing low-impact coefficients.

Potential next-generation tracks:

- **Breakout / hidden-gem engine:** identify off-radar players whose role, usage, prospect profile, depth-chart movement, injuries around them, camp/preseason evidence or market lag imply a material probability of a value breakout.
- **Bust / deterioration detection:** identify players whose market value has not caught up to role loss, efficiency deterioration, age/health risk, competition or structural team changes.
- **Buy-low / sell-high intelligence:** separate short-term narrative/market movement from changes in underlying expected dynasty value.
- **Prospect intelligence expansion:** empirically identify traits and combinations associated with NFL/fantasy hits, misses, late breakouts and market mispricing.
- **Trade-history learning:** use frozen contemporaneous values and subsequent outcomes to improve package economics and decision evaluation without hindsight leakage.
- **Manager modeling:** improve opponent-specific negotiation predictions as the league accumulates a larger action history.
- **Commercial productization:** replace/relicense restricted sources, harden provenance, portability, testing, documentation, interfaces and user-facing qualification.

# Commercial-readiness overlay

Commercial readiness is **not** synonymous with model completion. Before commercial deployment:

- Inventory every external data dependency and its terms/license.
- Replace or license dependencies that do not permit intended commercial use.
- Review provenance of coefficients derived from external inputs.
- Ensure commercially deployed projections and depth-chart/role inputs come from permitted sources.
- Preserve a versioned source/provenance registry alongside each production model release.

# Closure rule

A component should be classified as **Actually Open** only when at least one of the following is true:

- required functionality is not implemented;
- a known structural error or material double count remains;
- a high-impact upstream component is undergoing an approved rebuild/calibration;
- validation has shown the current implementation materially harms decisions; or
- the component cannot perform its intended role without the missing work.

A component is **Approved / Refinable**, not open, when it performs its intended role with bounded and disclosed assumptions and further work would principally improve precision rather than correct a known material defect.
