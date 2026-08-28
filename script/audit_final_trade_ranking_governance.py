#!/usr/bin/env python3
"""Governance audit for final trade-ranking formulas and signal overlap.

This is a structural audit only. It does not retune production coefficients or
change rankings. It identifies nested/reused channels and requires ablations or
independent validation before any final-ranking coefficient is promoted.
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
MODEL_VERSION="FSFFL-Final-Trade-Ranking-Governance-1.0"

def load(path,default=None):
    if not path.exists(): return default
    return json.loads(path.read_text(encoding="utf-8"))

def main():
    v20=V20.read_text(encoding="utf-8")
    v23=V23.read_text(encoding="utf-8")
    overlay=OVERLAY.read_text(encoding="utf-8")
    ablation=ABLATION.read_text(encoding="utf-8") if ABLATION.exists() else ""
    registry=load(REGISTRY,{}) or {}

    post_score_markers=all(x in v20 for x in [
      'current_block = 25000.0 * title + 5000.0 * playoff + 400.0 * wins + 1.25 * points',
      'future_block = dynasty + 0.30 * break_glass + 0.18 * optionality',
      'liquidity_block = 0.25 * liquidity',
      'resilience_block = 0.15 * strategic + 0.08 * break_glass',
      '- current_mult * 12000.0 * externality + 1200.0 * plausibility',
      'if row.get("plausibility") == "LOW": score -= 3000.0',
      'elif row.get("plausibility") == "THEORETICAL_ONLY": score -= 6000.0',
    ])
    structural_overlap=(
      'future_block = dynasty + 0.30 * break_glass + 0.18 * optionality' in v20
      and 'resilience_block = 0.15 * strategic + 0.08 * break_glass' in v20
      and 'strategic_value_delta' in overlay
      and 'liquidity_value_delta' in overlay
      and 'optionality_value_delta' in overlay
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
    ablation_ready=all(x in ablation for x in [
      'score_without_strategic_composite',
      'score_without_repeated_break_glass',
      'score_without_direct_liquidity',
      'score_without_all_three_overlap_channels',
      'historical_validation": False',
      'coefficient_tuning": False',
    ])
    params={str(x.get("id")):x for x in (registry.get("parameters") or [])}
    # Registry naming evolved across versions; require at least one governed
    # final-ranking/decision family and that none claims authoritative empirical use.
    final_rows=[x for x in params.values() if any(tok in str(x.get("component") or "").lower() for tok in ("ranking","decision lab","trade discovery"))]
    registry_non_authoritative=bool(final_rows) and all(x.get("authoritative_use") is False for x in final_rows)

    findings=[
      {
        "id":"FINAL-SCORE-OVERLAP-001","severity":"HIGH",
        "status":"NESTED_CHANNELS_ABLATION_REQUIRED" if structural_overlap else "IMPLEMENTATION_DRIFT",
        "observation":"The post-simulation score separately consumes dynasty, break-glass, optionality, liquidity and a strategic composite derived from GM profiles. Break-glass is explicitly present in both future and resilience blocks, and strategic/liquidity/optionality originate from the same reprofiled asset bundle. These channels require individual and grouped ablations; additive inclusion is not evidence of incremental value.",
        "authoritative_incremental_claim_allowed":False,
      },
      {
        "id":"FINAL-NEGOTIATION-BEHAVIOR-REUSE-001","severity":"CRITICAL" if behavior_reuse or same_reuse_v20 else "INFO",
        "status":"EXPLICIT_SIGNAL_REUSE_DETECTED" if behavior_reuse or same_reuse_v20 else "NO_REUSE_DETECTED",
        "observation":"Negotiation ranking consumes heuristic acceptance fit after owner behavior has already modified that fit, then separately adds owner_behavior.adjustment again. This is explicit signal reuse. No new weights should be guessed to compensate; the duplicate path must be removed or one component rebuilt from a behavior-free acceptance base only after regression/ablation testing.",
        "authoritative_incremental_claim_allowed":False,
      },
      {
        "id":"FINAL-RANK-WEIGHTS-001","severity":"HIGH",
        "status":"PROVISIONAL_WEIGHTS_NOT_EMPIRICALLY_IDENTIFIED",
        "observation":"The 50/30/20 negotiation blend and large post-simulation scaling constants are operational heuristics. Software regressions can preserve behavior, but empirical promotion requires a defensible outcome/choice target and time-ordered validation. The current accepted/rejected offer denominator is unavailable.",
        "authoritative_empirical_claim_allowed":False,
      },
    ]
    payload={
      "model_version":MODEL_VERSION,
      "production_behavior_changed":False,
      "policy":{
        "do_not_retune_to_preserve_visual_rankings":True,
        "nested_score_channels_require_grouped_ablation":True,
        "explicit_signal_reuse_must_not_be_compensated_with_guessed_weights":True,
        "software_regression_is_not_empirical_validation":True,
        "final_rank_coefficient_promotion_requires_out_of_sample_improvement":True,
        "behavioral_acceptance_double_count_requires_structural_resolution":True,
      },
      "summary":{
        "post_sim_formula_markers_detected":post_score_markers,
        "nested_strategic_channel_overlap_detected":structural_overlap,
        "behavior_reused_inside_and_outside_acceptance":bool(behavior_reuse or same_reuse_v20),
        "existing_final_score_ablation_tool_detected":ablation_ready,
        "governed_final_ranking_families_non_authoritative":registry_non_authoritative,
      },
      "findings":findings,
    }
    (OUT/"final_trade_ranking_governance_audit.json").write_text(json.dumps(payload,indent=2),encoding="utf-8")
    print(json.dumps(payload["summary"],indent=2))
    if not post_score_markers: raise SystemExit("Final post-simulation formula markers drifted")
    if not ablation_ready: raise SystemExit("Final-score grouped ablation tooling is incomplete")
    if not registry_non_authoritative: raise SystemExit("Final-ranking governance registry classification is incomplete or authoritative")

if __name__=="__main__": main()
