#!/usr/bin/env python3
"""Govern the first post-audit heuristic refinement pass."""
from __future__ import annotations
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"data"/"audit"/"heuristic_refinement_first_pass.json"

def read(rel):
    return (ROOT/rel).read_text(encoding="utf-8")

def main():
    v13=read("script/run_trade_market_sweep_v13.py")
    roster=read("script/roster_aware_trade.py")
    registry=json.loads(read("data/model_parameter_registry.json"))
    backlog=json.loads(read("data/model_governance/heuristic_refinement_backlog.json"))
    provisional_policy=json.loads(read("data/model_governance/provisional_estimation_policy.json"))
    interaction=read("script/roster_interaction.py")
    params={x["id"]:x for x in registry.get("parameters") or []}

    findings={
      "full_legal_cut_pool_exposed":"cut_candidate_pool" in roster and "cut_candidate_pool_size" in roster,
      "retention_cost_marked_prescreen":"retention_cost_prescreen_pending_final_plan_optimization" in roster,
      "final_cut_search_only_on_high_precision_candidates":"sims < 50000" in v13,
      "tractable_plan_space_bounded":"FINAL_CUT_PLAN_MAX_COMBINATIONS = 27" in v13,
      "cut_plan_screen_uses_common_downstream_trade_score":"canonical_downstream_trade_score" in v13 and "engine.post_sim_score" in v13,
      "selected_cut_plan_reruns_through_final_candidate_path":"_optimize_final_focus_cut_plan" in v13 and "_simulate_resolved_candidate" in v13,
      "retention_formula_not_claimed_final_authority":"retention_cost_is_final_authority" in v13,
      "registry_updated":params.get("ROSTER-CUT-001",{}).get("status")=="FINAL_FOCAL_TRACTABLE_PLAN_SEARCH_ACTIVE_PRESCREEN_FALLBACK",
      "refinement_backlog_separates_existing_vs_new_evidence":bool(backlog.get("improve_now_with_existing_evidence")) and bool(backlog.get("needs_genuinely_new_or_better_evidence")),
      "trade_score_uses_stronger_basis_after_first_pass":any(
          x.get("id")=="TRADE-SCORE-001" and x.get("status")=="DATA_DERIVED_SCALING_IMPLEMENTED"
          for x in (backlog.get("improve_now_with_existing_evidence") or [])
      ),
      "evidence_ladder_replaces_strict_no_replacement_rule":backlog.get("policy",{}).get("no_arbitrary_coefficient_replacement_without_stronger_basis") is True
          and "no_coefficient_replacement_without_incremental_evidence" not in backlog.get("policy",{}),
      "provisional_policy_requires_bounds_and_versioning":provisional_policy.get("replacement_rules",{}).get("provisional_replacements_must_be_bounded") is True
          and provisional_policy.get("replacement_rules",{}).get("provisional_replacements_must_be_versioned") is True,
      "roster_interaction_acceptance_duplicate_disabled":"MAX_ACCEPTANCE_FIT_SHIFT = 0.0" in interaction
          and '"acceptance_fit_shift": 0.0' in interaction,
    }
    payload={
      "model_version":"FSFFL-Heuristic-Refinement-First-Pass-Audit-1.0",
      "passed":all(findings.values()),
      "findings":findings,
      "policy":{
        "structural_elimination_preferred":True,
        "no_arbitrary_replacement_without_stronger_basis":True,
        "empirical_promotion_still_requires_holdout_improvement":True,
        "final_focal_cut_plan_heuristic_authority_reduced":True,
      }
    }
    OUT.parent.mkdir(parents=True,exist_ok=True)
    OUT.write_text(json.dumps(payload,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(payload,indent=2))
    if not payload["passed"]:
        raise SystemExit("Heuristic refinement first-pass audit failed")

if __name__=="__main__":
    main()
