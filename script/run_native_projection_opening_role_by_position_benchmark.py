#!/usr/bin/env python3
"""Test restrained opening-week administrative role features by fantasy position."""
from __future__ import annotations

import argparse
import csv
import io
import json
import urllib.request
from pathlib import Path

from native_projection_challenger import temporal_holdout
from run_native_projection_core_context_benchmark import AGE, DURABILITY, enrich, fetch_players
from run_native_projection_nflverse_benchmark import FEATURES as BASE_FEATURES, TARGETS, fetch_csv, make_lagged_rows, normalize_season

DEPTH_URL="https://github.com/nflverse/nflverse-data/releases/download/depth_charts/depth_charts_{season}.csv"
POSITIONS=("QB","RB","WR")
BASE_EXTRA={"QB":list(DURABILITY["QB"]),"RB":[],"WR":list(AGE["WR"])}
BUNDLES={
    "presence":["opening_role_available"],
    "first_team":["opening_role_available","opening_is_first_team"],
    "depth_rank":["opening_role_available","opening_is_first_team","opening_depth_rank"],
}


def rank_num(v):
    try:
        x=float(v)
        return x if x>0 else 9.0
    except (TypeError,ValueError):
        return 9.0


def fetch_depth(season:int)->list[dict]:
    req=urllib.request.Request(DEPTH_URL.format(season=season),headers={"User-Agent":"FSFFL-opening-role-by-position/1.0"})
    with urllib.request.urlopen(req,timeout=90) as r:
        return list(csv.DictReader(io.StringIO(r.read().decode("utf-8-sig"))))


def opening_roles(season:int)->dict[tuple[str,str],dict]:
    out={}
    for r in fetch_depth(season):
        if str(r.get("game_type") or "").upper()!="REG" or str(r.get("week") or "").strip()!="1":
            continue
        pid=str(r.get("gsis_id") or "").strip(); pos=str(r.get("position") or "").upper().strip()
        if not pid or pos not in POSITIONS:
            continue
        rank=rank_num(r.get("depth_team")); key=(pid,pos); prior=out.get(key)
        if prior is None or rank<prior["rank"]:
            out[key]={"rank":rank,"team":str(r.get("club_code") or "").strip()}
    return out


def attach(rows:list[dict],seasons:list[int]):
    maps={s:opening_roles(s) for s in seasons}; coverage={}
    for s,m in maps.items():
        coverage[str(s)]={p:{"listed":sum(1 for (_,q) in m if q==p),"first_team":sum(1 for (_,q),x in m.items() if q==p and x["rank"]==1)} for p in POSITIONS}
    out=[]
    for raw in rows:
        r=dict(raw); pos=r["position"]; role=maps.get(int(r["season"]),{}).get((str(r["player_id"]),pos)) if pos in POSITIONS else None
        rank=float(role["rank"]) if role else 9.0
        r["opening_role_available"]=int(role is not None)
        r["opening_is_first_team"]=int(bool(role and rank==1.0))
        r["opening_depth_rank"]=min(rank,4.0) if role else 4.0
        out.append(r)
    return out,coverage


def evaluate(rows,position,extra,holdouts):
    by={}
    for h in holdouts:
        eligible=[r for r in rows if int(r["season"])<=h]
        rep=temporal_holdout(eligible,position,list(BASE_FEATURES[position])+list(extra),TARGETS[position])
        vals=list(rep["targets"].values())
        by[str(h)]={"mean_improvement_vs_persistence_pct":sum(float(v.get("improvement_vs_persistence_pct",0)) for v in vals)/len(vals),"targets_beating_persistence":sum(bool(v.get("beats_persistence")) for v in vals),"targets":rep["targets"]}
    return {"mean_improvement_vs_persistence_pct":sum(x["mean_improvement_vs_persistence_pct"] for x in by.values())/len(by),"mean_targets_beating_persistence":sum(x["targets_beating_persistence"] for x in by.values())/len(by),"by_season":by}


def run(start_season:int,end_season:int,first_holdout:int):
    if end_season>2024: raise ValueError("historical-schema role experiment capped at 2024")
    source=[]
    for s in range(start_season,end_season+1): source.extend(normalize_season(fetch_csv(s),s))
    lagged=enrich(make_lagged_rows(source),source,fetch_players()); seasons=sorted({int(r["season"]) for r in lagged}); rows,coverage=attach(lagged,seasons)
    holdouts=[s for s in seasons if first_holdout<=s<=end_season]
    results={}; selections={}
    for pos in POSITIONS:
        base=evaluate(rows,pos,BASE_EXTRA[pos],holdouts); variants={"selected_v1":base}; sel={}
        for name,bundle in BUNDLES.items():
            cur=evaluate(rows,pos,BASE_EXTRA[pos]+bundle,holdouts); variants[name]=cur
            deltas={s:cur["by_season"][str(s)]["mean_improvement_vs_persistence_pct"]-base["by_season"][str(s)]["mean_improvement_vs_persistence_pct"] for s in holdouts}
            delta=cur["mean_improvement_vs_persistence_pct"]-base["mean_improvement_vs_persistence_pct"]
            sel[name]={"delta_vs_selected_v1_pp":delta,"seasons_improved":sum(v>0 for v in deltas.values()),"seasons_tested":len(deltas),"season_deltas_pp":{str(k):v for k,v in deltas.items()},"passes_restrained_gate":bool(delta>=0.5 and sum(v>0 for v in deltas.values())>=max(2,len(deltas)-1))}
        passing=[(n,x["delta_vs_selected_v1_pp"]) for n,x in sel.items() if x["passes_restrained_gate"]]
        selections[pos]={"selected":max(passing,key=lambda x:x[1])[0] if passing else None,"candidates":sel}
        results[pos]=variants
    return {"schema_version":"1.0","status":"PASS","experiment":"provisional_opening_week_role_by_position","holdouts":holdouts,"coverage":coverage,"results":results,"selection":selections,"governance":{"target_season_realized_stats_used":False,"freeze_provenance":"PROVISIONAL_WEEK1_ADMINISTRATIVE_RECORD_NOT_TIMESTAMPED","commercial_dependency_approved":False,"production_promoted":False,"retention_gate":"At least +0.5 pp mean and improves all but at most one holdout."}}


def main():
    p=argparse.ArgumentParser(); p.add_argument("--start-season",type=int,default=2016); p.add_argument("--end-season",type=int,default=2024); p.add_argument("--first-holdout",type=int,default=2021); p.add_argument("--output",type=Path,default=Path("data/model_validation/native_projection_opening_role_by_position_benchmark.json")); a=p.parse_args()
    d=run(a.start_season,a.end_season,a.first_holdout); a.output.parent.mkdir(parents=True,exist_ok=True); a.output.write_text(json.dumps(d,indent=2,sort_keys=True)+"\n",encoding="utf-8"); print(json.dumps({"status":d["status"],"selection":d["selection"]},indent=2))

if __name__=="__main__": main()
