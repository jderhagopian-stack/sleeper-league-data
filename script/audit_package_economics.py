#!/usr/bin/env python3
"""Governance audit for FSFFL multi-asset package economics."""
from __future__ import annotations
import json,re
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/"data"; OUT=DATA/"audit"; OUT.mkdir(parents=True,exist_ok=True)
ENGINE=ROOT/"script"/"build_fsffl_gm_engine.py"
READINESS=OUT/"transaction_evidence_readiness_audit.json"
REGISTRY=DATA/"model_parameter_registry.json"
MODEL_VERSION="FSFFL-Package-Economics-Governance-1.0"

def load(path,default=None):
    if not path.exists(): return default
    return json.loads(path.read_text(encoding="utf-8"))

def main():
    src=ENGINE.read_text(encoding="utf-8")
    ready=load(READINESS,{}) or {}
    registry=load(REGISTRY,{}) or {}
    params={x.get("id"):x for x in registry.get("parameters",[])}
    governed=params.get("PACKAGE-ECON-001",{})
    legacy=[1.0,.92,.84]
    strategic=[1.0,.78,.62,.50,.42]
    slot=.035
    rows=[]
    for n in (1,2,3):
        l=sum(legacy[:n])
        s=sum(strategic[:n])-slot*max(0,n-1)
        rows.append({
          "asset_count":n,
          "legacy_neutral_equal_asset_effective_value":round(l,4),
          "strategic_neutral_equal_asset_effective_value_after_slot_cost":round(s,4),
          "legacy_premium_vs_strategic_pct":round((l/s-1)*100,2) if s else None,
        })
    legacy_runtime=(
      '"package_effective_value_weights": [1.0, 0.92, 0.84]' in src
      and "seller_effective = effective_package_value(seller_values)" in src
    )
    strategic_runtime=(
      '"package_weights": [1.0, 0.78, 0.62, 0.50, 0.42]' in src
      and 'weights = GM22["package_weights"]' in src
      and 'GM22["extra_asset_slot_cost_pct"]' in src
    )
    liquidity_secondary_adjustment='w = clamp(w + (liq - 0.5) * 0.08' in src
    dual_sources=legacy_runtime and strategic_runtime
    package_ready=False
    for x in ready.get("findings",[]):
        if x.get("id")=="PACKAGE-CALIBRATION-READINESS-001":
            package_ready=bool(x.get("authoritative_empirical_claim_allowed"))
    registry_ok=(
      governed.get("evidence_tier")=="ASSUMPTION_SENSITIVE_PROVISIONAL"
      and governed.get("authoritative_use") is False
    )
    findings=[
      {
        "id":"PACKAGE-DUAL-CURVE-001",
        "severity":"HIGH",
        "status":"MULTIPLE_ACTIVE_PACKAGE_CURVES",
        "observation":"Candidate discovery uses a shallower legacy package-discount curve, while strategic valuation uses the steeper GM2.2 curve plus roster-slot cost and a liquidity adjustment. This is a deliberate-looking but unvalidated multiple-source-of-truth condition until prescreen recall and final ranking are tested together.",
        "authoritative_empirical_claim_allowed":False,
      },
      {
        "id":"PACKAGE-PRESCREEN-PATH-001",
        "severity":"HIGH",
        "status":"PATH_DEPENDENCE_RECALL_TEST_REQUIRED",
        "observation":"A package can be screened under one economic curve and ranked under another. The discovery curve should be treated only as a high-recall search heuristic unless exhaustive tractable-universe testing shows it does not hide strategically viable candidates.",
        "authoritative_empirical_claim_allowed":False,
      },
      {
        "id":"PACKAGE-CALIBRATION-001",
        "severity":"HIGH",
        "status":"NOT_READY_FOR_RESIDUAL_FIT" if not package_ready else "RESIDUAL_FIT_EVIDENCE_AVAILABLE",
        "observation":"Completed FSFFL trade geometry is not sufficient to estimate consolidation discounts without contemporaneous side-by-side value snapshots. Current market values may not be backfilled into old trades.",
        "authoritative_empirical_claim_allowed":package_ready,
      },
    ]
    payload={
      "model_version":MODEL_VERSION,
      "production_behavior_changed":False,
      "policy":{
        "prescreen_package_curve_is_search_heuristic_not_final_economics":True,
        "multiple_package_curves_require_explicit_provenance":True,
        "prescreen_recall_test_required_before_curve_unification_or_tuning":True,
        "current_market_value_backfill_for_package_fit_forbidden":True,
        "promotion_requires_temporal_or_other_out_of_sample_residual_improvement":True,
      },
      "summary":{
        "legacy_prescreen_curve_detected":legacy_runtime,
        "strategic_curve_detected":strategic_runtime,
        "strategic_liquidity_adjustment_detected":liquidity_secondary_adjustment,
        "multiple_active_package_curves":dual_sources,
        "transaction_evidence_ready_for_authoritative_package_fit":package_ready,
        "registry_consistent":registry_ok,
        "equal_asset_curve_comparison":rows,
      },
      "findings":findings,
    }
    (OUT/"package_economics_audit.json").write_text(json.dumps(payload,indent=2),encoding="utf-8")
    print(json.dumps(payload["summary"],indent=2))
    if not dual_sources: raise SystemExit("Expected package-economics paths changed or were not detected")
    if not registry_ok: raise SystemExit("PACKAGE-ECON-001 registry classification is inconsistent")
if __name__=="__main__": main()
