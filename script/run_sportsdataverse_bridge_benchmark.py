#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, time, urllib.error
from pathlib import Path
from collections import defaultdict

import run_native_vs_fftoday_historical_benchmark as fftsrc
from run_native_projection_core_context_benchmark import enrich, fetch_players
from run_native_projection_nflverse_benchmark import TARGETS, fetch_csv, make_lagged_rows, normalize_season
from run_native_projection_opening_role_by_position_benchmark import attach
from run_native_opening_role_all_vs_fftoday_historical_benchmark import native_predictions
from run_native_vs_fftoday_historical_benchmark import LAYOUT, NATIVE_TARGET, eligible_inventory, norm_name

import sportsdataverse.nfl.nfl_projection as sdv_projection
from sportsdataverse.nfl.nfl_projection import nfl_player_projection
from sportsdataverse.nfl.nfl_availability import nfl_availability_projection

# sportsdataverse 0.0.75 aging_curve groups a null-age bucket and then attempts
# float(None). Null ages cannot inform an aging transition, so exclude them
# only from curve fitting. The projection engine's own fallback age still
# applies to players whose last_age is missing.
_sdv_aging_curve = sdv_projection.aging_curve
def _aging_curve_without_null_age(season_rates, **kwargs):
    if "age" in season_rates.columns:
        season_rates = season_rates.filter(season_rates["age"].is_not_null())
    return _sdv_aging_curve(season_rates, **kwargs)
sdv_projection.aging_curve = _aging_curve_without_null_age

_last=[0.0]
_orig=fftsrc.fetch_html

def throttled(url):
    for attempt in range(6):
        elapsed=time.monotonic()-_last[0]
        if elapsed<1.25: time.sleep(1.25-elapsed)
        try:
            out=_orig(url); _last[0]=time.monotonic(); return out
        except urllib.error.HTTPError as e:
            _last[0]=time.monotonic()
            if e.code not in {403,429} or attempt==5: raise
            time.sleep(4*(attempt+1))
fftsrc.fetch_html=throttled

def med(vals):
    vals=sorted(vals)
    if not vals: return None
    m=len(vals)//2
    return vals[m] if len(vals)%2 else (vals[m-1]+vals[m])/2

