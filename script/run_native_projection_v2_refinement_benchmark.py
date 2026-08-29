#!/usr/bin/env python3
"""Targeted V2 refinement tests after opening-role validation.

Tests only two high-value questions:
1) QB: does pre-opening team continuity/change plus role-volume interactions close
   additional error beyond the accepted opening-role bundle?
2) TE: does the same opening-role signal that materially helped QB/RB/WR improve
   rolling holdouts enough to justify inclusion despite limited external history?

No target-season game statistics are used as features.
"""
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

DEPTH_URL = "https://github.com/nflverse/nflverse-data/releases/download/depth_charts/depth_charts_{season}.csv"
ROLE = ["opening_role_available", "opening_is_first_team", "opening_depth_rank"]
QB_BASE = list(DURABILITY["QB"]) + ROLE
TE_BASE = list(AGE["TE"])


def rank_num(v):
    try:
        x = float(v)
        return x if x > 0 else 9.0
    except (TypeError, ValueError):
        return 9.0


def fetch_depth(season: int) -> list[dict]:
    req = urllib.request.Request(DEPTH_URL.format(season=season), headers={"User-Agent":"FSFFL-v2-refinement/1.0"})
    with urllib.request.urlopen(req, timeout=90) as r:
        return list(csv.DictReader(io.StringIO(r.read().decode("utf-8-sig"))))


def opening_roles(season: int) -> dict[tuple[str,str],dict]:
    out = {}
    for r in fetch_depth(season):
        if str(r.get("game_type") or "").upper() != "REG" or str(r.get("week") or "").strip() != "1":
            continue
        pid = str(r.get("gsis_id") or "").strip()
        pos = str(r.get("position") or "").upper().strip()
        if not pid or pos not in {"QB","TE"}:
            continue
        rank = rank_num(r.get("depth_team"))
        key = (pid,pos)
        prior = out.get(key)
        if prior is None or rank < prior["rank"]:
            out[key] = {"rank":rank, "team":str(r.get("club_code") or "").strip()}
    return out


def attach(rows: list[dict], seasons: list[int]) -> list[dict]:
    maps = {s: opening_roles(s) for s in seasons}
    out = []
    for raw in rows:
        r = dict(raw)
        pos = r["position"]
        role = maps.get(int(r["season"]),{}).get((str(r["player_id"]),pos)) if pos in {"QB","TE"} else None
        rank = float(role["rank"]) if role else 9.0
        r["opening_role_available"] = int(role is not None)
        r["opening_is_first_team"] = int(bool(role and rank == 1.0))
        r["opening_depth_rank"] = min(rank,4.0) if role else 4.0
        opening_team = str(role["team"]) if role else ""
        prior_team = str(r.get("feature_team") or "")
        # feature_team is attached below from the immediately prior completed season.
        r["opening_team_known"] = int(bool(opening_team))
        r["opening_team_changed"] = int(bool(opening_team and prior_team and opening_team != prior_team))
        r["qb1_x_lag1_attempts"] = float(r.get("lag1_attempts",0.0)) * float(r["opening_is_first_team"])
        r["qb1_x_lag1_pass_yards"] = float(r.get("lag1_passing_yards",0.0)) * float(r["opening_is_first_team"])
        r["qb1_x_lag1_rush_yards"] = float(r.get("lag1_rushing_yards",0.0)) * float(r["opening_is_first_team"])
        out.append(r)
    return out


def add_feature_team(lagged: list[dict], season_rows: list[dict]) -> list[dict]:
    idx={(int(r["season"]),str(r["player_id"])):str(r.get("team") or "") for r in season_rows}
    out=[]
    for raw in lagged:
        r=dict(raw)
        r["feature_team"] = idx.get((int(r["feature_season"]),str(r["player_id"])),"")
        out.append(r)
    return out


