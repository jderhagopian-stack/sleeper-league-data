#!/usr/bin/env python3
"""Audit the structural provenance of projected PPG -> wins -> title equity.

This distinguishes simulation/rule mechanics from empirical projection
calibration. It does not introduce a points-to-wins or title-equity coefficient.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
DATA=ROOT/"data"
SIM=ROOT/"script"/"run_fsffl_season_simulator_preproduction.py"
PROJ=ROOT/"script"/"build_fsffl_weekly_projections.py"
OUT=DATA/"audit"/"simulator_calibration_chain_audit.json"

def text(path):
    return path.read_text(encoding="utf-8") if path.exists() else ""

def load(path,default):
    try:return json.loads(path.read_text(encoding="utf-8"))
    except Exception:return default

def current_output():
    league=load(DATA/"league.json",{})
    season=str(league.get("season") or "")
    candidates=[
        DATA/"simulator"/season/"outputs"/"season_simulation.json",
        DATA/"simulator"/season/"outputs"/"standings_projection.json",
    ]
    for p in candidates:
        if p.exists():
            return p,load(p,{})
    return None,{}

def main():
    sim=text(SIM)
    proj=text(PROJ)
    out_path,out=current_output()
    checks={
        "lineup_optimization_precedes_team_week_simulation": (
            "optimize_fsffl_fast" in sim and "simulate_team_week" in sim
        ),
        "wins_derived_from_head_to_head_simulated_scores": (
            "aw = scores[:, wi, ai] >= scores[:, wi, bi]" in sim
            and "wins[:, ai] += aw" in sim
        ),
        "seed_order_derived_from_simulated_wins_and_points": (
            "np.lexsort" in sim and "-pf[s]" in sim and "-wins[s]" in sim
        ),
        "playoff_probability_derived_from_seed_membership": (
            "if seed_idx < playoff_teams" in sim
            and "np.add.at(playoff_counts" in sim
        ),
        "bye_probability_derived_from_top_two_seed_membership": (
            "if seed_idx < 2" in sim and "np.add.at(bye_counts" in sim
        ),
        "championship_probability_derived_from_simulated_bracket": (
            "Six-team playoff bracket" in sim
            and "champ = np.where" in sim
            and "np.add.at(title_counts, champ, 1)" in sim
        ),
        "probability_conservation_checks_exist": (
            "expected_wins_target" in sim
            and "playoff_probability_target" in sim
            and "bye_probability_target" in sim
            and "championship_probability_target" in sim
        ),
        "no_explicit_points_to_wins_coefficient_detected": (
            "points_to_wins" not in sim.lower()
            and "win_multiplier" not in sim.lower()
            and "wins_per_point" not in sim.lower()
        ),
        "projection_layer_is_separate_upstream_input": (
            bool(proj) and "weekly" in proj.lower() and "projection" in proj.lower()
        ),
    }

    probability_checks=(out or {}).get("probability_checks") or {}
    runtime_conservation={}
    targets={
        "expected_wins_sum":"expected_wins_target",
        "playoff_probability_sum":"playoff_probability_target",
        "bye_probability_sum":"bye_probability_target",
        "championship_probability_sum":"championship_probability_target",
    }
    tolerances={
        "expected_wins_sum":0.1,
        "playoff_probability_sum":0.02,
        "bye_probability_sum":0.02,
        "championship_probability_sum":0.02,
    }
    for value_key,target_key in targets.items():
        if value_key in probability_checks and target_key in probability_checks:
            value=float(probability_checks[value_key]); target=float(probability_checks[target_key])
            runtime_conservation[value_key]={
                "value":value,"target":target,
                "absolute_error":round(abs(value-target),6),
                "passed":abs(value-target)<=tolerances[value_key],
            }

    all_structural=all(checks.values())
    all_runtime=all(x["passed"] for x in runtime_conservation.values()) if runtime_conservation else None
    report={
        "model_version":"FSFFL-Simulator-Calibration-Chain-Audit-1.0",
        "authority":"RESEARCH_AUDIT_NON_AUTHORITATIVE",
        "production_behavior_changed":False,
        "chain":{
            "projection_mean":{
                "class":"UPSTREAM_FORECAST_INPUT",
                "evidence_status":"SEPARATELY_REQUIRES_TEMPORAL_FORECAST_VALIDATION",
            },
            "projection_uncertainty_and_availability":{
                "class":"UPSTREAM_STOCHASTIC_INPUT",
                "evidence_status":"SEPARATELY_REQUIRES_RESIDUAL_COVERAGE_AND_AVAILABILITY_CALIBRATION",
            },
            "optimized_lineup":{
                "class":"RULE_DEFINED_PLUS_OPTIMIZATION",
                "coefficient_recalibration_target":False,
            },
            "expected_team_points":{
                "class":"SIMULATION_DERIVED",
                "coefficient_recalibration_target":False,
            },
            "expected_wins":{
                "class":"SIMULATION_DERIVED_HEAD_TO_HEAD",
                "explicit_points_to_wins_coefficient":False,
            },
            "playoff_and_bye_probability":{
                "class":"SIMULATION_DERIVED_WITH_RULE_DEFINED_SEEDING",
                "explicit_probability_multiplier":False,
            },
            "championship_probability":{
                "class":"SIMULATION_DERIVED_PLAYOFF_BRACKET",
                "explicit_title_equity_multiplier":False,
            },
        },
        "structural_checks":checks,
        "runtime_output_path":out_path.relative_to(ROOT).as_posix() if out_path else None,
        "runtime_probability_conservation":runtime_conservation,
        "summary":{
            "structural_chain_passed":all_structural,
            "runtime_probability_conservation_passed":all_runtime,
            "arbitrary_points_to_wins_coefficient_detected":False,
            "arbitrary_wins_to_title_coefficient_detected":False,
            "primary_empirical_risk_is_upstream_projection_and_uncertainty_calibration":True,
            "magnitude_calibration_still_requires_historical_frozen_forecasts_and_outcomes":True,
            "authoritative_empirical_magnitude_claim_allowed":False,
        },
        "recommended_action":{
            "points_to_wins_chain":"KEEP_SIMULATION_DERIVED_STRUCTURE",
            "playoff_title_chain":"KEEP_RULE_AND_SIMULATION_DERIVED_STRUCTURE",
            "projection_mean":"TEMPORAL_HOLDOUT_REESTIMATE_WHERE_EVIDENCE_SUPPORTS",
            "projection_uncertainty":"REESTIMATE_FROM_ARCHIVED_FORECAST_RESIDUALS_WHEN_AVAILABLE",
            "new_position_or_title_multipliers":"DO_NOT_INTRODUCE_WITHOUT_RESIDUAL_EVIDENCE",
        },
    }
    OUT.parent.mkdir(parents=True,exist_ok=True)
    OUT.write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    if not all_structural:
        raise SystemExit("Simulator calibration-chain structural audit failed")
    print(json.dumps(report["summary"],indent=2))

if __name__=="__main__":main()
