#!/usr/bin/env python3
"""Governance audit for behavioral intelligence and trade-acceptance modeling."""
from __future__ import annotations
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SCRIPT=ROOT/"script"; DATA=ROOT/"data"; OUT=DATA/"audit"; OUT.mkdir(parents=True,exist_ok=True)
MODEL_VERSION="FSFFL-Behavioral-Acceptance-Governance-1.0"

def load(path,default=None):
    if not path.exists(): return default
    return json.loads(path.read_text(encoding="utf-8"))

def txt(name): return (SCRIPT/name).read_text(encoding="utf-8")

def main():
    v16=txt("run_trade_market_sweep_v16.py")
    v18=txt("run_trade_market_sweep_v18.py")
    v23=txt("run_trade_market_sweep_v23.py")
    v26=txt("run_trade_market_sweep_v26.py")
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
    canonical_double_use=(
        'acceptance = clamp(sf(br.get("heuristic_acceptance_fit_score"), .5)' in v23
        and 'behavior = clamp(.50 + sf((br.get("owner_behavior") or {}).get("adjustment")) / .32' in v23
    )
    bi3_handset=all(x in v26 for x in (".45 * sf(t3.get(\"confidence\"", "adj += .035", "adj += .030", "adj += .020", "adj += .025", "clamp(adj, -.075, .075)"))
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
        "status":"HAND_SET_DECISION_GATES",
        "observation":"Acceptance bands and buyer-state title/value floors are hand-set thresholds with real candidate-selection and action leverage. They are useful bounded heuristics, not calibrated probabilities.",
        "authoritative_probability_claim_allowed":False,
      },
      {
        "id":"BEHAVIOR-RANK-DOUBLE-COUNT-001",
        "severity":"HIGH",
        "status":"OVERLAPPING_SIGNAL_PATH_DETECTED" if canonical_double_use else "NOT_DETECTED",
        "observation":"The canonical negotiation rank uses heuristic acceptance fit after historical owner behavior has already adjusted that fit, then adds owner-behavior match again as a separate ranking component. Incremental ranking value of the second behavior channel requires ablation; otherwise the same evidence is counted twice.",
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
      "production_scoring_behavior_changed":False,
      "policy":{
        "production_deployment_is_not_empirical_validation":True,
        "acceptance_fit_is_not_probability_without_accept_reject_denominator":True,
        "behavioral_signal_reuse_requires_ablation":True,
        "hand_set_acceptance_thresholds_remain_provisional":True,
        "predictive_promotion_requires_time_ordered_holdout_improvement":True,
      },
      "summary":{
        "acceptance_band_thresholds_detected":thresholds,
        "buyer_state_floors_detected":state_floors,
        "historical_behavior_enters_acceptance_score":behavior_enters_acceptance,
        "canonical_negotiation_ranking_reuses_behavior":canonical_double_use,
        "bi3_hand_set_blend_and_caps_detected":bi3_handset,
        "predictive_holdout_test_detected":predictive_holdout,
        "accept_reject_denominator_ready":denominator,
        "production_facade_empirical_scope_qualified":facade_qualified,
        "registry_consistent":registry_ok,
      },
      "findings":findings,
    }
    (OUT/"behavioral_acceptance_audit.json").write_text(json.dumps(payload,indent=2),encoding="utf-8")
    print(json.dumps(payload["summary"],indent=2))
    if not thresholds or not state_floors: raise SystemExit("Acceptance runtime markers changed")
    if not canonical_double_use: raise SystemExit("Expected behavior/acceptance overlap path was not detected")
    if not facade_qualified: raise SystemExit("BI3 production facade does not separate software and empirical validation")
    if not registry_ok: raise SystemExit("Behavior/acceptance registry classifications are inconsistent")
if __name__=="__main__": main()
