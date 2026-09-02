# FSFFL Coefficient Provenance & Empirical Recalibration

## Status

PR A audit infrastructure. No production economic behavior is changed by this work.

## Relationship to existing governance

The existing `data/model_parameter_registry.json` remains the family-level model
governance authority. It describes high-leverage parameter families, evidence
status, uncertainty, downstream use, and recalibration needs.

This project adds a subordinate exact-site provenance layer so individual
weights, thresholds, multipliers, priors, defaults, curve knots, and runtime
precision parameters can be traced. It does not create a second utility,
valuation, ranking, acceptance, or recommendation system.

## Candidate inventory

`script/audit_authoritative_parameters.py` reads the existing application
architecture and family registry to discover governed production paths, then
uses Python AST inspection to enumerate candidate parameter sites.

The generated artifact is:

`data/audit/authoritative_parameter_inventory.json`

It is explicitly `AUDIT_ONLY_NON_AUTHORITATIVE`.

The scanner intentionally over-includes. A numeric literal is not automatically
a material model coefficient. Review must distinguish economic parameters from:

- rule-defined mechanics,
- runtime precision or computational budgets,
- descriptive thresholds,
- diagnostic-only values,
- legacy/dead code,
- and implementation constants with no material decision impact.

## Manual adjudication

Each candidate site must eventually receive:

- exact runtime authority,
- downstream consumers,
- evidence classification,
- provenance/source,
- whether it was hand-set,
- empirical/simulation/external basis,
- duplicate-signal status,
- uncertainty,
- sensitivity,
- estimated decision impact,
- replacement feasibility,
- identifiability class,
- recommended action,
- and evidence needed for further promotion.

The governing evidence hierarchy is:

1. RULE_DEFINED
2. HISTORICALLY_STATISTICALLY_ESTIMATED
3. EVIDENCE_BASED_EXTERNAL_ANCHOR
4. SIMULATION_DERIVED_ESTIMATE
5. REGULARIZED_OR_SHRINKAGE_ESTIMATE
6. EVIDENCE_SUPPORTED_PROVISIONAL_PRIOR
7. UNVALIDATED_EXPERT_PRIOR
8. LEGACY_ARBITRARY_HEURISTIC

## Identifiability

Parameters are separated into:

- DIRECTLY_ESTIMABLE
- SIMULATION_IDENTIFIABLE
- NORMATIVE_STRATEGIC
- UNIDENTIFIED_OR_DUPLICATE
- RULE_OR_RUNTIME_MECHANIC

Structural elimination is preferred when a parameter represents duplicate or
unidentified signal.

## Promotion policy

Promotion standards depend on incumbent evidence quality.

Strong empirically supported incumbents require material out-of-sample
improvement. Unvalidated expert priors may be displaced by clearly stronger,
bounded, uncertainty-aware evidence. Legacy arbitrary heuristics receive no
special incumbency advantage.

Software regression equivalence is not empirical validation.

## Independent calibration targets

Normative objective weights cannot be calibrated against a target built from
the same current/future/liquidity/resilience component scores they weight.

The project will construct independent historical strategy outcomes or use
multi-objective validation when a single scalar outcome is not defensible.

Hurts So Good is retained only as a familiar regression fixture and downstream
diagnostic. It is not a fitting target or correctness oracle.

## Next stages

1. Adjudicate the exact parameter inventory and map sites to family authority.
2. Build the historical strategy-outcome specification and data-readiness report.
3. Add monotonicity/invariance tests independent of current Opportunity rankings.
4. Build temporal holdout and shadow-mode comparison infrastructure.
5. Research candidates conservatively using regularization, shrinkage, simulation,
   monotonic constraints, and uncertainty intervals.
6. Promote only evidence-supported changes under the evidence-tiered policy.
