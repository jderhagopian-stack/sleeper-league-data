#!/usr/bin/env python3
"""FSFFL Counter & Market Sweep 1.25 - evidence-consistent option governance.

Current production composition:
- v1.21 supplies the retained candidate frontier and simulation chain;
- roster_resolution_governance verifies and publishes the roster-aware runtime
  resolver provenance already emitted by the simulation path;
- roster_interaction_overlay applies the validated roster-specific interaction
  mechanics and refreshes negotiation ranking;
- trade_option_governance owns final BETTER/MIXED/WORSE comparison and action
  authority.

Historical v1.22-v1.24 wrappers remain available for reproducibility but are no
longer executed by the current production path. Their superseded comparison and
presentation-only layers therefore cannot regain decision authority.

No player-specific exceptions are permitted.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent
V27 = SCRIPT / "run_trade_market_sweep_v27.py"
ROSTER_RESOLUTION_GOVERNANCE = SCRIPT / "roster_resolution_governance.py"
ROSTER_OVERLAY = SCRIPT / "roster_interaction_overlay.py"
ROSTER_INTERACTION = SCRIPT / "roster_interaction.py"
NEGOTIATION_RANKING = SCRIPT / "negotiation_ranking.py"
OPTION_GOVERNANCE = SCRIPT / "trade_option_governance.py"
MODEL_VERSION = "FSFFL-Counter-Market-Sweep-1.25"


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
    v27 = load(V27, "market_v27_for_125")
    roster_resolution = load(ROSTER_RESOLUTION_GOVERNANCE, "roster_resolution_governance_for_125")
    overlay = load(ROSTER_OVERLAY, "roster_interaction_overlay_for_125")
    interaction = load(ROSTER_INTERACTION, "roster_interaction_for_125")
    ranker = load(NEGOTIATION_RANKING, "negotiation_ranking_for_125")
    gov = load(OPTION_GOVERNANCE, "trade_option_governance_for_125")

    v27.main()
    out = out_path()
    if not out or not out.exists():
        return

    report = json.loads(out.read_text(encoding="utf-8"))
    roster_resolution.apply_to_report(report)
    overlay.apply_to_report(report, interaction, ranker)
    action_basis = gov.apply_to_report(report)

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
        "low_or_very_low_acceptance_changes_trade_quality_verdict": False,
        "descriptive_state_labels_create_action_cliffs": False,
        "mixed_tradeoffs_remain_visible": True,
        "candidate_generation_unchanged": True,
        "simulation_unchanged": True,
        "canonical_option_governance_shared_component": True,
        "canonical_roster_interaction_overlay_shared_component": True,
        "canonical_roster_resolution_governance_shared_component": True,
        "historical_v29_executed_in_current_path": False,
        "historical_v28_executed_in_current_path": False,
        "historical_v30_executed_in_current_path": False,
    })
    report.setdefault("simulation", {})["execution_path"] = (
        str((report.get("simulation") or {}).get("execution_path") or "")
        + "_plus_outcome_consistent_option_governance"
    )
    out.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")


if __name__ == "__main__":
    main()