def score(records, left, right):
    groups=defaultdict(list)
    for r in records: groups[(r["position"],r["stat"])].append(r)
    lw=rw=ties=0; rels=[]; detail={}
    for (pos,stat),grp in sorted(groups.items()):
        l=sum(abs(x[left]-x["actual"]) for x in grp)/len(grp)
        rr=sum(abs(x[right]-x["actual"]) for x in grp)/len(grp)
        if abs(l-rr)<1e-12: winner="tie"; ties+=1
        elif l<rr: winner=left; lw+=1
        else: winner=right; rw+=1
        rel=100*(rr-l)/rr if rr else 0.0
        rels.append(rel)
        detail[f"2024|{pos}|{stat}"]={"n":len(grp),f"{left}_mae":l,f"{right}_mae":rr,
            f"{left}_improvement_vs_{right}_pct":rel,"winner":winner}
    return {"group_wins":{left:lw,right:rw,"ties":ties},"group_count":len(groups),
            f"mean_{left}_improvement_vs_{right}_pct":sum(rels)/len(rels) if rels else None,
            f"median_{left}_improvement_vs_{right}_pct":med(rels),"detail":detail}

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--inventory",type=Path,default=Path("data/model_validation/historical_projection_source_inventory.json"))
    p.add_argument("--output",type=Path,default=Path("data/model_validation/sportsdataverse_bridge_2024_benchmark.json"))
    a=p.parse_args()

    inv=[x for x in eligible_inventory(a.inventory) if int(x["season"])==2024]
    if not inv: raise RuntimeError("No eligible 2024 FFToday snapshots")
    source=[]
    for s in range(2016,2025): source.extend(normalize_season(fetch_csv(s),s))
    lagged=enrich(make_lagged_rows(source),source,fetch_players())
    rows,_=attach(lagged,sorted({int(r["season"]) for r in lagged}))

    hist=[2020,2021,2022,2023]
    sdv=nfl_player_projection(hist,2024)
    av=nfl_availability_projection(hist,2024)
    sdv=sdv.join(av.select("player_id","proj_games"),on="player_id",how="left",suffix="_avail")
    sdv_map={str(r["player_id"]):r for r in sdv.to_dicts()}

    records=[]; coverage=[]
    for item in inv:
        pos=item["position"]
        fft=fftsrc.fetch_fftoday(2024,pos,item["snapshot_date"])
        fi={norm_name(r["player_name"]):r for r in fft}
        nproj=native_predictions(rows,2024,pos)
        arows=[r for r in rows if int(r["season"])==2024 and r["position"]==pos]
        ai={norm_name(r["player_name"]):r for r in arows}
        stats=sorted(set(NATIVE_TARGET)&set(dict(LAYOUT[pos]))&{t.removeprefix("next_") for t in TARGETS[pos]})
        player_set=set()
        for name,ar in ai.items():
            if name not in fi: continue
            pid=str(ar["player_id"]); sr=sdv_map.get(pid)
            if sr is None: continue
            for stat in stats:
                nk=(name,stat); target=NATIVE_TARGET[stat]; rate_key=f"proj_{stat}_rate"
                if nk not in nproj or stat not in fi[name] or rate_key not in sr or sr[rate_key] is None: continue
                games=sr.get("proj_games_avail") if sr.get("proj_games_avail") is not None else sr.get("proj_games")
                if games is None: continue
                records.append({"position":pos,"stat":stat,"player_id":pid,"player_name":ar["player_name"],
                    "native":float(nproj[nk]),"fftoday":float(fi[name][stat]),"sportsdataverse":float(sr[rate_key])*float(games),
                    "actual":float(ar[target])})
                player_set.add(pid)
        coverage.append({"position":pos,"three_way_players":len(player_set),
                         "three_way_stat_rows":sum(1 for r in records if r["position"]==pos)})

    if not records: raise RuntimeError("No three-way benchmark records")
    result={
      "schema_version":"1.0","status":"PASS","experiment":"sportsdataverse_bridge_clean_2024_three_way_raw_stat_benchmark",
      "season":2024,"player_seasons":len({r["player_id"] for r in records}),"common_stat_rows":len(records),
      "comparisons":{
        "sportsdataverse_vs_fftoday":score(records,"sportsdataverse","fftoday"),
        "sportsdataverse_vs_native_v2":score(records,"sportsdataverse","native"),
        "native_v2_vs_fftoday_same_cohort":score(records,"native","fftoday")
      },
      "coverage":coverage,
      "method":{
        "sportsdataverse_version_source":"sportsdataverse-py pinned Git commit in workflow",
        "history_seasons":hist,
        "season_totals":"projected per-game raw-stat rate multiplied by separately projected availability games",
        "comparison_population":"exact 2024 players/stats projected by SportsDataverse, Native V2, and eligible preseason FFToday",
        "fantasy_points_used":False
      },
      "governance":{
        "clean_holdout":True,
        "why_2024_only":"SportsDataverse projection/availability constants were fitted on 2022-2023 folds; its source documents 2024 as the untouched holdout. Earlier seasons would not be valid with current fitted constants.",
        "production_promoted":False,
        "commercial_dependency_approved":False,
        "sportsdataverse_0_0_75_null_age_workaround":True
      },
      "limitations":[
        "This is one clean external holdout season, not a multi-season independent validation.",
        "The three-way cohort excludes players not projected by all three systems.",
        "SportsDataverse software is MIT-licensed, but underlying NFL data rights must be cleared separately before commercial dependency approval."
      ]
    }
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(result,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print(json.dumps({k:v for k,v in result.items() if k in ["status","season","player_seasons","common_stat_rows","comparisons","coverage"]},indent=2))

if __name__=="__main__": main()
