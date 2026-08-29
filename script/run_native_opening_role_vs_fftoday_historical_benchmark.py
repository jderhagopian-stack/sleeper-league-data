#!/usr/bin/env python3
"""Re-run the historical FFToday benchmark with the validated provisional QB opening-role bundle."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from benchmark_native_vs_external_raw_stats import compare
from native_projection_challenger import RidgeModel, choose_alpha_temporally
from run_native_projection_core_context_benchmark import AGE, DURABILITY, enrich, fetch_players
from run_native_projection_nflverse_benchmark import FEATURES as BASE_FEATURES, POSITIONS, TARGETS, fetch_csv, make_lagged_rows, normalize_season
from run_native_projection_opening_role_benchmark import attach_opening_context
from run_native_vs_fftoday_historical_benchmark import (
    LAYOUT, NATIVE_TARGET, eligible_inventory, fetch_fftoday, norm_name,
)

SELECTED = {
    "QB": list(DURABILITY["QB"]) + ["opening_role_available", "opening_is_qb1", "opening_depth_rank"],
    "RB": [],
    "WR": list(AGE["WR"]),
    "TE": list(AGE["TE"]),
}


def native_predictions(rows: list[dict], target_season: int, position: str) -> dict:
    train=[r for r in rows if r["position"]==position and int(r["season"])<target_season]
    test=[r for r in rows if r["position"]==position and int(r["season"])==target_season]
    if not train or not test:
        raise ValueError(f"{target_season} {position}: empty native train/test")
    features=list(BASE_FEATURES[position])+list(SELECTED[position])
    out={}
    allowed=set(TARGETS[position])
    for stat,target in NATIVE_TARGET.items():
        if target not in allowed:
            continue
        alpha,_=choose_alpha_temporally(train,features,target)
        model=RidgeModel(alpha).fit([[float(r[f]) for f in features] for r in train],[float(r[target]) for r in train])
        preds=model.predict([[float(r[f]) for f in features] for r in test])
        for r,pred in zip(test,preds):
            out[(norm_name(r["player_name"]),stat)]=float(pred)
    return out


def run(inventory_path: Path,start_season:int=2016) -> dict:
    inv=eligible_inventory(inventory_path)
    if not inv:
        raise ValueError("no eligible FFToday snapshots")
    max_season=max(int(r["season"]) for r in inv)
    season_rows=[]
    for season in range(start_season,max_season+1):
        season_rows.extend(normalize_season(fetch_csv(season),season))
    lagged=enrich(make_lagged_rows(season_rows),season_rows,fetch_players())
    target_seasons=sorted({int(r["season"]) for r in lagged})
    rows,role_coverage=attach_opening_context(lagged,target_seasons)

    native,external,actual={},{},{}
    coverage=[]
    for item in inv:
        season,pos=int(item["season"]),item["position"]
        fft=fetch_fftoday(season,pos,item["snapshot_date"])
        nproj=native_predictions(rows,season,pos)
        actual_rows=[r for r in rows if int(r["season"])==season and r["position"]==pos]
        actual_index={norm_name(r["player_name"]):r for r in actual_rows}
        fft_index={norm_name(r["player_name"]):r for r in fft}
        common_players=sorted(set(actual_index)&set(fft_index))
        common_stats=sorted(set(NATIVE_TARGET)&set(dict(LAYOUT[pos]).keys())&{t.removeprefix("next_") for t in TARGETS[pos]})
        matched=0
        for name in common_players:
            ar,er=actual_index[name],fft_index[name]
            for stat in common_stats:
                nk=(name,stat); target=NATIVE_TARGET[stat]
                if nk not in nproj or target not in ar or stat not in er:
                    continue
                key=(season,pos,name,stat)
                native[key]=nproj[nk]; external[key]=float(er[stat]); actual[key]=float(ar[target]); matched+=1
        coverage.append({"season":season,"position":pos,"common_players":len(common_players),"common_stat_rows":matched,"snapshot_date":item["snapshot_date"]})

    result=compare(native,external,actual)
    summaries={"by_position":{},"by_season":{}}
    for key,d in result["detail"].items():
        season_s,pos,_=key.split("|")
        for bucket,name in ((summaries["by_position"],pos),(summaries["by_season"],season_s)):
            x=bucket.setdefault(name,{"native_wins":0,"external_wins":0,"ties":0,"relative_improvements_pct":[]})
            if d["winner"]=="native": x["native_wins"]+=1
            elif d["winner"]=="external": x["external_wins"]+=1
            else: x["ties"]+=1
            x["relative_improvements_pct"].append(float(d["native_improvement_vs_external_pct"]))
    for bucket in summaries.values():
        for x in bucket.values():
            vals=sorted(x.pop("relative_improvements_pct")); x["group_count"]=len(vals)
            x["mean_native_improvement_vs_external_pct"]=sum(vals)/len(vals)
            m=len(vals)//2; x["median_native_improvement_vs_external_pct"]=vals[m] if len(vals)%2 else (vals[m-1]+vals[m])/2
    result.update({
        "experiment":"selected_native_plus_provisional_qb_opening_role_vs_fftoday",
        "normalized_group_summary":summaries,
        "coverage":coverage,
        "opening_role_coverage":role_coverage,
        "native_model":{"selected_features":SELECTED,"training_rule":"strictly seasons before target; target-season Week-1 administrative role only for QB"},
        "governance":{
            "target_season_realized_stats_used":False,
            "historical_role_provenance":"PROVISIONAL_WEEK1_ADMINISTRATIVE_RECORD_NOT_TIMESTAMPED",
            "commercial_dependency_approved":False,
            "production_promoted":False,
            "blend_promoted":False,
        },
        "limitations":[
            "The QB role archive is Week-1 administrative depth-chart data but is not timestamp-frozen before kickoff; treat this result as a strong signal requiring a commercially safe timestamped replacement before production.",
            "Common cohort excludes players not forecastable by both systems, including rookies absent from the veteran-history path.",
            "Only one independent external projection source is presently verified.",
        ],
    })
    return result


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--inventory",type=Path,default=Path("data/model_validation/historical_projection_source_inventory.json"))
    p.add_argument("--start-season",type=int,default=2016)
    p.add_argument("--output",type=Path,default=Path("data/model_validation/native_opening_role_vs_fftoday_scorecard.json"))
    a=p.parse_args(); result=run(a.inventory,a.start_season)
    a.output.parent.mkdir(parents=True,exist_ok=True); a.output.write_text(json.dumps(result,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print(json.dumps({"status":"PASS","group_wins":result["group_wins"],"normalized_group_summary":result["normalized_group_summary"]},indent=2))

if __name__=="__main__":
    main()
