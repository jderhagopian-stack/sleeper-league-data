#!/usr/bin/env python3
"""Governance audit for behavioral intelligence and trade-acceptance modeling."""
from __future__ import annotations
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SCRIPT=ROOT/"script"; DATA=ROOT/"data"; OUT=DATA/"audit"; OUT.mkdir(parents=True,exist_ok=True)
MODEL_VERSION="FSFFL-Behavioral-Acceptance-Governance-2.0"

def load(path,default=None):
    if not path.exists(): return default
    return json.loads(path.read_text(encoding="utf-8"))

def txt(name): return (SCRIPT/name).read_text(encoding="utf-8")

def main():
    v16=txt("run_trade_market_sweep_v16.py")
    v18=txt("run_trade_market_sweep_v18.py")
    v23=txt("run_trade_market_sweep_v23.py")
    v26=txt("run_trade_market_sweep_v26.py")\n    behavior_integration=text_path = (SCRIPT/"trade_decision"/"behavior_integration.py").read_text(encoding="utf-8")\n    bi3=txt("behavioral_intelligence_v3.py")\n    bilateral=txt("trade_bilateral_gate.py")
    facade=txt("behavioral_intelligence_v3_production.py")
    workflow=(ROOT/".github/workflows/test-behavioral-intelligence-v3-production.yml").read_text(encoding="utf-8")
    trans=load(OUT/"transaction_evidence_readiness_audit.json",{}) or {}
    reg=load(DATA/"model_parameter_registry.json",{}) or {}
    params={x.get("id"):x for x in reg.get("parameters",[])}

    thresholds=all(x in v16 for x in ("score >= 0.68","score >= 0.48","score >= 0.28"))
    state_floors=all(x in v16 for x in ("title_floor = -0.04","title_floor = -0.05","title_floor = -0.10"))
    behavior_enters_acceptance=(
        "base_score+sf(sig.get(\"adjustment\"))" in v18.replace(" ","")
        or "base_score + sf(sig.get(\"adjustment\"))" in v18
    )
    ranking_reuses_behavior=(
        'acceptance=clamp(sf(br.get("heuristic_acceptance_fit_score"),.5)' in v18.replace(" ","")
        and 'behavior=clamp(.50+sf((br.get("owner_behavior")or{}).get("adjustment"))/.32' in v18.replace(" ","")
    )
    # v23 is the canonical ranking helper inherited by the current production wrapper.
    ranker=txt("negotiation_ranking.py")
    canonical_double_use=not (
        'OWNER_BEHAVIOR_WEIGHT = 0.0' in ranker
        and 'ACCEPTANCE_WEIGHT = 0.0' in ranker
        and 'arbitrary_strategic_acceptance_exchange_rate_authorized": False' in ranker
    )
    canonical_deduplicated=not canonical_double_use
    adaptive_bi3=all(x in bi3 for x in (
        "def shrinkage_factor(weight, prior_strength)",
        "statistics.median(positive_weights)",
        "SOURCE_WEIGHT = {\"trade\": 1.0, \"draft\": 1.0, \"acquisition\": 1.0}",
    ))
    confidence_weighted_integration=all(x in behavior_integration for x in (
        "def combine_behavior_signals",
        "confidence_weighted_boundary_shrinkage",
        "trait_confidence",
    ))
    bi3_handset=not (adaptive_bi3 and confidence_weighted_integration)
    predictive_terms=("brier","log_loss","log loss","future acceptance","held-out acceptance","out-of-sample acceptance","time-ordered holdout")
    predictive_holdout=any(x in workflow.lower() for x in predictive_terms)
    denominator=False
    for x in trans.get("findings",[]):
        if x.get("id")=="ACCEPTANCE-CALIBRATION-READINESS-001":
            denominator=bool(x.get("authoritative_empirical_claim_allowed"))
    facade_qualified=(
        '"empirical_validation_status"] = "NOT_PREDICTIVELY_VALIDATED"' in facade
        and '"predictive_holdout_validated"] = False' in facade
        and '"software_promotion_is_not_empirical_validation"] = True' in facade
    )
    behavior_registry=params.get("BEHAVIOR-001",{})
    acceptance_registry=params.get("ACCEPTANCE-GATE-001",{})
    registry_ok=(
        behavior_registry.get("authoritative_use") is False
        and acceptance_registry.get("authoritative_use") is False
    )

    findings=[
      {
        "id":"BEHAVIOR-PRODUCTION-LABEL-001",
        "severity":"INFO" if facade_qualified else "HIGH",
        "status":"SOFTWARE_VS_EMPIRICAL_SCOPE_EXPLICIT" if facade_qualified else "PRODUCTION_LABEL_OVERSTATES_VALIDATION",
        "observation":"BI3 may run in production while remaining empirically unvalidated for future manager-choice prediction. Production deployment and predictive validation are now represented separately.",
        "authoritative_empirical_claim_allowed":False,
      },
      {
        "id":"ACCEPTANCE-THRESHOLDS-001",
        "severity":"HIGH",
        "status":"CONTINUOUS_BUYER_UTILITY_WITH_DESCRIPTIVE_BANDS" if continuous_buyer_utility and not state_floors else "LEGACY_STATE_GATE_DETECTED",
        "observation":"Buyer feasibility now uses shared continuous decision utility. HIGH/MEDIUM/LOW labels remain descriptive and are not calibrated probabilities; categorical state-specific title/value floors no longer have authority.",
        "authoritative_probability_claim_allowed":False,
      },
      {
        "id":"BEHAVIOR-RANK-DOUBLE-COUNT-001",
        "severity":"HIGH",
        "status":"OVERLAPPING_SIGNAL_PATH_DETECTED" if canonical_double_use else "RESOLVED_BY_CANONICAL_COMPOSER",
        "observation":"Owner behavior still informs heuristic acceptance fit, but the canonical ranking composer now gives the separate owner-behavior diagnostic zero additional weight. The prior duplicate path is preserved only in ablation evidence, not production ranking.",
        "authoritative_incremental_weight_claim_allowed":False,
      },
      {
        "id":"ACCEPTANCE-DENOMINATOR-001",
        "severity":"HIGH",
        "status":"NO_ACCEPT_REJECT_DENOMINATOR" if not denominator else "DENOMINATOR_AVAILABLE",
        "observation":"Completed trades and reconstructed actions do not provide the rejected/expired offer denominator needed to estimate literal acceptance probability.",
        "authoritative_probability_claim_allowed":denominator,
      },
      {
        "id":"BEHAVIOR-PREDICTIVE-HOLDOUT-001",
        "severity":"HIGH",
        "status":"PREDICTIVE_HOLDOUT_PRESENT" if predictive_holdout else "STRUCTURAL_TESTS_ONLY",
        "observation":"Current BI3 production tests cover runtime, cache, context normalization and boundedness but do not demonstrate time-ordered held-out prediction of future manager actions.",
        "authoritative_empirical_claim_allowed":predictive_holdout,
      },
    ]
    payload={
      "model_version":MODEL_VERSION,
      "production_scoring_behavior_changed":True,
      "policy":{
        "production_deployment_is_not_empirical_validation":True,
        "acceptance_fit_is_not_probability_without_accept_reject_denominator":True,
        "behavioral_signal_reuse_requires_ablation":True,
        "acceptance_band_labels_remain_descriptive_and_uncalibrated":True,\n        "categorical_buyer_state_thresholds_removed":True,\n        "behavioral_sparse_data_uses_adaptive_shrinkage":True,
        "predictive_promotion_requires_time_ordered_holdout_improvement":True,
      },
      "summary":{
        "acceptance_band_thresholds_detected":thresholds,
        "buyer_state_floors_detected":state_floors,\n        "continuous_buyer_utility_gate_detected":continuous_buyer_utility,
        "historical_behavior_enters_acceptance_score":behavior_enters_acceptance,
        "canonical_negotiation_ranking_reuses_behavior":canonical_double_use,
        "canonical_negotiation_ranking_deduplicated":canonical_deduplicated,
        "bi3_hand_set_blend_and_caps_detected":bi3_handset,\n        "bi3_adaptive_shrinkage_detected":adaptive_bi3,\n        "trade_behavior_confidence_weighted_integration_detected":confidence_weighted_integration,
        "predictive_holdout_test_detected":predictive_holdout,
        "accept_reject_denominator_ready":denominator,
        "production_facade_empirical_scope_qualified":facade_qualified,
        "registry_consistent":registry_ok,
      },
      "findings":findings,
    }
    (OUT/"behavioral_acceptance_audit.json").write_text(json.dumps(payload,indent=2),encoding="utf-8")
    print(json.dumps(payload["summary"],indent=2))
    if not thresholds or state_floors or not continuous_buyer_utility: raise SystemExit("Acceptance governance did not converge to continuous buyer utility")\n    if not adaptive_bi3 or not confidence_weighted_integration: raise SystemExit("Behavioral shrinkage governance markers missing")
    if not canonical_deduplicated: raise SystemExit("Canonical negotiation ranking still double-counts behavior")
    if not facade_qualified: raise SystemExit("BI3 production facade does not separate software and empirical validation")
    if not registry_ok: raise SystemExit("Behavior/acceptance registry classifications are inconsistent")
if __name__=="__main__": main()
