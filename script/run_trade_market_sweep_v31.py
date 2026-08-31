#!/usr/bin/env python3
"""FSFFL Counter & Market Sweep 1.25 - evidence-consistent option governance.

Current production composition:
- v1.14 supplies the retained continuous state-aware GM reprofiling/ranking engine;
- trade_bilateral_composition installs the validated shared v1.15-equivalent
  buyer current-state hard gate and negotiation-family identity;
- trade_multi_asset_composition installs the validated shared v1.16-equivalent
  expanded multi-asset package generator;
- trade_state_selector_composition installs the v1.17-equivalent shared state
  policy and candidate selector onto that retained engine;
- Trade Decision's internal historical-behavior policy interprets shared
  historical-state intelligence for trade feasibility;
- Trade Decision's internal BI integration interprets production BI3-over-BI2
  intelligence for trade feasibility;
- trade_candidate_pools organizes same-partner counters and market alternatives
  with the validated v1.21 eligibility/dedup semantics;
- roster_resolution_governance verifies and publishes the roster-aware runtime
  resolver provenance already emitted by the simulation path;
- roster_interaction_overlay applies the validated roster-specific interaction
  mechanics and refreshes negotiation ranking;
- trade_option_governance owns final BETTER/MIXED/WORSE comparison and action
  authority.

Historical v1.15-v1.24 wrappers remain available for reproducibility but are no
longer executed by the current production path. Bilateral market gating,
negotiation-family identity, multi-asset package generation, dynamic state
policy and candidate-selection mechanics are separated from the historical
wrappers. Trade-specific BI/history interpretation remains owned by Trade
Decision rather than being promoted into Shared Core. Superseded wrapper
presentation/comparison logic cannot regain decision authority.

No player-specific exceptions are permitted.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent
V20 = SCRIPT / "run_trade_market_sweep_v20.py"
TRADE_NEGOTIATION_FAMILY = SCRIPT / "trade_negotiation_family.py"
TRADE_BILATERAL_GATE = SCRIPT / "trade_bilateral_gate.py"
TRADE_BILATERAL_COMPOSITION = SCRIPT / "trade_bilateral_composition.py"
TRADE_MULTI_ASSET_PACKAGES = SCRIPT / "trade_multi_asset_packages.py"
TRADE_MULTI_ASSET_COMPOSITION = SCRIPT / "trade_multi_asset_composition.py"
TRADE_STATE_POLICY = SCRIPT / "trade_state_policy.py"
TRADE_CANDIDATE_SELECTOR = SCRIPT / "trade_candidate_selector.py"
TRADE_STATE_SELECTOR_COMPOSITION = SCRIPT / "trade_state_selector_composition.py"
HISTORICAL_STATE = SCRIPT / "historical_state_behavior.py"
TRADE_HISTORICAL_BEHAVIOR = SCRIPT / "trade_decision" / "historical_behavior_policy.py"
BI2 = SCRIPT / "behavioral_intelligence.py"
TRADE_BEHAVIOR = SCRIPT / "trade_decision" / "behavior_integration.py"
TRADE_CANDIDATE_POOLS = SCRIPT / "trade_candidate_pools.py"
ROSTER_RESOLUTION_GOVERNANCE = SCRIPT / "roster_resolution_governance.py"
ROSTER_OVERLAY = SCRIPT / "roster_interaction_overlay.py"
ROSTER_INTERACTION = SCRIPT / "roster_interaction.py"
NEGOTIATION_RANKING = SCRIPT / "negotiation_ranking.py"
OPTION_GOVERNANCE = SCRIPT / "trade_option_governance.py"\nREPORT_CONTEXT = SCRIPT / "trade_report_context.py"
MODEL_VERSION = "FSFFL-Counter-Market-Sweep-1.26"


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def out_path():
    if "--output" not in sys.argv:
        return None
    i = sys.argv.index("--output")
    return Path(sys.argv[i + 1]) if i + 1 < len(sys.argv) else None


def main():
    v20 = load(V20, "market_v20_for_125")
    negotiation_family = load(
        TRADE_NEGOTIATION_FAMILY, "trade_negotiation_family_for_125"
    )
    bilateral_gate = load(TRADE_BILATERAL_GATE, "trade_bilateral_gate_for_125")
    bilateral_composition = load(
        TRADE_BILATERAL_COMPOSITION, "trade_bilateral_composition_for_125"
    )
    multi_asset_packages = load(
        TRADE_MULTI_ASSET_PACKAGES, "trade_multi_asset_packages_for_125"
    )
    multi_asset_composition = load(
        TRADE_MULTI_ASSET_COMPOSITION, "trade_multi_asset_composition_for_125"
    )
    state_policy = load(TRADE_STATE_POLICY, "trade_state_policy_for_125")
    candidate_selector = load(TRADE_CANDIDATE_SELECTOR, "trade_candidate_selector_for_125")
    state_selector_composition = load(
        TRADE_STATE_SELECTOR_COMPOSITION, "trade_state_selector_composition_for_125"
    )
    historical_state = load(HISTORICAL_STATE, "historical_state_behavior_for_125")
    historical_behavior = load(TRADE_HISTORICAL_BEHAVIOR, "trade_historical_behavior_for_125")
    bi2 = load(BI2, "behavioral_intelligence_for_125")
    trade_behavior = load(TRADE_BEHAVIOR, "trade_behavioral_intelligence_for_125")
    candidate_pools = load(TRADE_CANDIDATE_POOLS, "trade_candidate_pools_for_125")
    roster_resolution = load(ROSTER_RESOLUTION_GOVERNANCE, "roster_resolution_governance_for_125")
    overlay = load(ROSTER_OVERLAY, "roster_interaction_overlay_for_125")
    interaction = load(ROSTER_INTERACTION, "roster_interaction_for_125")
    ranker = load(NEGOTIATION_RANKING, "negotiation_ranking_for_125")
    gov = load(OPTION_GOVERNANCE, "trade_option_governance_for_126")
    report_context = load(REPORT_CONTEXT, "trade_report_context_for_126")

    state_selector_composition.install(
        v20, state_policy, candidate_selector, ranker, negotiation_family
    )
    multi_asset_composition.install(v20, multi_asset_packages)
    bilateral_composition.install(v20, bilateral_gate)
    bi3_cache, bi3_cache_status = trade_behavior.load_bi3_cache()
    trade_behavior.install(historical_behavior, bi2, bi3_cache, bi3_cache_status)
    historical_index = historical_behavior.install_historical_state_conditioning(
        state_policy, historical_state
    )
    v20.MODEL_VERSION = "FSFFL-Counter-Market-Sweep-1.20"
    v20.main()
    out = out_path()
    if not out or not out.exists():
        return

    report = json.loads(out.read_text(encoding="utf-8"))
    inherited_action = report.get("recommended_next_action")
    state_selector_composition.apply_report_metadata(
        report, inherited_action, state_policy
    )
    multi_asset_composition.apply_report_metadata(report, multi_asset_packages)
    bilateral_composition.apply_report_metadata(
        report, bilateral_gate, negotiation_family
    )
    historical_behavior.apply_report_metadata(report, historical_index)
    trade_behavior.apply_report_metadata(report, bi2, bi3_cache, bi3_cache_status)
    candidate_pools.apply_to_report(report)
    roster_resolution.apply_to_report(report)
    overlay.apply_to_report(report, interaction, ranker)
    action_basis = gov.apply_to_report(report)
    scenario = {}
    if "--scenario" in sys.argv:
        i = sys.argv.index("--scenario")
        if i + 1 < len(sys.argv):
            try:
                scenario = json.loads(Path(sys.argv[i + 1]).read_text(encoding="utf-8"))
            except Exception:
                scenario = {}
    report_context.apply_to_report(report, scenario)

    report.setdefault("governance", {})["option_outcome_consistency"] = {
        "categorical_score_threshold_removed": True,
        "post_sim_score_is_diagnostic_not_categorical_decision_rule": True,
        "better_worse_uses_pareto_decision_outputs": True,
        "decision_outputs": list(gov.DECISION_OUTPUTS),
        "acceptance_fit_affects_trade_valuation": False,
        "acceptance_fit_reported_as_separate_behavioral_intelligence": True,
        "acceptance_fit_hard_gate_on_trade_quality": False,
        "descriptive_state_labels_create_action_cliffs": False,
        "current_offer_action_recomputed_after_final_option_comparisons": True,
        "current_offer_counterparty_utility_can_veto_acceptance": False,
        "generated_option_counterparty_utility_remains_feasibility_signal": True,
        "action_basis": action_basis,
        "player_specific_exceptions": False,
        "shared_option_governance_model_version": gov.MODEL_VERSION,
    }
    report["model_version"] = MODEL_VERSION
    report.setdefault("policy", {}).update({
        "option_comparison_model_version": gov.MODEL_VERSION,
        "every_recommended_option_compared_to_current_offer": True,
        "option_comparison_includes_explicit_verdict": True,
        "option_comparison_includes_reason": True,
        "option_comparison_uses_state_aware_post_sim_score": False,
        "option_comparison_uses_pareto_decision_outputs": True,
        "unsupported_numeric_score_cutoff_used_for_better_worse": False,
        "state_aware_score_is_search_and_diagnostic_signal_not_categorical_better_proof": True,
        "better_requires_no_regression_across_decision_outputs": True,
        "worse_requires_no_improvement_across_decision_outputs": True,
        "conflicting_decision_outputs_are_mixed": True,
        "acceptance_likelihood_is_separate_from_trade_valuation": True,
        "behavioral_intelligence_informs_counterparty_feasibility_not_trade_value": True,
        "current_offer_is_evaluated_on_focal_utility_not_counterparty_willingness": True,
        "counterparty_utility_applies_to_generated_offer_feasibility_not_acceptance_of_offer_in_hand": True,
        "low_or_very_low_acceptance_changes_trade_quality_verdict": False,
        "descriptive_state_labels_create_action_cliffs": False,
        "mixed_tradeoffs_remain_visible": True,
        "candidate_generation_unchanged": False,\n        "candidate_generation_change": "offeror_origin_aware_target_preserving_concession_search",
        "simulation_unchanged": True,
        "trade_decision_option_governance_internal_component": True,
        "trade_decision_bilateral_gate_internal_component": True,
        "trade_decision_negotiation_family_internal_component": True,
        "historical_v21_executed_in_current_path": False,
        "trade_decision_multi_asset_package_generator_internal_component": True,
        "historical_v22_executed_in_current_path": False,
        "trade_decision_state_policy_internal_component": True,
        "trade_decision_candidate_selector_internal_component": True,
        "historical_v23_executed_in_current_path": False,
        "trade_decision_historical_behavior_internal_component": True,
        "historical_state_intelligence_shared_source_consumed": True,
        "historical_behavior_interpretation_owned_by_trade_decision": True,
        "historical_v24_executed_in_current_path": False,
        "trade_decision_behavior_integration_internal_component": True,
        "behavioral_intelligence_shared_source_consumed": True,
        "trade_behavior_interpretation_owned_by_trade_decision": True,
        "historical_v26_executed_in_current_path": False,
        "trade_decision_candidate_pools_internal_component": True,
        "historical_v27_executed_in_current_path": False,
        "trade_decision_roster_interaction_overlay_internal_component": True,
        "trade_decision_roster_resolution_governance_internal_component": True,
        "historical_v29_executed_in_current_path": False,
        "historical_v28_executed_in_current_path": False,
        "historical_v30_executed_in_current_path": False,\n        "trade_report_context_internal_component": True,
    })
    report.setdefault("simulation", {})["execution_path"] = (
        str((report.get("simulation") or {}).get("execution_path") or "")
        + "_plus_outcome_consistent_option_governance"
    )
    out.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")


if __name__ == "__main__":
    main()
