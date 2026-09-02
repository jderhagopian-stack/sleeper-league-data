#!/usr/bin/env python3
"""Non-authoritative shadow comparison for competitive-state objective weights.

Compares the incumbent expert prior with simple baselines and an ablation using
the canonical Shared Decision Utility. It does not fit, select, or promote a
replacement curve.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
SCRIPT=ROOT/"script"
DATA=ROOT/"data"
OUT=DATA/"audit"/"state_weight_shadow_research.json"

def loadmod(path,name):
    spec=importlib.util.spec_from_file_location(name,path)
    mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); return mod

weighting=loadmod(SCRIPT/"gm_state_weighting.py","shadow_weights")
utility=loadmod(SCRIPT/"decision_utility.py","shadow_utility")

def normalize(w):
    z=sum(max(0.0,float(v)) for v in w.values()) or 1.0
    return {k:max(0.0,float(w.get(k,0.0)))/z for k in ("current","future","liquidity","resilience")}

def baselines(incumbent):
    cf=normalize({
        "current":incumbent["current"],
        "future":incumbent["future"],
        "liquidity":0.0,
        "resilience":0.0,
    })
    return {
        "incumbent_expert_prior":normalize(incumbent),
        "neutral_equal_four":{"current":.25,"future":.25,"liquidity":.25,"resilience":.25},
        "neutral_current_future_only":{"current":.5,"future":.5,"liquidity":0.0,"resilience":0.0},
        "incumbent_current_future_ablation":cf,
    }

CASES=[
    {
        "id":"CURRENT_GAIN_FUTURE_COST",
        "current":{"points":8.0,"wins":.35,"playoffs":.04,"title":.008},
        "future":-500.0,"liquidity":0.0,"resilience":0.0,
    },
    {
        "id":"FUTURE_GAIN_CURRENT_COST",
        "current":{"points":-6.0,"wins":-.25,"playoffs":-.03,"title":-.006},
        "future":700.0,"liquidity":100.0,"resilience":0.0,
    },
    {
        "id":"LIQUIDITY_ONLY",
        "current":{"points":0.0,"wins":0.0,"playoffs":0.0,"title":0.0},
        "future":0.0,"liquidity":500.0,"resilience":0.0,
    },
    {
        "id":"RESILIENCE_ONLY",
        "current":{"points":0.0,"wins":0.0,"playoffs":0.0,"title":0.0},
        "future":0.0,"liquidity":0.0,"resilience":500.0,
    },
    {
        "id":"BALANCED_SMALL_IMPROVEMENT",
        "current":{"points":2.0,"wins":.08,"playoffs":.01,"title":.002},
        "future":100.0,"liquidity":50.0,"resilience":50.0,
    },
    {
        "id":"CONSOLIDATION_TRADEOFF",
        "current":{"points":5.0,"wins":.2,"playoffs":.025,"title":.005},
        "future":-150.0,"liquidity":-250.0,"resilience":-100.0,
    },
]

def row(case,w):
    c=case["current"]
    return {
        "focus_delta":{
            "expected_points_for":c["points"],
            "expected_wins":c["wins"],
            "playoff_probability":c["playoffs"],
            "championship_probability":c["title"],
        },
        "league_reference":{
            "expected_points_for_mean":100.0,
            "expected_wins_mean":7.0,
            "playoff_probability_mean":.5,
            "championship_probability_mean":1.0/12.0,
        },
        "strategic":{
            "baseline_team_market_redraft_value":10000.0,
            "market_dynasty_delta":case["future"],
            "liquidity_value_delta":case["liquidity"],
            "resilience_value_delta":case["resilience"],
            "objective_weights":w,
            "incremental_channel_authorization":{"liquidity":True,"resilience":True},
        },
        "buyer_championship_probability_delta":0.0,
    }

def main():
    cal=weighting.load_calibration()
    strength_grid=[0.0,.10,.20,.30,.35,.40,.50,.55,.60,.70,.78,.80,.90,1.0]
    rows=[]
    sign_changes=[]
    for strength in strength_grid:
        incumbent=weighting.interpolate(strength,cal.get("anchor_points") or [])
        candidates=baselines(incumbent)
        case_rows=[]
        for case in CASES:
            scores={}
            for name,w in candidates.items():
                s=utility.score(row(case,w))
                scores[name]={
                    "score":s["score"],
                    "objective_weights":s["objective_weights"],
                    "components":s["components"],
                }
            inc=scores["incumbent_expert_prior"]["score"]
            changes={
                name:("POSITIVE" if payload["score"]>0 else "NEGATIVE" if payload["score"]<0 else "ZERO")
                for name,payload in scores.items()
            }
            if any(v!=changes["incumbent_expert_prior"] for k,v in changes.items() if k!="incumbent_expert_prior"):
                sign_changes.append({
                    "competitive_strength":strength,
                    "case_id":case["id"],
                    "signs":changes,
                    "incumbent_score":inc,
                })
            case_rows.append({
                "case_id":case["id"],
                "inputs":case,
                "scores":scores,
                "score_delta_vs_incumbent":{
                    name:round(payload["score"]-inc,2)
                    for name,payload in scores.items() if name!="incumbent_expert_prior"
                },
            })
        rows.append({
            "competitive_strength":strength,
            "incumbent_weights":normalize(incumbent),
            "baselines":candidates,
            "cases":case_rows,
        })

    report={
        "model_version":"FSFFL-State-Weight-Shadow-Research-1.0",
        "authority":"SHADOW_RESEARCH_NON_AUTHORITATIVE",
        "production_behavior_changed":False,
        "incumbent_status":cal.get("status"),
        "candidate_selection_performed":False,
        "empirical_validation_claim":False,
        "ground_truth_used":False,
        "hurts_so_good_used_as_target":False,
        "baselines":{
            "neutral_equal_four":"constant 25% per channel",
            "neutral_current_future_only":"constant 50/50 current/future with liquidity/resilience disabled",
            "incumbent_current_future_ablation":"incumbent current/future ratio renormalized after removing liquidity/resilience",
        },
        "synthetic_cases_are_structural_sensitivity_not_empirical_outcomes":True,
        "grid":rows,
        "sign_change_count":len(sign_changes),
        "sign_changes":sign_changes,
        "interpretation":{
            "sign_changes_indicate_material_weight_sensitivity":True,
            "sign_changes_do_not_prove_any_baseline_is_better":True,
            "liquidity_resilience_identifiability_requires_independent_outcome_evidence":True,
        },
    }
    OUT.parent.mkdir(parents=True,exist_ok=True)
    OUT.write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({
        "strength_points":len(strength_grid),
        "synthetic_cases":len(CASES),
        "sign_change_count":len(sign_changes),
        "candidate_selection_performed":False,
    },indent=2))

if __name__=="__main__": main()
