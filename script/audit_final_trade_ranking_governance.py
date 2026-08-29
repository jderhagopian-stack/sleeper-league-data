#!/usr/bin/env python3
"""Govern final trade-ranking composition after structural de-duplication.

The active final score must use primitive evidence channels. Composite GM
summaries may remain available for explanation, but they must not receive a
second positive weight on top of their underlying value families.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/"data"; OUT=DATA/"audit"; OUT.mkdir(parents=True,exist_ok=True)
V20=ROOT/"script"/"run_trade_market_sweep_v20.py"
V23=ROOT/"script"/"run_trade_market_sweep_v23.py"
OVERLAY=ROOT/"script"/"decision_lab_state_aware.py"
ABLATION=ROOT/"script"/"audit_final_score_ablation.py"
REGISTRY=DATA/"model_parameter_registry.json"
MODEL_VERSION="FSFFL-Final-Trade-Ranking-Governance-2.0"

def load(path,default=None):
    if not path.exists(): return default
    return json.loads(path.read_text(encoding="utf-8"))

def main():
    v20=V20.read_text(encoding="utf-8")
    v23=V23.read_text(encoding="utf-8")
    overlay=OVERLAY.read_text(encoding="utf-8")
    ablation=ABLATION.read_text(encoding="utf-8") if ABLATION.exists() else ""
    registry=load(REGISTRY,{}) or {}

    primitive_formula=all(x in v20 for x in [
      'current_block = 25000.0 * title + 5000.0 * playoff + 400.0 * wins + 1.25 * points',
      'future_block = dynasty + 0.18 * optionality',
      'liquidity_block = 0.25 * liquidity',
      'resilience_block = 0.15 * resilience',
      'resilience = sf(s.get("resilience_value_delta"))',
      '- current_mult * 12000.0 * externality + 1200.0 * plausibility',
    ])
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
      'strategic = sf(s.get("strategic_value_delta"))' in v20
      or 'break_glass = sf(s.get("break_glass_delta"))' in v20
      or '0.30 * break_glass' in v20
      or '0.15 * strategic' in v20
    )

    behavior_reuse=all(x in v23 for x in [
      'acceptance = clamp(sf(br.get("heuristic_acceptance_fit_score"), .5), 0, 1)',
      'behavior = clamp(.50 + sf((br.get("owner_behavior") or {}).get("adjustment")) / .32, 0, 1)',
      'score = .50 * strategic + .30 * acceptance + .20 * behavior',
    ])
    same_reuse_v20=all(x in v20 for x in [
      'acceptance = clamp(sf(br.get("heuristic_acceptance_fit_score"), .5), 0.0, 1.0)',
      'behavior = clamp(.50 + sf((br.get("owner_behavior") or {}).get("adjustment")) / .32, 0.0, 1.0)',
      'score = .50 * strategic + .30 * acceptance + .20 * behavior',
    ])

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
      and trade_score.get("status")=="PRIMITIVE_CHANNELS_ACTIVE_WEIGHTS_PROVISIONAL"
    )

    findings=[
      {
        "id":"FINAL-SCORE-OVERLAP-001","severity":"HIGH",
        "status":"STRUCTURALLY_DEDUPLICATED_PRIMITIVE_CHANNELS" if primitive_formula and not composites_active_in_final else "UNRESOLVED",
        "observation":"The final post-simulation score now uses simulated current-season impact, dynasty market delta, optionality, liquidity and direct roster-replacement resilience. strategic_value_delta and break_glass_delta remain reportable diagnostics but receive zero incremental final-score weight.",
        "authoritative_incremental_claim_allowed":False,
      },
      {
        "id":"FINAL-SCORE-RESILIENCE-001","severity":"INFO",
        "status":"DIRECT_ROSTER_REPLACEMENT_CHANNEL_ACTIVE" if direct_resilience else "MISSING",
        "observation":"Resilience is sourced from the team-specific lineup reoptimization signal rather than from the broader strategic composite.",
        "authoritative_long_run_war_claim_allowed":False,
      },
      {
        "id":"FINAL-RANK-WEIGHTS-001","severity":"HIGH",
        "status":"PROVISIONAL_WEIGHTS_NOT_EMPIRICALLY_IDENTIFIED",
        "observation":"Structural de-duplication does not validate the surviving scaling constants. Those remain provisional until a defensible historical ranking/choice target supports time-ordered out-of-sample calibration.",
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
      "production_behavior_changed":True,
      "policy":{
        "primitive_channels_only_in_final_score":True,
        "composite_gm_channels_are_diagnostic_only":True,
        "do_not_retune_to_preserve_visual_rankings":True,
        "structural_deduplication_is_not_empirical_calibration":True,
        "surviving_weights_remain_provisional":True,
        "final_rank_coefficient_promotion_requires_out_of_sample_improvement":True,
      },
      "summary":{
        "primitive_post_sim_formula_detected":primitive_formula,
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
    if not primitive_formula or old_nested_formula or composites_active_in_final:
        raise SystemExit("Final score is not cleanly composed from primitive channels")
    if not direct_resilience:
        raise SystemExit("Direct roster-replacement resilience is missing")
    if not prior_ablation_evidence:
        raise SystemExit("Prior grouped ablation evidence is no longer available")
    if not registry_ok:
        raise SystemExit("TRADE-SCORE-001 governance drifted")

if __name__=="__main__":
    main()
