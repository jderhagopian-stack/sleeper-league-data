#!/usr/bin/env python3
"""Governance audit for FSFFL multi-asset package economics."""
from __future__ import annotations
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/"data"; OUT=DATA/"audit"; OUT.mkdir(parents=True,exist_ok=True)
ENGINE=ROOT/"script"/"build_fsffl_gm_engine.py"
ROSTER_AWARE=ROOT/"script"/"roster_aware_trade.py"
STATE_AWARE=ROOT/"script"/"decision_lab_state_aware.py"
ROBUST=ROOT/"script"/"package_curve_robustness.py"
GM30_CF=ROOT/"script"/"run_fsffl_gm30_counterfactual.py"
GM30_RUNNER=ROOT/"script"/"run_fsffl_gm30_counterfactual_governed.py"
READINESS=OUT/"transaction_evidence_readiness_audit.json"
BASELINE=OUT/"package_curve_leverage_baseline.json"
REGISTRY=DATA/"model_parameter_registry.json"
MODEL_VERSION="FSFFL-Package-Economics-Governance-1.1"

def load(path,default=None):
    if not path.exists(): return default
    return json.loads(path.read_text(encoding="utf-8"))

def main():
    src=ENGINE.read_text(encoding="utf-8")
    roster_src=ROSTER_AWARE.read_text(encoding="utf-8")
    state_src=STATE_AWARE.read_text(encoding="utf-8")
    robust_src=ROBUST.read_text(encoding="utf-8")
    cf_src=GM30_CF.read_text(encoding="utf-8")
    runner_src=GM30_RUNNER.read_text(encoding="utf-8")
    ready=load(READINESS,{}) or {}
    baseline=load(BASELINE,{}) or {}
    registry=load(REGISTRY,{}) or {}
    params={x.get("id"):x for x in registry.get("parameters",[])}
    governed=params.get("PACKAGE-ECON-001",{})

    legacy=[1.0,.92,.84]
    strategic=[1.0,.78,.62,.50,.42]
    rows=[]
    for n in (1,2,3):
        l=sum(legacy[:n]); s=sum(strategic[:n])
        rows.append({"asset_count":n,"legacy_neutral_equal_asset_effective_value":round(l,4),"strategic_neutral_equal_asset_effective_value":round(s,4),"legacy_premium_vs_strategic_pct":round((l/s-1)*100,2) if s else None})

    legacy_runtime=('"package_effective_value_weights": [1.0, 0.92, 0.84]' in src and "seller_effective = effective_package_value(seller_values)" in src)
    strategic_runtime=('"package_weights": [1.0, 0.78, 0.62, 0.50, 0.42]' in src and 'weights = GM22["package_weights"]' in src)
    generic_slot_cost_removed=('"extra_asset_slot_cost_pct": 0.0' in src and '"roster_slot_cost_source": "exact_downstream_roster_legalization"' in src)
    exact_cut_runtime=("incremental_overflow = max(0, active_pre_cut - effective_limit)" in roster_src and '"selected_cuts": selected' in roster_src and 'elif typ in {"drop", "cut"}' in state_src and 'sent.extend(f"player:{x}"' in state_src)
    liquidity_secondary_adjustment='w = clamp(w + (liq - 0.5) * 0.08' in src
    dual_sources=legacy_runtime and strategic_runtime

    robust_discovery=('"production_steep": [1.0, 0.78, 0.62, 0.50, 0.42]' in robust_src and '"shallow": [1.0, 0.92, 0.84, 0.78, 0.72]' in robust_src and '"neutral": [1.0, 1.0, 1.0, 1.0, 1.0]' in robust_src and '"single_package_curve_authoritative": False' in robust_src and '"robust_ranking_basis": "presence_then_minimax_rank_then_rank_sum"' in robust_src)
    gm30_upstream_path=('original = gm30.core.build_universal_trade_opportunities' in cf_src and 'packages[:package_limit]' in cf_src and 'package_robustness.install(gm30.core)' in runner_src and 'counterfactual.install_counterfactual_trade_patch()' in runner_src)

    leverage=(baseline.get("summary") or {})
    exhaustive_leverage=(int(baseline.get("teams_audited") or 0)==12 and leverage.get("material_downstream_leverage_detected") is True and int(leverage.get("teams_with_top_target_flip_under_any_counterfactual") or 0)>=1 and int(leverage.get("teams_with_top_package_flip_under_any_counterfactual") or 0)>=1)

    package_ready=False
    for x in ready.get("findings",[]):
        if x.get("id")=="PACKAGE-CALIBRATION-READINESS-001": package_ready=bool(x.get("authoritative_empirical_claim_allowed"))
    registry_ok=(governed.get("evidence_tier")=="ASSUMPTION_SENSITIVE_PROVISIONAL" and governed.get("authoritative_use") is False)

    findings=[
      {"id":"PACKAGE-DUAL-CURVE-001","severity":"HIGH","status":"MULTIPLE_PROVISIONAL_CURVES_ROBUST_DISCOVERY_ACTIVE" if robust_discovery else "MULTIPLE_ACTIVE_PACKAGE_CURVES","observation":"Multiple package-shape assumptions remain available because no authoritative consolidation curve is calibrated. The governed GM3 path searches steep, shallow and neutral shapes and ranks candidates by cross-curve robustness before simulation.","authoritative_empirical_claim_allowed":False},
      {"id":"PACKAGE-SLOT-COST-001","severity":"HIGH","status":"GENERIC_SLOT_PENALTY_REMOVED_EXACT_CUT_ACTIVE","observation":"The canonical trade path computes actual roster overflow, forces the required incumbent cut, and carries that cut into post-trade strategic evaluation. The prior generic percentage slot charge remains zero.","authoritative_empirical_claim_allowed":True},
      {"id":"PACKAGE-PRESCREEN-PATH-001","severity":"HIGH","status":"HIGH_LEVERAGE_MEASURED_ROBUST_RECALL_GUARD_ACTIVE" if exhaustive_leverage and robust_discovery and gm30_upstream_path else "PATH_DEPENDENCE_RECALL_TEST_REQUIRED","observation":"The exhaustive 12-team sensitivity showed the curve materially changes candidate recall. Because GM3 simulates only a bounded subset, the governed path uses multi-curve robust discovery rather than declaring one unsupported curve correct.","authoritative_empirical_claim_allowed":False},
      {"id":"PACKAGE-CALIBRATION-001","severity":"HIGH","status":"NOT_READY_FOR_RESIDUAL_FIT" if not package_ready else "RESIDUAL_FIT_EVIDENCE_AVAILABLE","observation":"Completed trade geometry is insufficient to estimate consolidation discounts without contemporaneous side-by-side value snapshots. Robust search fixes path dependence but is not coefficient calibration.","authoritative_empirical_claim_allowed":package_ready},
    ]

    payload={"model_version":MODEL_VERSION,"production_behavior_changed":False,"production_state_changed_by_governed_fix":True,"policy":{"prescreen_package_curve_is_search_heuristic_not_final_economics":True,"generic_roster_slot_penalty_must_not_duplicate_exact_cut_cost":True,"multiple_package_curves_require_explicit_provenance":True,"single_package_curve_must_not_control_gm3_candidate_recall":True,"curve_disagreement_is_model_uncertainty_not_evidence_for_a_new_coefficient":True,"robust_candidate_discovery_precedes_counterfactual_simulation":True,"current_market_value_backfill_for_package_fit_forbidden":True,"promotion_requires_temporal_or_other_out_of_sample_residual_improvement":True},"summary":{"legacy_prescreen_curve_detected":legacy_runtime,"strategic_curve_detected":strategic_runtime,"strategic_liquidity_adjustment_detected":liquidity_secondary_adjustment,"generic_slot_cost_removed":generic_slot_cost_removed,"exact_downstream_cut_cost_active":exact_cut_runtime,"multiple_active_package_curves":dual_sources,"robust_multi_curve_discovery_detected":robust_discovery,"gm30_bounded_candidate_simulation_path_detected":gm30_upstream_path,"exhaustive_12_team_curve_leverage_detected":exhaustive_leverage,"teams_with_top_target_flip":leverage.get("teams_with_top_target_flip_under_any_counterfactual"),"teams_with_top_package_flip":leverage.get("teams_with_top_package_flip_under_any_counterfactual"),"minimum_top_target_overlap_fraction":leverage.get("minimum_top_target_overlap_fraction"),"minimum_top_package_overlap_fraction":leverage.get("minimum_top_package_overlap_fraction"),"transaction_evidence_ready_for_authoritative_package_fit":package_ready,"registry_consistent":registry_ok,"equal_asset_curve_comparison":rows},"findings":findings}
    (OUT/"package_economics_audit.json").write_text(json.dumps(payload,indent=2),encoding="utf-8")
    print(json.dumps(payload["summary"],indent=2))
    if not dual_sources: raise SystemExit("Expected package-economics paths changed or were not detected")
    if not generic_slot_cost_removed or not exact_cut_runtime: raise SystemExit("Package roster-burden de-duplication is incomplete")
    if not robust_discovery or not gm30_upstream_path or not exhaustive_leverage: raise SystemExit("High-leverage package-curve recall governance is incomplete")
    if not registry_ok: raise SystemExit("PACKAGE-ECON-001 registry classification is inconsistent")
if __name__=="__main__": main()
