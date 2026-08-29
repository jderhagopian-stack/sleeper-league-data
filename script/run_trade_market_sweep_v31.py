#!/usr/bin/env python3
"""FSFFL Counter & Market Sweep 1.25 - evidence-consistent option governance.

Extends validated 1.24 without changing candidate generation or simulation.

Final option comparison/action authority is delegated to the version-neutral
trade_option_governance component so future FSFFL applications can reuse the
same validated BETTER/MIXED/WORSE and acceptance-separation semantics.

No player-specific exceptions are permitted.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent
V30 = SCRIPT / "run_trade_market_sweep_v30.py"
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
    v30 = load(V30, "market_v30_for_125")
    gov = load(OPTION_GOVERNANCE, "trade_option_governance_for_125")
    v30.main()
    out = out_path()
    if not out or not out.exists():
        return

    report = json.loads(out.read_text(encoding="utf-8"))
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
    })
    report.setdefault("simulation", {})["execution_path"] = (
        str((report.get("simulation") or {}).get("execution_path") or "")
        + "_plus_outcome_consistent_option_governance"
    )
    out.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")


if __name__ == "__main__":
    main()
