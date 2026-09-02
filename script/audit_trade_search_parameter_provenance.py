#!/usr/bin/env python3
"""Audit exact GM3 proactive-discovery heuristics separately from Trade Decision and final utility."""
from __future__ import annotations
import json,re
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
ENGINE=ROOT/"script/build_fsffl_gm_engine.py"
OUT=ROOT/"data/audit/trade_search_parameter_provenance.json"

def main():
    s=ENGINE.read_text(encoding="utf-8")
    required=[
      "movability = v * (1.0 - 0.62 * strategic) * (0.85 + 0.15 * liquidity)",
      "target_screen = target_screen[:30]",
      "outgoing_candidates = outgoing_candidates[:18]",
      "if dyn < 1000 and need < 0.68:",
      "if static_ratio < 0.68:",
      "0.30 * need",
      "0.24 * clamp(red / 8000.0",
      "0.22 * clamp(dyn / 8500.0",
      "0.14 * clamp((gap/max(seller_value,1.0)+0.30)/0.60",
      "0.10 * (1.0 - seller_strategic)",
    ]
    missing=[x for x in required if x not in s]
    if missing:
        raise SystemExit("trade search runtime changed; re-audit: "+repr(missing))

    findings=[
      {
        "parameter_id":"TRADE-SEARCH-MOVABILITY-001",
        "parameter_name":"outgoing movability formula",
        "current_value":"value * (1 - 0.62*strategic) * (0.85 + 0.15*liquidity)",
        "runtime_authority":"SEARCH_ORDERING_ONLY",
        "evidence_classification":"LEGACY_ARBITRARY_HEURISTIC",
        "identifiability_class":"UNIDENTIFIED_OR_DUPLICATE",
        "decision_impact":"MEDIUM_HIGH_THROUGH_CANDIDATE_RECALL",
        "recommended_action":"REPLACE_WITH_DATA_DERIVED_SCALE",
        "reason":"Strategic/liquidity fields can be provisional or diagnostic and should not silently determine which assets are even considered.",
        "evidence_needed":"Recall benchmark against broader/exhaustive tractable outgoing universe; prefer multi-lane coverage or simple value-based inclusion over fitted hidden preference weights."
      },
      {
        "parameter_id":"TRADE-SEARCH-TARGET-SCORE-001",
        "parameter_name":"target prescreen weighted score",
        "current_value":{"need":0.30,"redraft":0.24,"dynasty":0.22,"owner_gap":0.14,"seller_strategic_inverse":0.10,"redraft_scale":8000.0,"dynasty_scale":8500.0},
        "runtime_authority":"ACTIVE_GM3_PROACTIVE_TARGET_SEARCH_ORDERING_ONLY; NOT_TRADE_DECISION_PRESCREEN",
        "evidence_classification":"LEGACY_ARBITRARY_HEURISTIC",
        "identifiability_class":"UNIDENTIFIED_OR_DUPLICATE",
        "decision_impact":"HIGH_IF_TOP_30_TRUNCATION_OMITS_VALID_TARGETS",
        "recommended_action":"REPLACE_WITH_DATA_DERIVED_SCALE",
        "reason":"The weighted blend is not final utility but can create omission bias before canonical simulation.",
        "evidence_needed":"Target recall benchmark versus broad canonical GM3 evaluation across archetypes; use separate coverage lanes rather than one composite score where possible."
      },
      {
        "parameter_id":"TRADE-SEARCH-TARGET-CUTOFF-001",
        "parameter_name":"low dynasty / low need exclusion",
        "current_value":{"dynasty_value_floor":1000.0,"need_floor":0.68},
        "runtime_authority":"ACTIVE_GM3_PROACTIVE_DISCOVERY_HARD_EXCLUSION; NOT_TRADE_DECISION_PRESCREEN",
        "evidence_classification":"LEGACY_ARBITRARY_HEURISTIC",
        "identifiability_class":"RULE_OR_RUNTIME_MECHANIC",
        "decision_impact":"HIGH_FOR_FALSE_NEGATIVE_RECALL",
        "recommended_action":"ELIMINATE",
        "reason":"A hard exclusion can prevent a truly positive low-cost/depth target from reaching canonical evaluation; computational limits should be handled by coverage budgets rather than economic cliffs.",
        "evidence_needed":"Remove or convert to non-exclusive lane prioritization if runtime allows; otherwise demonstrate near-perfect recall on exhaustive tractable fixtures."
      },
      {
        "parameter_id":"TRADE-SEARCH-TARGET-BUDGET-001",
        "parameter_name":"target_screen top-k",
        "current_value":30,
        "runtime_authority":"ACTIVE_GM3_PROACTIVE_DISCOVERY_COMPUTATIONAL_BUDGET",
        "evidence_classification":"UNVALIDATED_EXPERT_PRIOR",
        "identifiability_class":"RULE_OR_RUNTIME_MECHANIC",
        "decision_impact":"MEDIUM_HIGH_THROUGH_RECALL",
        "recommended_action":"RE_ESTIMATE",
        "reason":"Top-k is not economics, but it can make upstream heuristic ordering de facto authority.",
        "evidence_needed":"Recall/convergence curve of discovered positive-utility targets versus target budget."
      },
      {
        "parameter_id":"TRADE-SEARCH-OUTGOING-BUDGET-001",
        "parameter_name":"outgoing candidate top-k",
        "current_value":18,
        "runtime_authority":"COMPUTATIONAL_SEARCH_BUDGET",
        "evidence_classification":"UNVALIDATED_EXPERT_PRIOR",
        "identifiability_class":"RULE_OR_RUNTIME_MECHANIC",
        "decision_impact":"MEDIUM_HIGH_THROUGH_PACKAGE_RECALL",
        "recommended_action":"RE_ESTIMATE",
        "reason":"Budget can hide packages if coupled to heuristic movability ordering.",
        "evidence_needed":"Recall/convergence versus expanded outgoing candidate counts and multi-lane inclusion."
      },
      {
        "parameter_id":"TRADE-SEARCH-SELLER-RATIO-FLOOR-001",
        "parameter_name":"static seller ratio floor",
        "current_value":0.68,
        "runtime_authority":"ACTIVE_GM3_PRICE_DISCOVERY_HARD_PACKAGE_EXCLUSION_BEFORE_FULL_GM3_EVALUATION",
        "evidence_classification":"UNVALIDATED_EXPERT_PRIOR",
        "identifiability_class":"DIRECTLY_ESTIMABLE",
        "decision_impact":"HIGH_NEAR_PRICE_FRONTIER",
        "recommended_action":"RE_ESTIMATE",
        "reason":"May be a useful computational sanity bound, but can truncate packages that become plausible after replacement relief or other canonical effects.",
        "evidence_needed":"Exhaustive tractable price-frontier recall plus historical transaction geometry; require no material positive-utility candidates below the floor."
      },
      {
        "parameter_id":"TRADE-SEARCH-PRELIM-SCORE-001",
        "parameter_name":"package preliminary weighted score",
        "current_value":{"focal_surplus":0.45,"fairness":0.22,"need":0.18,"motivation":0.15},
        "runtime_authority":"ACTIVE_GM3_DISCOVERY_ORDERING_NOT_FINAL_UTILITY_OR_TRADE_DECISION_PRESCREEN",
        "evidence_classification":"LEGACY_ARBITRARY_HEURISTIC",
        "identifiability_class":"UNIDENTIFIED_OR_DUPLICATE",
        "decision_impact":"LOW_IF_ALL_PRELIM_ROWS_FLOW_DOWNSTREAM_HIGH_IF_LATER_BUDGETED",
        "recommended_action":"DIAGNOSTIC_ONLY",
        "reason":"Should not become a hidden second utility. It is acceptable only while downstream evaluation coverage is not truncated by this ordering.",
        "evidence_needed":"CI assertion that final/bounded evaluation budgets do not allow prelim score to suppress materially better canonical candidates."
      }
    ]
    report={
      "model_version":"FSFFL-Trade-Search-Parameter-Provenance-1.0",
      "authority":"RESEARCH_AUDIT_NON_AUTHORITATIVE",
      "production_behavior_changed":False,
      "findings":findings,
      "summary":{
        "finding_count":len(findings),
        "hard_exclusion_count":sum("HARD" in x["runtime_authority"] for x in findings),
        "legacy_arbitrary_count":sum(x["evidence_classification"]=="LEGACY_ARBITRARY_HEURISTIC" for x in findings),
        "final_utility_coefficients_created":0,
        "production_changes_recommended_in_this_pr":0,
        "trade_decision_prescreen_coefficients_implicated":0
      },
      "policy":{
        "search_heuristic_is_not_final_decision_authority":True,\n        "trade_decision_current_prescreen_is_separate_and_coefficient_free":True,\n        "these_findings_apply_to_gm3_proactive_discovery_and_price_search":True,
        "search_budget_can_still_create_omission_bias":True,
        "hard_search_cliffs_require_recall_evidence":True,
        "prefer_multi_lane_coverage_over_single_composite_search_score":True,
        "do_not_fit_search_to_hurts_so_good_rankings":True
      }
    }
    OUT.parent.mkdir(parents=True,exist_ok=True)
    OUT.write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(report["summary"],indent=2))

if __name__=="__main__":main()
