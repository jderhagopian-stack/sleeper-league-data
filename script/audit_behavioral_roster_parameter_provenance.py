#!/usr/bin/env python3
"""Audit exact high-impact behavioral and roster heuristics without changing them."""
from __future__ import annotations
import json,re
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
OUT=ROOT/"data/audit/behavioral_roster_parameter_provenance.json"
BI=ROOT/"script/behavioral_intelligence_v3.py"
CUT=ROOT/"script/roster_aware_trade.py"
RI=ROOT/"script/roster_interaction.py"

def txt(p): return p.read_text(encoding="utf-8")

def must(pattern,s,name):
    m=re.search(pattern,s,re.MULTILINE)
    if not m: raise SystemExit(f"expected runtime parameter missing: {name}")
    return m.groups() if m.groups() else m.group(0)

def main():
    bi,cut,ri=txt(BI),txt(CUT),txt(RI)

    findings=[
      {
        "parameter_id":"BEHAVIOR-OPPORTUNITY-SMOOTHING-001",
        "path":"script/behavioral_intelligence_v3.py",
        "parameter_name":"OPPORTUNITY_SMOOTHING",
        "current_value":float(must(r"OPPORTUNITY_SMOOTHING\s*=\s*([0-9.]+)",bi,"OPPORTUNITY_SMOOTHING")[0]),
        "runtime_authority":"ACTIVE_IN_LEAVE_ONE_MANAGER_OUT_POSITION_OPPORTUNITY_PRIOR",
        "evidence_classification":"UNVALIDATED_EXPERT_PRIOR",
        "identifiability_class":"DIRECTLY_ESTIMABLE",
        "recommended_action":"RE_ESTIMATE",
        "uncertainty_status":"HIGH",
        "reason":"Laplace-style smoothing is sensible but its strength is hand-set; sparse-manager predictive holdout can estimate whether this amount is appropriate.",
        "promotion_evidence":"Time-ordered held-out manager action prediction versus unsmoothed and alternative smoothing strengths."
      },
      {
        "parameter_id":"BEHAVIOR-NEED-FLOOR-001",
        "path":"script/behavioral_intelligence_v3.py",
        "parameter_name":"NEED_FLOOR",
        "current_value":float(must(r"NEED_FLOOR\s*=\s*([0-9.]+)",bi,"NEED_FLOOR")[0]),
        "runtime_authority":"ACTIVE_IN_EXPECTED_POSITION_SHARE_NORMALIZATION",
        "evidence_classification":"UNVALIDATED_EXPERT_PRIOR",
        "identifiability_class":"DIRECTLY_ESTIMABLE",
        "recommended_action":"RE_ESTIMATE",
        "uncertainty_status":"HIGH",
        "reason":"Controls how strongly roster need can suppress baseline positional opportunity and therefore affects inferred manager preference residuals.",
        "promotion_evidence":"Held-out action prediction and preference-residual stability across seasons/managers; strongly shrink manager effects."
      },
      {
        "parameter_id":"BEHAVIOR-CONFIDENCE-CAP-001",
        "path":"script/behavioral_intelligence_v3.py",
        "parameter_name":"confidence cap",
        "current_value":float(must(r"min\(\.([0-9]+),\s*shrinkage_factor",bi,"confidence cap")[0])/100.0,
        "runtime_authority":"DESCRIPTIVE_CONFIDENCE_ONLY_UNLESS_CONSUMER_REUSES_FIELD",
        "evidence_classification":"UNVALIDATED_EXPERT_PRIOR",
        "identifiability_class":"RULE_OR_RUNTIME_MECHANIC",
        "recommended_action":"DIAGNOSTIC_ONLY",
        "uncertainty_status":"MEDIUM",
        "reason":"Cap does not estimate a calibrated probability; should remain descriptive unless validated against held-out outcomes.",
        "promotion_evidence":"Reliability curve only if confidence is ever exposed as probability-like authority."
      },
      {
        "parameter_id":"BEHAVIOR-SHRINKAGE-FORM-001",
        "path":"script/behavioral_intelligence_v3.py",
        "parameter_name":"w/(w+league_median_effective_sample)",
        "current_value":"adaptive empirical-Bayes-style shrinkage",
        "runtime_authority":"ACTIVE_MANAGER_SPECIFIC_SIGNAL_SHRINKAGE",
        "evidence_classification":"REGULARIZED_OR_SHRINKAGE_ESTIMATE",
        "identifiability_class":"DIRECTLY_ESTIMABLE",
        "recommended_action":"KEEP",
        "uncertainty_status":"MEDIUM",
        "reason":"Data-adaptive prior strength is materially more defensible than a hand-set saturation constant, but predictive holdout is still required for empirical promotion.",
        "promotion_evidence":"Time-ordered held-out manager-action ranking/log-loss versus league-only and fixed-prior baselines."
      },
      {
        "parameter_id":"ROSTER-CUT-RETENTION-FORMULA-001",
        "path":"script/roster_aware_trade.py",
        "parameter_name":"retention cost additive coefficients and status multipliers",
        "current_value":{
          "break_glass_weight":0.12,
          "depth_weight":0.06,
          "liquidity_market_dynasty_weight":0.04,
          "starter_multiplier":1.75,
          "core_multipliers":{"franchise_cornerstone":2.0,"core_high_hold":1.7,"core_pick":1.35,"liquid_asset":1.12}
        },
        "runtime_authority":"PRESCREEN_AND_FALLBACK_CUT_ORDERING; FINAL_FOCAL_TRACTABLE_CASES_CAN_BE_SIMULATION_OPTIMIZED",
        "evidence_classification":"LEGACY_ARBITRARY_HEURISTIC",
        "identifiability_class":"SIMULATION_IDENTIFIABLE",
        "recommended_action":"REPLACE_WITH_DATA_DERIVED_SCALE",
        "uncertainty_status":"HIGH",
        "reason":"These hand-set coefficients can alter which incumbent is cut, but canonical downstream simulation can identify the true marginal consequence for tractable cut plans.",
        "promotion_evidence":"Expand exact/top-k canonical simulation of legal cut plans; use retention formula only as computational prescreen where exhaustive evaluation is impractical."
      },
      {
        "parameter_id":"ROSTER-CUT-SHORTLIST-001",
        "path":"script/roster_aware_trade.py",
        "parameter_name":"CUT_SHORTLIST_SIZE",
        "current_value":int(must(r"CUT_SHORTLIST_SIZE\s*=\s*([0-9]+)",cut,"CUT_SHORTLIST_SIZE")[0]),
        "runtime_authority":"COMPUTATIONAL_SEARCH_BUDGET",
        "evidence_classification":"UNVALIDATED_EXPERT_PRIOR",
        "identifiability_class":"RULE_OR_RUNTIME_MECHANIC",
        "recommended_action":"RE_ESTIMATE",
        "uncertainty_status":"LOW_ECONOMIC_MEDIUM_RECALL",
        "reason":"Not an economic coefficient, but a shortlist of three can create omission risk if the true best legal cut lies outside it.",
        "promotion_evidence":"Recall/convergence benchmark versus exhaustive cut-plan evaluation on tractable historical and synthetic rosters."
      },
      {
        "parameter_id":"ROSTER-INTERACTION-UNCERTAINTY-BLEND-001",
        "path":"script/roster_interaction.py",
        "parameter_name":"downside/injury/role uncertainty blend",
        "current_value":{"downside":0.45,"availability_or_injury":0.30,"role":0.25,"floor":0.05,"cap":0.85},
        "runtime_authority":"DIAGNOSTIC_ROSTER_INTERACTION_CONTEXT",
        "evidence_classification":"LEGACY_ARBITRARY_HEURISTIC",
        "identifiability_class":"UNIDENTIFIED_OR_DUPLICATE",
        "recommended_action":"DIAGNOSTIC_ONLY",
        "uncertainty_status":"HIGH",
        "reason":"Availability and role risk are already represented upstream in canonical projections/Simulator; incremental roster-interaction value is disabled to avoid double counting.",
        "promotion_evidence":"Only re-enable if residual lineup-availability value remains after canonical Simulator effects."
      },
      {
        "parameter_id":"ROSTER-PAIR-INSURANCE-001",
        "path":"script/roster_interaction.py",
        "parameter_name":"PAIR_CAPTURE_SCALE / MAX_PAIR_INSURANCE_PCT / MAX_PORTFOLIO_ADJUSTMENT",
        "current_value":{
          "pair_capture_scale":float(must(r"PAIR_CAPTURE_SCALE\s*=\s*([0-9.]+)",ri,"PAIR_CAPTURE_SCALE")[0]),
          "max_pair_insurance_pct":float(must(r"MAX_PAIR_INSURANCE_PCT\s*=\s*([0-9.]+)",ri,"MAX_PAIR_INSURANCE_PCT")[0]),
          "max_portfolio_adjustment":float(must(r"MAX_PORTFOLIO_ADJUSTMENT\s*=\s*([0-9.]+)",ri,"MAX_PORTFOLIO_ADJUSTMENT")[0])
        },
        "runtime_authority":"DIAGNOSTIC_ONLY_INCREMENTAL_FINAL_VALUE_DISABLED",
        "evidence_classification":"LEGACY_ARBITRARY_HEURISTIC",
        "identifiability_class":"UNIDENTIFIED_OR_DUPLICATE",
        "recommended_action":"DIAGNOSTIC_ONLY",
        "uncertainty_status":"HIGH",
        "reason":"Same-team insurance is plausible but not independently identified beyond lineup replacement resilience; current governance correctly prevents it from repricing final utility.",
        "promotion_evidence":"Incremental residual simulation or historical availability evidence beyond existing resilience."
      }
    ]

    report={
      "model_version":"FSFFL-Behavioral-Roster-Parameter-Provenance-1.0",
      "authority":"RESEARCH_AUDIT_NON_AUTHORITATIVE",
      "production_behavior_changed":False,
      "findings":findings,
      "summary":{
        "finding_count":len(findings),
        "legacy_arbitrary_count":sum(x["evidence_classification"]=="LEGACY_ARBITRARY_HEURISTIC" for x in findings),
        "unvalidated_prior_count":sum(x["evidence_classification"]=="UNVALIDATED_EXPERT_PRIOR" for x in findings),
        "regularized_count":sum(x["evidence_classification"]=="REGULARIZED_OR_SHRINKAGE_ESTIMATE" for x in findings),
        "production_changes_recommended_now":0
      },
      "conclusion":"The highest-risk remaining exact coefficients in these families are either diagnostic/prescreen-only or can be displaced structurally by canonical simulation. No coefficient promotion is justified from provenance alone."
    }
    OUT.parent.mkdir(parents=True,exist_ok=True)
    OUT.write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(report["summary"],indent=2))

if __name__=="__main__":main()
