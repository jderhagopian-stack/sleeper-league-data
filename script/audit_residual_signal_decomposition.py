#!/usr/bin/env python3
"""Decompose currently-disabled economic diagnostics into residual vs duplicate parts.

This audit does not activate any production channel. Its purpose is to avoid the
opposite failure from the old model: we will not simply turn diagnostics back on
because the concepts sound economically reasonable.

For each family we ask whether the current implementation itself is eligible to
be a residual signal:
- player liquidity: current formula explicitly consumes market dynasty value,
  so it is NOT eligible as a residual liquidity primitive;
- optionality: mixed implementation; market-derived spread/momentum pieces are
  not eligible, while age/rookie/pedigree pieces may be candidates for a new
  bounded residual proxy;
- resilience: the legacy blend combines starter dependency with depth insurance.
  Starter dependency overlaps current lineup/simulator evidence; only the
  separately measurable depth-insurance residual remains a candidate;
- market tier scarcity/momentum/endowment remain excluded for their previously
  governed duplicate/wrong-role reasons.

The output is architecture/governance evidence, not a fitted coefficient.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"data/audit/residual_signal_decomposition_audit.json"


def main():
    findings=[
        {
            "family":"LIQUIDITY-RESIDUAL-001",
            "current_implementation":"_u_player_liquidity",
            "current_formula_dependencies":[
                "constant baseline",
                "market_dynasty / 8000",
                "position",
                "age",
            ],
            "duplicate_or_overlap":[
                "market_dynasty is the final future-value anchor",
                "age is already reflected in market prices to an unknown degree",
            ],
            "eligible_to_activate_current_formula":False,
            "residual_candidate":"Observed convertibility/retradeability after controlling for current market value, such as trade frequency, bid/ask or clearing spread, and breadth of manager demand.",
            "required_evidence":"Transaction/opportunity denominator and time-ordered residual test.",
        },
        {
            "family":"OPTIONALITY-RESIDUAL-001",
            "current_implementation":"_u_player_distribution_features.upside_optionality",
            "current_formula_dependencies":[
                "age/youth",
                "rookie/experience status",
                "market dynasty minus market redraft spread",
                "NFL draft pedigree",
                "same-source recent market trend",
                "young-QB heuristic bonus",
            ],
            "duplicate_or_overlap":[
                "market dynasty-redraft spread uses market coordinates already consumed by current/future utility",
                "same-source market trend cannot independently reprice current market value without residual validation",
                "young-QB bonus is hand-set",
            ],
            "potentially_distinct_components":[
                "age/experience horizon",
                "NFL draft pedigree",
                "asymmetric outcome distribution if independently estimated",
            ],
            "eligible_to_activate_current_formula":False,
            "residual_candidate":"Independently estimated asymmetric future distribution/reversibility after conditioning on current market dynasty value.",
            "required_evidence":"Historical frozen market values plus future value/outcome distributions; compare residual models on time-ordered holdouts.",
        },
        {
            "family":"RESILIENCE-RESIDUAL-001",
            "current_implementation":"0.62 * starter dependency + 0.38 * depth insurance",
            "current_formula_dependencies":[
                "single-starter lineup loss",
                "same-position depth-insurance loss",
            ],
            "duplicate_or_overlap":[
                "single-starter dependency is already represented by optimized starter evidence and current Simulator outcomes",
                "weekly availability/substitution is already simulated",
            ],
            "potentially_distinct_components":[
                "future/stress depth-insurance value beyond the current season",
            ],
            "eligible_to_activate_current_formula":False,
            "residual_candidate":"Depth-insurance-only stress value after ablating current lineup and Simulator substitution effects.",
            "required_evidence":"Simulation ablation across injury/availability stress scenarios and future-horizon roster states.",
        },
        {
            "family":"PACKAGE-CONCENTRATION-RESIDUAL-001",
            "current_implementation":"bounded inherited nonlinear package curves",
            "current_formula_dependencies":[
                "market dynasty values",
                "ordinal package position after sorting by value",
            ],
            "duplicate_or_overlap":[
                "must exclude forced cuts",
                "must exclude lineup replacement effects",
                "must not use market rank/scarcity as an additional premium",
            ],
            "eligible_to_activate_current_formula":False,
            "challenger_eligible":True,
            "reason":"Unlike liquidity/optionality/resilience, the residual question can be isolated structurally by replacing raw package additivity rather than adding a new value channel.",
            "required_evidence":"Continue bounded challenger validation; empirical magnitude still requires frozen transaction/choice evidence.",
        },
    ]

    gates={
        "no_current_disabled_formula_is_blanket_reenabled":all(
            x.get("eligible_to_activate_current_formula") is False for x in findings
        ),
        "liquidity_market_value_dependency_blocks_direct_activation":True,
        "optionality_mixed_signal_requires_decomposition":True,
        "resilience_dependency_component_must_not_be_counted_again":True,
        "package_challenger_replaces_additivity_instead_of_stacking":True,
    }
    payload={
        "schema_version":"1.0",
        "audit_family":"residual signal decomposition",
        "production_behavior_changed":False,
        "coefficient_fit_performed":False,
        "central_rule":"A credible concept does not make its legacy implementation eligible. Only a separately defined residual may receive bounded provisional authority.",
        "gates":gates,
        "findings":findings,
        "recommended_order":[
            "Complete package-concentration challenger validation because its residual boundary is already structurally isolated.",
            "Build a new optionality residual target from independently estimated future distributions rather than reviving the legacy optionality score.",
            "Build observed liquidity evidence conditional on current market value rather than reviving the market-value-derived liquidity formula.",
            "Ablate resilience to depth-insurance-only future/stress effects before considering any authority.",
        ],
    }
    OUT.parent.mkdir(parents=True,exist_ok=True)
    OUT.write_text(json.dumps(payload,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print(json.dumps({"passed":all(gates.values()),"findings":len(findings)},indent=2))
    if not all(gates.values()):
        raise SystemExit("residual signal decomposition gate failed")


if __name__=="__main__":
    main()
