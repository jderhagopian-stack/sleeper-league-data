#!/usr/bin/env python3
"""Govern final trade-ranking composition after structural de-duplication.

The active strategic score must use primitive evidence channels. Composite GM
summaries may remain available for explanation, but they must not receive a
second positive weight on top of their underlying value families. Negotiation
plausibility is also kept outside the focal strategic-value score.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/"data"; OUT=DATA/"audit"; OUT.mkdir(parents=True,exist_ok=True)
V20=ROOT/"script"/"run_trade_market_sweep_v20.py"
V23=ROOT/"script"/"run_trade_market_sweep_v23.py"
OVERLAY=ROOT/"script"/"decision_lab_state_aware.py"
UTILITY=ROOT/"script"/"decision_utility.py"
ABLATION=ROOT/"script"/"audit_final_score_ablation.py"
REGISTRY=DATA/"model_parameter_registry.json"
MODEL_VERSION="FSFFL-Final-Trade-Ranking-Governance-3.0"

def load(path,default=None):
    if not path.exists(): return default
    return json.loads(path.read_text(encoding="utf-8"))

def main():
    v20=V20.read_text(encoding="utf-8")
    v23=V23.read_text(encoding="utf-8")
    overlay=OVERLAY.read_text(encoding="utf-8")
    utility=UTILITY.read_text(encoding="utf-8")
    ablation=ABLATION.read_text(encoding="utf-8") if ABLATION.exists() else ""
    registry=load(REGISTRY,{}) or {}

    primitive_formula=(
      'DECISION_UTILITY = SCRIPT / "decision_utility.py"' in v20
      and 'resolved = utility.score(sim)' in v20
      and all(x in utility for x in [
        'MODEL_VERSION = "FSFFL-Shared-Decision-Utility-2.0"',
        'statistics.median(values.values())',
        'baseline_team_market_redraft_value',
        'ref = sim.get("league_reference") or {}',
        '"fixed_unit_conversion_coefficients_used": False',
        '"optionality_incremental_value_authorized": False',
        '"negotiation_plausibility_incremental_weight": 0.0',
      ])
      and all(x not in utility for x in [
        'CURRENT_TITLE_SCALE', 'CURRENT_PLAYOFF_SCALE', 'CURRENT_WINS_SCALE',
        'CURRENT_POINTS_SCALE', 'FUTURE_OPTIONALITY_SCALE', 'LIQUIDITY_SCALE',
        'RESILIENCE_SCALE', 'OPPONENT_EXTERNALITY_SCALE',
      ])
    )
    plausibility_contaminates_strategic=(
      '+ 1200.0 * plausibility' in v20
      or 'score -= 3000.0' in v20
      or 'score -= 6000.0' in v20
    )
    old_nested_formula=any(x in v20 for x in [
      'future_block = dynasty + 0.30 * break_glass + 0.18 * optionality',
      'resilience_block = 0.15 * strategic + 0.08 * break_glass',
    ])
    direct_resilience=all(x in overlay for x in [
      '"replacement_resilience_score"',
      '"resilience_value_delta"',
      '"composite_channels_diagnostic_only": ["strategic_value_delta", "break_glass_delta"]',
    ])
    composites_still_reported=(
      '"strategic_value_delta"' in overlay and '"break_glass_delta"' in overlay
    )
    composites_active_in_final=(
      'strategic_value_delta' in utility
      or 'break_glass_delta' in utility
      or '0.30 * break_glass' in utility
      or '0.15 * strategic' in utility
    )

    behavior_reuse=all(x in v23 for x in [
      'acceptance = clamp(sf(br.get("heuristic_acceptance_fit_score"), .5), 0, 1)',
      'behavior = clamp(.50 + sf((br.get("owner_behavior") or {}).get("adjustment")) / .32, 0, 1)',
      'score = .50 * strategic + .30 * acceptance + .20 * behavior',
    ])
    ranker=(ROOT/"script"/"negotiation_ranking.py").read_text(encoding="utf-8")
    same_reuse_v20=not (
      'return nr.recompute_from_row(row)' in v20
      and 'ACCEPTANCE_WEIGHT = 0.0' in ranker
      and 'OWNER_BEHAVIOR_WEIGHT = 0.0' in ranker
    )

    prior_ablation_evidence=all(x in ablation for x in [
      'score_without_strategic_composite',
      'score_without_repeated_break_glass',
      'score_without_all_three_overlap_channels',
      'historical_validation": False',
      'coefficient_tuning": False',
    ])

    params={str(x.get("id")):x for x in (registry.get("parameters") or [])}
    trade_score=params.get("TRADE-SCORE-001",{})
    registry_ok=(
      trade_score.get("authoritative_use") is False
      and trade_score.get("status")=="DATA_DERIVED_LEAGUE_RELATIVE_SCALING_ACTIVE"
    )

    findings=[
      {
        "id":"FINAL-SCORE-OVERLAP-001","severity":"HIGH",
        "status":"STRUCTURALLY_DEDUPLICATED_PRIMITIVE_CHANNELS" if primitive_formula and not composites_active_in_final and not plausibility_contaminates_strategic else "UNRESOLVED",
        "observation":"The shared focal utility now converts correlated Simulator outcomes with league-relative denominators and a median ensemble, then uses the roster's observed market-redraft scale. Future value stays on the market-dynasty scale; liquidity and direct replacement resilience stay value-denominated. No fixed unit-conversion coefficients remain, and optionality/composite GM summaries are diagnostic only.",
        "authoritative_incremental_claim_allowed":False,
      },
      {
        "id":"FINAL-SCORE-RESILIENCE-001","severity":"INFO",
        "status":"DIRECT_ROSTER_REPLACEMENT_CHANNEL_ACTIVE" if direct_resilience else "MISSING",
        "observation":"Resilience is sourced from the team-specific lineup reoptimization signal rather than from the broader strategic composite.",
        "authoritative_long_run_war_claim_allowed":False,
      },
      {
        "id":"FINAL-RANK-SCALING-001","severity":"INFO",
        "status":"FIXED_UNIT_CONVERSION_COEFFICIENTS_REMOVED_DATA_DERIVED_SCALING_ACTIVE",
        "observation":"The prior fixed title/playoff/wins/points/liquidity/resilience conversion constants are removed. Current utility uses canonical league-relative Simulator means and observed team market-redraft scale. Objective-state preference weights remain a governed provisional prior until outcome validation.",
        "authoritative_empirical_claim_allowed":False,
      },
      {
        "id":"FINAL-NEGOTIATION-BEHAVIOR-REUSE-001","severity":"CRITICAL" if behavior_reuse or same_reuse_v20 else "INFO",
        "status":"EXPLICIT_SIGNAL_REUSE_DETECTED" if behavior_reuse or same_reuse_v20 else "NO_REUSE_DETECTED",
        "observation":"Owner behavior must not receive a second positive ranking weight after it has already modified acceptance fit.",
        "authoritative_incremental_claim_allowed":False,
      },
    ]

    payload={
      "model_version":MODEL_VERSION,
      "shared_decision_utility_model":"FSFFL-Shared-Decision-Utility-2.0",
      "production_behavior_changed":True,
      "policy":{
        "primitive_channels_only_in_final_score":True,
        "negotiation_plausibility_separate_from_focal_strategic_value":True,
        "composite_gm_channels_are_diagnostic_only":True,
        "do_not_retune_to_preserve_visual_rankings":True,
        "structural_deduplication_is_not_empirical_calibration":True,
        "fixed_unit_conversion_coefficients_removed":True,
        "objective_preference_weights_remain_provisional":True,
        "final_rank_coefficient_promotion_requires_out_of_sample_improvement":True,
      },
      "summary":{
        "primitive_post_sim_formula_detected":primitive_formula,
        "negotiation_plausibility_contaminates_strategic_value":plausibility_contaminates_strategic,
        "old_nested_formula_detected":old_nested_formula,
        "nested_strategic_channel_overlap_detected":composites_active_in_final,
        "direct_roster_replacement_resilience_detected":direct_resilience,
        "composite_channels_still_available_for_diagnostics":composites_still_reported,
        "prior_grouped_ablation_evidence_preserved":prior_ablation_evidence,
        "behavior_reused_inside_and_outside_acceptance":bool(behavior_reuse or same_reuse_v20),
        "trade_score_registry_consistent":registry_ok,
      },
      "findings":findings,
    }
    (OUT/"final_trade_ranking_governance_audit.json").write_text(json.dumps(payload,indent=2),encoding="utf-8")
    print(json.dumps(payload["summary"],indent=2))
    if not primitive_formula or old_nested_formula or composites_active_in_final or plausibility_contaminates_strategic:
        raise SystemExit("Final score is not cleanly composed from primitive strategic channels")
    if not direct_resilience:
        raise SystemExit("Direct roster-replacement resilience is missing")
    if not prior_ablation_evidence:
        raise SystemExit("Prior grouped ablation evidence is no longer available")
    if not registry_ok:
        raise SystemExit("TRADE-SCORE-001 governance drifted")

if __name__=="__main__":
    main()
