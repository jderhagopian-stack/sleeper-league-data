#!/usr/bin/env python3
"""Test whether single-season raw-stat winners remain stable under long-run position priors.

This is a research diagnostic only. It does not learn production weights and does
not convert position-level fantasy-point accuracy into raw-stat authority.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

WEIGHTS=(0.25,0.50,0.75)


def relative_scores(values: dict[str,float]) -> dict[str,float]:
    best=min(values.values())
    return {k:v/best for k,v in values.items()}


def evaluate(scorecard:dict, priors:dict) -> dict:
    out={}
    for category,row in scorecard["categories"].items():
        position=category.split("|",1)[0]
        category_mae={k:float(v) for k,v in row["mae_by_source"].items()}
        prior_all=priors["positions"][position]["historical_mae"]
        common=sorted(set(category_mae)&set(prior_all))
        if len(common)<2:
            out[category]={"status":"INSUFFICIENT_PRIOR_OVERLAP"}
            continue
        cat_rel=relative_scores({s:category_mae[s] for s in common})
        prior_rel=relative_scores({s:float(prior_all[s]) for s in common})
        winners={}
        scores={}
        for w in WEIGHTS:
            blended={s:(1-w)*cat_rel[s]+w*prior_rel[s] for s in common}
            winner=min(blended,key=blended.get)
            winners[str(w)]=winner
            scores[str(w)]=blended
        stable=len(set(winners.values()))==1
        raw_winner=row["winner_in_2014_cross_section"]
        if raw_winner=="equal_weight":
            recommendation="EQUAL_WEIGHT_STRUCTURALLY_SUPPORTED"
        elif stable:
            recommendation="STABLE_SINGLE_SOURCE_PRIOR"
        else:
            recommendation="UNSTABLE_NEEDS_MORE_RAW_STAT_SEASONS"
        out[category]={
            "status":"PASS",
            "common_players":row["common_players"],
            "raw_2014_winner":raw_winner,
            "individual_source_winner_by_position_prior_weight":winners,
            "stable_individual_winner":next(iter(winners.values())) if stable else None,
            "stable_across_sensitivity":stable,
            "recommendation":recommendation,
            "normalized_score_by_prior_weight":scores,
        }
    return {
        "schema_version":"1.0",
        "status":"RESEARCH_ONLY",
        "production_behavior_changed":False,
        "prior_weight_sensitivity":list(WEIGHTS),
        "categories":out,
        "summary":{
            "equal_weight_structurally_supported":sum(v.get("recommendation")=="EQUAL_WEIGHT_STRUCTURALLY_SUPPORTED" for v in out.values()),
            "stable_single_source_prior":sum(v.get("recommendation")=="STABLE_SINGLE_SOURCE_PRIOR" for v in out.values()),
            "unstable_needs_more_raw_stat_seasons":sum(v.get("recommendation")=="UNSTABLE_NEEDS_MORE_RAW_STAT_SEASONS" for v in out.values()),
        },
        "governance":{
            "position_prior_is_stabilizer_only":True,
            "raw_stat_holdout_remains_primary":True,
            "single_shrinkage_coefficient_selected":False,
            "production_promotion_authority":False,
        },
    }


def self_test():
    score={"categories":{"RB|carries":{"mae_by_source":{"A":10,"B":11},"winner_in_2014_cross_section":"A","common_players":30},"WR|yards":{"mae_by_source":{"A":10,"B":9},"winner_in_2014_cross_section":"equal_weight","common_players":30}}}
    pri={"positions":{"RB":{"historical_mae":{"A":20,"B":30}},"WR":{"historical_mae":{"A":20,"B":19}}}}
    result=evaluate(score,pri)
    assert result["categories"]["RB|carries"]["stable_across_sensitivity"] is True
    assert result["categories"]["WR|yards"]["recommendation"]=="EQUAL_WEIGHT_STRUCTURALLY_SUPPORTED"
    print("hierarchical projection stability self-test: PASS")


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--scorecard",type=Path,default=Path("data/model_validation/projection_2014_multisource_raw_stat_scorecard.json"))
    p.add_argument("--priors",type=Path,default=Path("data/model_validation/projection_position_accuracy_priors_2014_2025.json"))
    p.add_argument("--output",type=Path,default=Path("data/model_validation/projection_championship_hierarchical_stability.json"))
    p.add_argument("--self-test",action="store_true")
    a=p.parse_args()
    if a.self_test:
        self_test(); return
    result=evaluate(json.loads(a.scorecard.read_text()),json.loads(a.priors.read_text()))
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(result,indent=2,sort_keys=True)+"\n")
    print(json.dumps(result["summary"],indent=2,sort_keys=True))

if __name__=="__main__":
    main()
