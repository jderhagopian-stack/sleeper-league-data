#!/usr/bin/env python3
"""Validate a native no-NFL-history projection path for rookies/new entrants.

Historical examples are players who produce in target season T but have no
regular-season player-stat row in T-1. Features use only opening administrative
role/depth plus age metadata. Target-season football stats are outcomes only.
Historical Week-1 role records remain explicitly provisional on freeze provenance.
"""
from __future__ import annotations

import argparse, json
from datetime import date
from pathlib import Path

from native_projection_challenger import RidgeModel, choose_alpha_temporally, mae
from run_native_projection_core_context_benchmark import fetch_players, fval
from run_native_projection_nflverse_benchmark import TARGETS, fetch_csv, normalize_season
from build_native_preseason_projections import role_map

POSITIONS=("QB","RB","WR","TE")
FEATURES=["opening_role_available","opening_is_first_team","opening_depth_rank","age_available","target_age","target_age_sq"]


def entrant_rows(start_season:int,end_season:int)->list[dict]:
    seasons={}
    for s in range(start_season-1,end_season+1):
        seasons[s]=normalize_season(fetch_csv(s),s)
    players=fetch_players(); out=[]
    for s in range(start_season,end_season+1):
        prior={str(r["player_id"]) for r in seasons[s-1]}
        roles,_=role_map(s,None)
        for cur in seasons[s]:
            pid=str(cur["player_id"]); pos=cur["position"]
            if pos not in POSITIONS or pid in prior:
                continue
            role=roles.get((pid,pos)); rank=float(role["rank"]) if role else 9.0
            meta=players.get(pid,{})
            age=0.0; age_ok=0
            birth=str(meta.get("birth_date") or "")
            if birth:
                try:
                    born=date.fromisoformat(birth[:10]); age=(date(s,9,1)-born).days/365.2425
                    age_ok=int(17<=age<=50)
                    if not age_ok: age=0.0
                except ValueError: pass
            row={
                "season":s,"position":pos,"player_id":pid,"player_name":cur["player_name"],
                "opening_role_available":int(role is not None),
                "opening_is_first_team":int(bool(role and rank==1.0)),
                "opening_depth_rank":min(rank,4.0) if role else 4.0,
                "age_available":age_ok,"target_age":age,"target_age_sq":age*age if age_ok else 0.0,
            }
            for target in TARGETS[pos]:
                stat=target.removeprefix("next_")
                row[target]=fval(cur.get(stat))
            out.append(row)
    return out


def evaluate(rows:list[dict],first_holdout:int,end_season:int)->dict:
    by={}; aggregate={}
    for pos in POSITIONS:
        posrows=[r for r in rows if r["position"]==pos]
        holdouts=[s for s in sorted({int(r["season"]) for r in posrows}) if first_holdout<=s<=end_season]
        pby={}
        for h in holdouts:
            train=[r for r in posrows if int(r["season"])<h]; test=[r for r in posrows if int(r["season"])==h]
            if not train or not test: continue
            target_results={}
            for target in TARGETS[pos]:
                alpha,_=choose_alpha_temporally(train,FEATURES,target)
                model=RidgeModel(alpha).fit([[float(r[f]) for f in FEATURES] for r in train],[float(r[target]) for r in train])
                pred=model.predict([[float(r[f]) for f in FEATURES] for r in test]); actual=[float(r[target]) for r in test]
                model_mae=mae(actual,pred); mean=sum(float(r[target]) for r in train)/len(train); base_mae=mae(actual,[mean]*len(actual))
                target_results[target]={"model_mae":model_mae,"mean_baseline_mae":base_mae,"improvement_vs_mean_pct":100*(base_mae-model_mae)/base_mae if base_mae>1e-12 else 0.0,"beats_mean":model_mae<base_mae}
            vals=list(target_results.values())
            pby[str(h)]={"n":len(test),"mean_improvement_vs_mean_pct":sum(v["improvement_vs_mean_pct"] for v in vals)/len(vals),"targets_beating_mean":sum(v["beats_mean"] for v in vals),"targets":target_results}
        vals=list(pby.values())
        aggregate[pos]={"holdouts":len(vals),"mean_improvement_vs_mean_pct":sum(v["mean_improvement_vs_mean_pct"] for v in vals)/len(vals) if vals else None,"mean_targets_beating_mean":sum(v["targets_beating_mean"] for v in vals)/len(vals) if vals else None,"seasons_improved":sum(v["mean_improvement_vs_mean_pct"]>0 for v in vals),"entrant_n":len(posrows)}
        by[pos]=pby
    return {"by_season":by,"aggregate":aggregate}


def main():
    p=argparse.ArgumentParser(); p.add_argument("--start-season",type=int,default=2017); p.add_argument("--end-season",type=int,default=2024); p.add_argument("--first-holdout",type=int,default=2021); p.add_argument("--output",type=Path,default=Path("data/model_validation/native_no_history_role_benchmark.json")); a=p.parse_args()
    rows=entrant_rows(a.start_season,a.end_season); result=evaluate(rows,a.first_holdout,a.end_season)
    payload={"schema_version":"1.0","status":"PASS","experiment":"native_no_history_role_model","features":FEATURES,"aggregate":result["aggregate"],"by_season":result["by_season"],"governance":{"target_season_stats_used_as_features":False,"historical_role_freeze_provenance":"PROVISIONAL_WEEK1_ADMINISTRATIVE_RECORD_NOT_TIMESTAMPED","external_projection_source_used":False,"production_promoted":False}}
    a.output.parent.mkdir(parents=True,exist_ok=True); a.output.write_text(json.dumps(payload,indent=2,sort_keys=True)+"\n",encoding="utf-8"); print(json.dumps({"status":"PASS","aggregate":payload["aggregate"]},indent=2))

if __name__=="__main__": main()
