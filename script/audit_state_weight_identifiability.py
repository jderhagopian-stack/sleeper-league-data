#!/usr/bin/env python3
"""Audit independent identifiability of competitive-state objective channels.

This is a research/governance finding, not a learned weight model. It asks what
historical or simulation target could independently identify each channel
without using that channel's own model score as its target.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
DATA=ROOT/"data"
OUT=DATA/"audit"/"state_weight_identifiability.json"

def load(path,default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default

def main():
    readiness=load(DATA/"audit"/"strategy_outcome_readiness.json",{})
    team_outcomes=load(DATA/"audit"/"historical_team_outcome_evidence.json",{})
    panel=load(DATA/"model_validation"/"historical_trade_outcome_panel.json",{})
    registry=load(DATA/"model_parameter_registry.json",{})
    state=next(
        (x for x in registry.get("parameters",[]) if x.get("id")=="STATE-WEIGHTS-001"),
        {}
    )

    current_evidence=bool(
        (team_outcomes.get("summary") or {}).get("team_level_realized_points_evidence_available")
        or (panel.get("summary") or {}).get("observed_team_outcome_panel_available")
    )
    pristine_market=bool(
        (readiness.get("summary") or {}).get("pristine_frozen_market_archive_detected")
    )
    pristine_projection=bool(
        (readiness.get("summary") or {}).get("pristine_frozen_projection_archive_detected")
    )

    channels={
        "current":{
            "independent_target_candidates":[
                "post-decision realized team points",
                "post-decision wins",
                "playoff/bye/championship outcomes",
                "starter games contributed",
            ],
            "direct_outcome_evidence_available":current_evidence,
            "causal_or_regret_identified":False,
            "identifiability":"PARTIAL",
            "reason":"Observed team outcomes exist, but without a defensible no-action/alternative counterfactual they do not isolate the marginal value of the decision.",
            "recommended_treatment":"RESEARCH_MULTI_OBJECTIVE_AND_SIMULATION_ABLATION",
        },
        "future":{
            "independent_target_candidates":[
                "6/12/24-month frozen market value",
                "realized pick outcomes",
                "retained franchise value",
                "age-adjusted value preservation",
            ],
            "pristine_point_in_time_market_archive_available":pristine_market,
            "identifiability":"NOT_PRISTINE",
            "reason":"Realized asset/pick outcomes can be observed, but a multi-season frozen contemporaneous market archive needed for clean value-preservation calibration is not yet available.",
            "recommended_treatment":"RETAIN_GOVERNED_PRIOR_OR_EXTERNAL_ANCHOR_PENDING_ARCHIVE",
        },
        "liquidity":{
            "independent_target_candidates":[
                "time-to-sale at contemporaneous fair value",
                "bid/ask or accepted/rejected offer dispersion",
                "realized option exercise under roster constraints",
                "transaction frequency conditional on opportunity",
            ],
            "offer_choice_denominator_available":False,
            "identifiability":"UNIDENTIFIED",
            "reason":"Completed trades alone do not identify liquidity preference or execution probability; transaction frequency without an opportunity denominator confounds demand, supply and asset quality.",
            "recommended_treatment":"HEAVILY_SHRUNK_PRIOR_OR_DIAGNOSTIC_ONLY",
        },
        "resilience":{
            "independent_target_candidates":[
                "team points/wins retained after realized injury/attrition shocks",
                "replacement cost after starter loss",
                "lineup-value retention under simulated unavailability",
            ],
            "historical_injury_counterfactual_target_available":False,
            "canonical_simulator_or_lineup_reoptimization_available":True,
            "identifiability":"SIMULATION_IDENTIFIABLE_PARTIAL",
            "reason":"Roster-specific replacement mechanics are identifiable by legal lineup reoptimization, but the strategic willingness to pay for resilience is normative and lacks an independent historical outcome target.",
            "recommended_treatment":"SIMULATION_DERIVED_DIAGNOSTIC_WITH_STRONG_SHRINKAGE_FOR_OBJECTIVE_WEIGHT",
        },
    }

    joint_ready=all(
        x["identifiability"] in {"IDENTIFIED","PRISTINE"}
        for x in channels.values()
    )

    report={
        "model_version":"FSFFL-State-Weight-Identifiability-1.0",
        "authority":"RESEARCH_AUDIT_NON_AUTHORITATIVE",
        "production_behavior_changed":False,
        "incumbent_status":state.get("status"),
        "channels":channels,
        "joint_weight_vector":{
            "empirically_identified":joint_ready,
            "four_channel_learned_curve_authorized":False,
            "current_future_only_challenger_research_authorized":True,
            "liquidity_resilience_ablation_required":True,
            "hierarchical_state_dependent_model_authorized_now":False,
            "reason":"Adding model complexity before independent channel targets exist would manufacture precision rather than identify strategy economics.",
        },
        "recommended_baselines":[
            "current expert prior",
            "equal four-channel baseline",
            "neutral current/future-only baseline",
            "incumbent current/future ratio with liquidity/resilience ablated",
        ],
        "policy":{
            "component_score_cannot_be_its_own_target":True,
            "transaction_frequency_without_opportunity_denominator_is_not_liquidity_probability":True,
            "simulation_can_identify_replacement_mechanics_but_not_normative_resilience_preference":True,
            "lack_of_four_channel_identifiability_is_valid_reason_to_prefer_simpler_model":True,
            "simpler_model_still_requires_outcome_validation_before_promotion":True,
        },
        "summary":{
            "current_partially_identifiable":channels["current"]["identifiability"]=="PARTIAL",
            "future_pristine_identifiable":channels["future"]["identifiability"]=="PRISTINE",
            "liquidity_independently_identified":channels["liquidity"]["identifiability"]=="IDENTIFIED",
            "resilience_normative_weight_identified":False,
            "four_channel_empirical_fit_ready":False,
            "current_future_only_research_is_better_identified":True,
            "authoritative_promotion_allowed":False,
        },
    }
    OUT.parent.mkdir(parents=True,exist_ok=True)
    OUT.write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(report["summary"],indent=2))

if __name__=="__main__":
    main()