def evaluate(rows, pos, extra, holdouts):
    by={}
    for h in holdouts:
        rep=temporal_holdout([r for r in rows if int(r["season"])<=h],pos,list(BASE_FEATURES[pos])+list(extra),TARGETS[pos])
        vals=list(rep["targets"].values())
        by[str(h)]={"mean_improvement_vs_persistence_pct":sum(float(v.get("improvement_vs_persistence_pct",0)) for v in vals)/len(vals),"targets_beating_persistence":sum(bool(v.get("beats_persistence")) for v in vals),"targets":rep["targets"]}
    return {"mean_improvement_vs_persistence_pct":sum(x["mean_improvement_vs_persistence_pct"] for x in by.values())/len(by),"mean_targets_beating_persistence":sum(x["targets_beating_persistence"] for x in by.values())/len(by),"by_season":by}


def compare(base, cur):
    deltas={s:cur["by_season"][s]["mean_improvement_vs_persistence_pct"]-base["by_season"][s]["mean_improvement_vs_persistence_pct"] for s in base["by_season"]}
    delta=cur["mean_improvement_vs_persistence_pct"]-base["mean_improvement_vs_persistence_pct"]
    return {"delta_vs_base_pp":delta,"seasons_improved":sum(v>0 for v in deltas.values()),"seasons_tested":len(deltas),"season_deltas_pp":deltas,"passes_restrained_gate":bool(delta>=0.5 and sum(v>0 for v in deltas.values())>=max(2,len(deltas)-1))}


def run(start_season=2016,end_season=2024,first_holdout=2021):
    source=[]
    for s in range(start_season,end_season+1): source.extend(normalize_season(fetch_csv(s),s))
    lagged=add_feature_team(enrich(make_lagged_rows(source),source,fetch_players()),source)
    seasons=sorted({int(r["season"]) for r in lagged}); rows=attach(lagged,seasons); holdouts=[s for s in seasons if first_holdout<=s<=end_season]

    qb_base=evaluate(rows,"QB",QB_BASE,holdouts)
    qb_candidates={
        "team_change":QB_BASE+["opening_team_known","opening_team_changed"],
        "role_volume_interactions":QB_BASE+["qb1_x_lag1_attempts","qb1_x_lag1_pass_yards","qb1_x_lag1_rush_yards"],
        "team_change_plus_interactions":QB_BASE+["opening_team_known","opening_team_changed","qb1_x_lag1_attempts","qb1_x_lag1_pass_yards","qb1_x_lag1_rush_yards"],
    }
    qb={"base":qb_base,"candidates":{},"selection":{}}
    for name,features in qb_candidates.items():
        cur=evaluate(rows,"QB",features,holdouts); qb["candidates"][name]=cur; qb["selection"][name]=compare(qb_base,cur)

    te_base=evaluate(rows,"TE",TE_BASE,holdouts); te_role=evaluate(rows,"TE",TE_BASE+ROLE,holdouts)
    te={"base":te_base,"role":te_role,"selection":compare(te_base,te_role)}

    passing=[(n,x["delta_vs_base_pp"]) for n,x in qb["selection"].items() if x["passes_restrained_gate"]]
    qb_selected=max(passing,key=lambda x:x[1])[0] if passing else None
    return {"schema_version":"1.0","status":"PASS","holdouts":holdouts,"qb":{"selected_candidate":qb_selected,**qb},"te":te,"governance":{"target_season_realized_stats_used":False,"team_change_definition":"Opening-week administrative team vs immediately prior completed-season team; no target-season stats.","historical_role_provenance":"PROVISIONAL_WEEK1_ADMINISTRATIVE_RECORD_NOT_TIMESTAMPED","production_promoted":False}}


def main():
    p=argparse.ArgumentParser(); p.add_argument("--start-season",type=int,default=2016); p.add_argument("--end-season",type=int,default=2024); p.add_argument("--first-holdout",type=int,default=2021); p.add_argument("--output",type=Path,default=Path("data/model_validation/native_projection_v2_refinement_benchmark.json")); a=p.parse_args()
    d=run(a.start_season,a.end_season,a.first_holdout); a.output.parent.mkdir(parents=True,exist_ok=True); a.output.write_text(json.dumps(d,indent=2,sort_keys=True)+"\n",encoding="utf-8"); print(json.dumps({"status":d["status"],"qb_selected":d["qb"]["selected_candidate"],"qb_selection":d["qb"]["selection"],"te_selection":d["te"]["selection"]},indent=2))

if __name__=="__main__": main()
