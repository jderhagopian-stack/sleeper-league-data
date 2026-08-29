#!/usr/bin/env python3
"""Historical external benchmark for opening-role V2 across QB/RB/WR."""
from __future__ import annotations
import argparse,json
from pathlib import Path
from benchmark_native_vs_external_raw_stats import compare
from native_projection_challenger import RidgeModel,choose_alpha_temporally
from run_native_projection_core_context_benchmark import AGE,DURABILITY,enrich,fetch_players
from run_native_projection_nflverse_benchmark import FEATURES as BASE_FEATURES,POSITIONS,TARGETS,fetch_csv,make_lagged_rows,normalize_season
from run_native_projection_opening_role_by_position_benchmark import attach
from run_native_vs_fftoday_historical_benchmark import LAYOUT,NATIVE_TARGET,eligible_inventory,fetch_fftoday,norm_name
ROLE=["opening_role_available","opening_is_first_team","opening_depth_rank"]
SELECTED={"QB":list(DURABILITY["QB"])+ROLE,"RB":ROLE,"WR":list(AGE["WR"])+ROLE,"TE":list(AGE["TE"])}

def native_predictions(rows,target_season,position):
    train=[r for r in rows if r["position"]==position and int(r["season"])<target_season]; test=[r for r in rows if r["position"]==position and int(r["season"])==target_season]
    features=list(BASE_FEATURES[position])+list(SELECTED[position]); out={}; allowed=set(TARGETS[position])
    for stat,target in NATIVE_TARGET.items():
        if target not in allowed: continue
        alpha,_=choose_alpha_temporally(train,features,target); m=RidgeModel(alpha).fit([[float(r[f]) for f in features] for r in train],[float(r[target]) for r in train])
        for r,p in zip(test,m.predict([[float(r[f]) for f in features] for r in test])): out[(norm_name(r["player_name"]),stat)]=float(p)
    return out

def run(inventory_path,start_season=2016):
    inv=eligible_inventory(inventory_path); max_season=max(int(x["season"]) for x in inv); source=[]
    for s in range(start_season,max_season+1): source.extend(normalize_season(fetch_csv(s),s))
    lagged=enrich(make_lagged_rows(source),source,fetch_players()); rows,role_coverage=attach(lagged,sorted({int(r["season"]) for r in lagged}))
    native,external,actual={},{},{}; coverage=[]
    for item in inv:
        season,pos=int(item["season"]),item["position"]; fft=fetch_fftoday(season,pos,item["snapshot_date"]); nproj=native_predictions(rows,season,pos)
        arows=[r for r in rows if int(r["season"])==season and r["position"]==pos]; ai={norm_name(r["player_name"]):r for r in arows}; ei={norm_name(r["player_name"]):r for r in fft}; common=sorted(set(ai)&set(ei)); stats=sorted(set(NATIVE_TARGET)&set(dict(LAYOUT[pos]))&{t.removeprefix("next_") for t in TARGETS[pos]}); matched=0
        for name in common:
            ar,er=ai[name],ei[name]
            for stat in stats:
                nk=(name,stat); target=NATIVE_TARGET[stat]
                if nk not in nproj or target not in ar or stat not in er: continue
                key=(season,pos,name,stat); native[key]=nproj[nk]; external[key]=float(er[stat]); actual[key]=float(ar[target]); matched+=1
        coverage.append({"season":season,"position":pos,"common_players":len(common),"common_stat_rows":matched,"snapshot_date":item["snapshot_date"]})
    result=compare(native,external,actual); sums={"by_position":{},"by_season":{}}
    for key,d in result["detail"].items():
        ss,pos,_=key.split("|")
        for bucket,name in ((sums["by_position"],pos),(sums["by_season"],ss)):
            x=bucket.setdefault(name,{"native_wins":0,"external_wins":0,"ties":0,"vals":[]}); x[d["winner"]+"_wins" if d["winner"] in ("native","external") else "ties"]+=1; x["vals"].append(float(d["native_improvement_vs_external_pct"]))
    for bucket in sums.values():
        for x in bucket.values():
            vals=sorted(x.pop("vals")); x["group_count"]=len(vals); x["mean_native_improvement_vs_external_pct"]=sum(vals)/len(vals); m=len(vals)//2; x["median_native_improvement_vs_external_pct"]=vals[m] if len(vals)%2 else (vals[m-1]+vals[m])/2
    result.update({"experiment":"opening_role_v2_qb_rb_wr_vs_fftoday","normalized_group_summary":sums,"coverage":coverage,"opening_role_coverage":role_coverage,"native_model":{"selected_features":SELECTED},"governance":{"target_season_realized_stats_used":False,"historical_role_provenance":"PROVISIONAL_WEEK1_ADMINISTRATIVE_RECORD_NOT_TIMESTAMPED","commercial_dependency_approved":False,"production_promoted":False,"blend_promoted":False},"limitations":["Week-1 administrative role archive lacks pre-kickoff timestamp proof and must be replaced/verified before commercial production.","Common cohort excludes rookies/no-history players.","Only one independent external projection source is verified."]})
    return result

def main():
    p=argparse.ArgumentParser(); p.add_argument("--inventory",type=Path,default=Path("data/model_validation/historical_projection_source_inventory.json")); p.add_argument("--start-season",type=int,default=2016); p.add_argument("--output",type=Path,default=Path("data/model_validation/native_opening_role_v2_vs_fftoday_scorecard.json")); a=p.parse_args(); d=run(a.inventory,a.start_season); a.output.parent.mkdir(parents=True,exist_ok=True); a.output.write_text(json.dumps(d,indent=2,sort_keys=True)+"\n",encoding="utf-8"); print(json.dumps({"status":"PASS","group_wins":d["group_wins"],"normalized_group_summary":d["normalized_group_summary"]},indent=2))
if __name__=="__main__": main()
