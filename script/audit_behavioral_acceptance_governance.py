#!/usr/bin/env python3
"""Governance audit for manager behavior and acceptance modeling.

No production scores are changed here. The audit distinguishes descriptive
revealed-preference evidence from calibrated acceptance probability and flags
signal reuse that must be resolved in the final-ranking stage.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/"data"; OUT=DATA/"audit"; OUT.mkdir(parents=True,exist_ok=True)
BI3=ROOT/"script"/"behavioral_intelligence_v3.py"
V24=ROOT/"script"/"run_trade_market_sweep_v24.py"
V26=ROOT/"script"/"run_trade_market_sweep_v26.py"
V23=ROOT/"script"/"run_trade_market_sweep_v23.py"\nV16=ROOT/"script"/"run_trade_market_sweep_v16.py"\nINTEGRATION=ROOT/"script"/"trade_decision"/"behavior_integration.py"\nBILATERAL=ROOT/"script"/"trade_bilateral_gate.py"
RANKER=ROOT/"script"/"negotiation_ranking.py"
READINESS=OUT/"transaction_evidence_readiness_audit.json"
REGISTRY=DATA/"model_parameter_registry.json"
MODEL_VERSION="FSFFL-Behavioral-Acceptance-Governance-2.0"

def load(path,default=None):
    if not path.exists(): return default
    return json.loads(path.read_text(encoding="utf-8"))

def main():
    bi3=BI3.read_text(encoding="utf-8")
    v24=V24.read_text(encoding="utf-8")
    v26=V26.read_text(encoding="utf-8")
    v23=V23.read_text(encoding="utf-8")\n    v16=V16.read_text(encoding="utf-8")\n    integration=INTEGRATION.read_text(encoding="utf-8")\n    bilateral=BILATERAL.read_text(encoding="utf-8")
    ranker=RANKER.read_text(encoding="utf-8")
    readiness=load(READINESS,{}) or {}
    registry=load(REGISTRY,{}) or {}
    params={str(x.get("id")):x for x in (registry.get("parameters") or [])}

    behavior_markers=all(x in bi3 for x in [
        'SOURCE_WEIGHT = {"trade": 1.0, "draft": 1.0, "acquisition": 1.0}',
        'OPPORTUNITY_SMOOTHING = 1.0', 'NEED_FLOOR = .30',
        'def shrinkage_factor(weight, prior_strength)',
        'statistics.median(positive_weights)',
        'prior_strength_basis": "median positive manager weighted context sample in the current build"',
        'leave_one_manager_out_opportunity_prior": True',
    ])
    behavior_bounded=all(x in integration for x in [
        'def combine_behavior_signals',
        'confidence_weighted_boundary_shrinkage',
        'behavioral_intelligence_can_override_current_state_utility',
    ])
    legacy_buyer_state_floors=any(x in v16 for x in [
        'title_floor = -0.04', 'title_floor = -0.05', 'title_floor = -0.10'
    ])
    continuous_buyer_gate=all(x in bilateral for x in [
        'FSFFL-Bilateral-Buyer-Gate-2.0',
        'buyer_decision_utility_score',
        'categorical_state_thresholds_authoritative',
    ])
    acceptance_band_markers=all(x in v24 for x in [
        'return "HIGH" if score >= .68 else "MEDIUM" if score >= .48 else "LOW" if score >= .28 else "VERY_LOW"',
        'adjustment = clamp(raw * evidence_weight * .14, -.14, .14)',
    ])
    final_signal_reuse=not (
        'OWNER_BEHAVIOR_WEIGHT = 0.0' in ranker
        and 'STRATEGIC_WEIGHT = 1.0' in ranker
        and 'ACCEPTANCE_WEIGHT = 0.0' in ranker
        and 'arbitrary_strategic_acceptance_exchange_rate_authorized": False' in ranker
    )
    acceptance_ready=False
    for x in readiness.get("findings",[]):
        if x.get("id")=="ACCEPTANCE-CALIBRATION-READINESS-001":
            acceptance_ready=bool(x.get("authoritative_empirical_claim_allowed"))
    breg=params.get("BEHAVIOR-001") or {}; areg=params.get("ACCEPTANCE-GATE-001") or {}
    registry_ok=(
        breg.get("evidence_tier")=="REGULARIZED_OR_SHRINKAGE_ESTIMATE" and breg.get("authoritative_use") is False
        and areg.get("authoritative_use") is False
    )

    findings=[
      {
        "id":"BEHAVIOR-PREFERENCE-001","severity":"HIGH",
        "status":"BOUNDED_RESEARCH_SIGNAL_NOT_PREDICTIVELY_VALIDATED",
        "observation":"Observed manager actions now use adaptive league-sample shrinkage and confidence-weighted Trade Decision integration. Remaining opportunity smoothing/need priors are still provisional; leave-one-manager-out construction controls leakage but does not establish time-ordered predictive validity.",
        "authoritative_predictive_claim_allowed":False,
      },
      {
        "id":"ACCEPTANCE-PROBABILITY-001","severity":"CRITICAL" if not acceptance_ready else "INFO",
        "status":"NO_ACCEPT_REJECT_DENOMINATOR" if not acceptance_ready else "CALIBRATION_TARGET_AVAILABLE",
        "observation":"Completed transactions and reconstructed choices cannot identify the probability a proposed trade is accepted without rejected/expired offers or another defensible opportunity denominator. HIGH/MEDIUM/LOW/VERY_LOW remain heuristic plausibility bands, not probabilities.",
        "authoritative_probability_claim_allowed":acceptance_ready,
      },
      {
        "id":"BEHAVIOR-ACCEPTANCE-OVERLAP-001","severity":"HIGH",
        "status":"SIGNAL_REUSE_DETECTED" if final_signal_reuse else "RESOLVED_BY_ZERO_INCREMENTAL_BEHAVIOR_WEIGHT",
        "observation":"The prior negotiation rank reused owner behavior after it had already adjusted acceptance fit. The canonical composer now retains behavior as a diagnostic but assigns it zero incremental ranking weight; the distinct strategic/acceptance ratio is preserved by renormalization.",
        "authoritative_incremental_adjustment_claim_allowed":False,
      },
    ]
    payload={
      "model_version":MODEL_VERSION,
      "production_behavior_changed":True,
      "policy":{
        "revealed_preference_is_not_acceptance_probability":True,
        "leave_one_manager_out_is_leakage_control_not_predictive_validation":True,
        "acceptance_probability_requires_offer_or_choice_denominator":True,
        "heuristic_acceptance_bands_must_not_be_reported_as_calibrated_probabilities":True,
        "behavioral_signal_reuse_requires_final_score_ablation":True,
        "promotion_requires_time_ordered_holdout_improvement":True,\n        "sparse_manager_effects_use_adaptive_shrinkage":True,\n        "categorical_buyer_state_floors_authoritative":False,
      },
      "summary":{
        "behavior_research_markers_detected":behavior_markers,
        "behavior_bounded_secondary_markers_detected":behavior_bounded,\n        "continuous_buyer_utility_gate_detected":continuous_buyer_gate,\n        "legacy_buyer_state_floors_detected":legacy_buyer_state_floors,
        "heuristic_acceptance_band_markers_detected":acceptance_band_markers,
        "acceptance_evidence_ready_for_probability_fit":acceptance_ready,
        "behavior_signal_reused_in_negotiation_ranking":final_signal_reuse,
        "registry_consistent":registry_ok,
      },
      "findings":findings,
    }
    (OUT/"behavioral_acceptance_governance_audit.json").write_text(json.dumps(payload,indent=2),encoding="utf-8")
    print(json.dumps(payload["summary"],indent=2))
    if not behavior_markers or not behavior_bounded or not acceptance_band_markers:
        raise SystemExit("Behavioral/acceptance implementation markers drifted")
    if not registry_ok: raise SystemExit("Behavior/acceptance registry classification drifted")

if __name__=="__main__": main()
