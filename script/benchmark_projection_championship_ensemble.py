#!/usr/bin/env python3
"""Leakage-safe position/stat projection championship and ensemble benchmark.

Input CSV columns:
  season,position,player_name,stat,source,projection,actual

For each position/stat category and each eligible holdout season, the benchmark:
1. constructs an exact common player cohort across all compared sources,
2. uses only earlier seasons to estimate source MAE,
3. evaluates equal-weight, training-champion, and shrunk inverse-MAE blends,
4. scores all methods on the held-out season,
5. reports category-level winners without changing production projections.

External projections are never used as FSFFL Native training targets here.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import re
import statistics
import unicodedata
from collections import defaultdict
from pathlib import Path

REQUIRED={"season","position","player_name","stat","source","projection","actual"}


def norm_name(value: str) -> str:
    value=unicodedata.normalize("NFKD",value or "")
    value="".join(ch for ch in value if not unicodedata.combining(ch)).lower()
    value=re.sub(r"\b(jr|sr|ii|iii|iv|v)\b\.?","",value)
    return re.sub(r"[^a-z0-9]+","",value)


def fnum(value):
    try: return float(value)
    except (TypeError,ValueError): return None


def mean(values):
    values=list(values)
    return sum(values)/len(values) if values else None


def mae(errors):
    return mean(abs(x) for x in errors)


def load_rows(path: Path):
    with path.open(newline="",encoding="utf-8-sig") as fh:
        reader=csv.DictReader(fh)
        missing=REQUIRED-set(reader.fieldnames or [])
        if missing:
            raise SystemExit(f"missing required columns: {sorted(missing)}")
        out=[]
        seen=set()
        for raw in reader:
            try: season=int(raw["season"])
            except (TypeError,ValueError): continue
            projection=fnum(raw.get("projection")); actual=fnum(raw.get("actual"))
            if projection is None or actual is None: continue
            row={
                "season":season,
                "position":str(raw.get("position") or "").upper().strip(),
                "player_name":str(raw.get("player_name") or "").strip(),
                "player_key":norm_name(raw.get("player_name") or ""),
                "stat":str(raw.get("stat") or "").strip(),
                "source":str(raw.get("source") or "").strip(),
                "projection":projection,
                "actual":actual,
            }
            key=(row["season"],row["position"],row["player_key"],row["stat"],row["source"])
            if key in seen:
                raise SystemExit(f"duplicate forecast row: {key}")
            seen.add(key); out.append(row)
        return out


def category_rows(rows):
    groups=defaultdict(list)
    for row in rows:
        groups[(row["position"],row["stat"])].append(row)
    return groups


def common_cohort(rows, sources, seasons=None):
    sources=tuple(sorted(sources))
    grouped=defaultdict(dict)
    for row in rows:
        if seasons is not None and row["season"] not in seasons: continue
        key=(row["season"],row["player_key"])
        grouped[key][row["source"]]=row
    out=[]
    for (season,player_key),by_source in grouped.items():
        if not all(source in by_source for source in sources): continue
        actuals={round(by_source[source]["actual"],8) for source in sources}
        if len(actuals)!=1:
            raise SystemExit(f"actual mismatch for {season}/{player_key}")
        out.append({
            "season":season,
            "player_key":player_key,
            "actual":next(iter(by_source.values()))["actual"],
            "projections":{source:by_source[source]["projection"] for source in sources},
        })
    return out


def source_mae(common_rows, source):
    return mae(row["projections"][source]-row["actual"] for row in common_rows)


def equal_weights(sources):
    n=len(sources)
    return {source:1.0/n for source in sources}


def inverse_mae_weights(training_rows,sources,shrinkage=.5):
    errors={}
    for source in sources:
        value=source_mae(training_rows,source)
        if value is None: return None
        errors[source]=max(value,1e-9)
    inv={source:1.0/value for source,value in errors.items()}
    denom=sum(inv.values())
    raw={source:value/denom for source,value in inv.items()}
    equal=1.0/len(sources)
    return {
        source:(1.0-shrinkage)*raw[source]+shrinkage*equal
        for source in sources
    }


def blend_mae(common_rows,weights):
    errors=[]
    for row in common_rows:
        pred=sum(weights[source]*row["projections"][source] for source in weights)
        errors.append(pred-row["actual"])
    return mae(errors)


def evaluate_category(rows,*,min_train_seasons=2,min_rows_per_source=20,shrinkage=.5):
    sources=sorted({row["source"] for row in rows})
    seasons=sorted({row["season"] for row in rows})
    if len(sources)<2:
        return {"status":"INSUFFICIENT_SOURCES","sources":sources,"seasons":seasons,"holdouts":[]}

    holdouts=[]
    for holdout in seasons:
        train_seasons=[season for season in seasons if season<holdout]
        if len(train_seasons)<min_train_seasons: continue
        training=common_cohort(rows,sources,set(train_seasons))
        test=common_cohort(rows,sources,{holdout})
        if len(training)<min_rows_per_source or not test: continue

        train_source_mae={source:source_mae(training,source) for source in sources}
        champion=min(sources,key=lambda source:train_source_mae[source])
        eq=equal_weights(sources)
        inv=inverse_mae_weights(training,sources,shrinkage=shrinkage)
        methods={
            "equal_weight":blend_mae(test,eq),
            "training_champion":source_mae(test,champion),
        }
        if inv:
            methods["shrunk_inverse_mae"]=blend_mae(test,inv)

        source_holdout={source:source_mae(test,source) for source in sources}
        winner=min(methods,key=methods.get)
        best_source=min(source_holdout,key=source_holdout.get)
        holdouts.append({
            "train_seasons":train_seasons,
            "holdout_season":holdout,
            "training_common_rows":len(training),
            "holdout_common_rows":len(test),
            "training_source_mae":train_source_mae,
            "training_champion":champion,
            "shrunk_inverse_mae_weights":inv,
            "holdout_source_mae":source_holdout,
            "holdout_method_mae":methods,
            "method_winner":winner,
            "best_source_on_holdout":best_source,
        })

    if not holdouts:
        return {"status":"INSUFFICIENT_TEMPORAL_EVIDENCE","sources":sources,"seasons":seasons,"holdouts":[]}

    method_scores=defaultdict(list)
    source_scores=defaultdict(list)
    for h in holdouts:
        for method,value in h["holdout_method_mae"].items(): method_scores[method].append(value)
        for source,value in h["holdout_source_mae"].items(): source_scores[source].append(value)

    method_mean={key:mean(vals) for key,vals in method_scores.items()}
    source_mean={key:mean(vals) for key,vals in source_scores.items()}
    champion_method=min(method_mean,key=method_mean.get)
    champion_source=min(source_mean,key=source_mean.get)

    return {
        "status":"PASS",
        "sources":sources,
        "seasons":seasons,
        "holdouts":holdouts,
        "summary":{
            "holdout_count":len(holdouts),
            "mean_holdout_mae_by_method":method_mean,
            "mean_holdout_mae_by_source":source_mean,
            "champion_method":champion_method,
            "champion_source":champion_source,
            "ensemble_beats_best_single_source":method_mean.get(champion_method,math.inf)<source_mean.get(champion_source,math.inf),
        },
    }


def benchmark(rows,*,min_train_seasons=2,min_rows_per_source=20,shrinkage=.5):
    categories={}
    for (position,stat),group in sorted(category_rows(rows).items()):
        categories[f"{position}|{stat}"]=evaluate_category(
            group,
            min_train_seasons=min_train_seasons,
            min_rows_per_source=min_rows_per_source,
            shrinkage=shrinkage,
        )
    passed=[value for value in categories.values() if value.get("status")=="PASS"]
    method_wins=defaultdict(int); source_wins=defaultdict(int)
    for value in passed:
        method_wins[value["summary"]["champion_method"]]+=1
        source_wins[value["summary"]["champion_source"]]+=1
    return {
        "schema_version":"1.0",
        "status":"PASS",
        "production_behavior_changed":False,
        "configuration":{
            "minimum_training_seasons":min_train_seasons,
            "minimum_training_rows_per_source":min_rows_per_source,
            "inverse_mae_shrinkage_toward_equal":shrinkage,
        },
        "category_count":len(categories),
        "categories_with_temporal_evidence":len(passed),
        "championship_summary":{
            "category_method_wins":dict(sorted(method_wins.items())),
            "category_source_wins":dict(sorted(source_wins.items())),
        },
        "categories":categories,
        "governance":{
            "raw_stats_before_fantasy_scoring":True,
            "common_cohort_required":True,
            "future_seasons_used_to_learn_weights":False,
            "external_projection_used_as_native_training_target":False,
            "promotion_requires_separate_governed_decision":True,
        },
    }


def self_test():
    rows=[]
    # A is better for QB passing yards, B is better for RB carries.
    for season in (2021,2022,2023,2024):
        for i in range(30):
            for pos,stat,bias_a,bias_b in [
                ("QB","passing_yards",10,40),
                ("RB","carries",20,5),
            ]:
                actual=100+i+season%10
                rows.append({"season":season,"position":pos,"player_name":f"p{i}","player_key":f"p{i}","stat":stat,"source":"A","projection":actual+bias_a,"actual":actual})
                rows.append({"season":season,"position":pos,"player_name":f"p{i}","player_key":f"p{i}","stat":stat,"source":"B","projection":actual+bias_b,"actual":actual})
    out=benchmark(rows,min_train_seasons=2,min_rows_per_source=20,shrinkage=.5)
    qb=out["categories"]["QB|passing_yards"]; rb=out["categories"]["RB|carries"]
    assert qb["summary"]["champion_source"]=="A"
    assert rb["summary"]["champion_source"]=="B"
    assert qb["holdouts"][0]["train_seasons"]==[2021,2022]
    assert qb["holdouts"][0]["holdout_season"]==2023
    assert out["production_behavior_changed"] is False
    print("projection championship ensemble self-test: PASS")


def main():
    p=argparse.ArgumentParser()
    p.add_argument("input_csv",nargs="?")
    p.add_argument("--output",default="data/model_validation/projection_championship_results.json")
    p.add_argument("--min-train-seasons",type=int,default=2)
    p.add_argument("--min-rows-per-source",type=int,default=20)
    p.add_argument("--shrinkage",type=float,default=.5)
    p.add_argument("--self-test",action="store_true")
    a=p.parse_args()
    if a.self_test:
        self_test(); return
    if not a.input_csv:
        p.error("input_csv required unless --self-test")
    if not 0<=a.shrinkage<=1:
        p.error("--shrinkage must be between 0 and 1")
    result=benchmark(
        load_rows(Path(a.input_csv)),
        min_train_seasons=a.min_train_seasons,
        min_rows_per_source=a.min_rows_per_source,
        shrinkage=a.shrinkage,
    )
    target=Path(a.output); target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(result,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print(json.dumps({"status":"PASS","categories":result["category_count"],"output":str(target)},indent=2))


if __name__=="__main__":
    main()
