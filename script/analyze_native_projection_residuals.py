#!/usr/bin/env python3
"""Diagnose where FSFFL Native differs from an external preseason projection source.

Input CSV columns:
  season,position,player_name,stat,native_projection,external_projection,actual

Optional diagnostic columns are preserved as strata, for example:
  opening_depth_rank, opening_is_first_team, opening_team_changed,
  role_bucket, age_bucket, rookie_flag, prior_games_bucket

This is a post-benchmark diagnostic. It does not change production projections,
learn source weights, or train FSFFL Native on external projections.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path

REQUIRED={
    "season","position","player_name","stat",
    "native_projection","external_projection","actual",
}


def fnum(v):
    try:
        return float(v)
    except (TypeError,ValueError):
        return None


def mean(vals):
    vals=list(vals)
    return sum(vals)/len(vals) if vals else None


def median(vals):
    vals=list(vals)
    return statistics.median(vals) if vals else None


def pearson(xs,ys):
    pairs=[(float(x),float(y)) for x,y in zip(xs,ys)]
    if len(pairs)<3:
        return None
    xb=mean(x for x,_ in pairs); yb=mean(y for _,y in pairs)
    num=sum((x-xb)*(y-yb) for x,y in pairs)
    dx=math.sqrt(sum((x-xb)**2 for x,_ in pairs))
    dy=math.sqrt(sum((y-yb)**2 for _,y in pairs))
    if dx==0 or dy==0:
        return None
    return num/(dx*dy)


def load_rows(path: Path):
    with path.open(newline="",encoding="utf-8-sig") as fh:
        reader=csv.DictReader(fh)
        missing=REQUIRED-set(reader.fieldnames or [])
        if missing:
            raise SystemExit(f"missing required columns: {sorted(missing)}")
        optional=[c for c in (reader.fieldnames or []) if c not in REQUIRED]
        out=[]
        for raw in reader:
            n=fnum(raw.get("native_projection"))
            e=fnum(raw.get("external_projection"))
            a=fnum(raw.get("actual"))
            try:
                season=int(raw.get("season",""))
            except (TypeError,ValueError):
                continue
            if n is None or e is None or a is None:
                continue
            out.append({
                "season":season,
                "position":str(raw.get("position") or "").upper().strip(),
                "player_name":str(raw.get("player_name") or "").strip(),
                "stat":str(raw.get("stat") or "").strip(),
                "native_projection":n,
                "external_projection":e,
                "actual":a,
                "strata":{k:str(raw.get(k) or "").strip() for k in optional},
            })
        return out,optional


def summarize(rows):
    if not rows:
        return None
    nerr=[r["native_projection"]-r["actual"] for r in rows]
    eerr=[r["external_projection"]-r["actual"] for r in rows]
    disagreement=[r["native_projection"]-r["external_projection"] for r in rows]
    native_abs=[abs(x) for x in nerr]; external_abs=[abs(x) for x in eerr]
    wins_native=sum(na<ea for na,ea in zip(native_abs,external_abs))
    wins_external=sum(ea<na for na,ea in zip(native_abs,external_abs))
    ties=len(rows)-wins_native-wins_external
    return {
        "n":len(rows),
        "native_mae":mean(native_abs),
        "external_mae":mean(external_abs),
        "native_median_absolute_error":median(native_abs),
        "external_median_absolute_error":median(external_abs),
        "native_bias":mean(nerr),
        "external_bias":mean(eerr),
        "native_player_wins":wins_native,
        "external_player_wins":wins_external,
        "ties":ties,
        "native_improvement_vs_external_pct":(
            100.0*(mean(external_abs)-mean(native_abs))/mean(external_abs)
            if mean(external_abs) else 0.0
        ),
        "projection_disagreement_mean_native_minus_external":mean(disagreement),
        "projection_disagreement_abs_mean":mean(abs(x) for x in disagreement),
        "disagreement_vs_native_absolute_error_pearson":pearson(
            [abs(x) for x in disagreement],native_abs
        ),
        "disagreement_vs_external_absolute_error_pearson":pearson(
            [abs(x) for x in disagreement],external_abs
        ),
    }


def grouped(rows,key_fn):
    g=defaultdict(list)
    for row in rows:
        g[key_fn(row)].append(row)
    return {str(k):summarize(v) for k,v in sorted(g.items(),key=lambda x:str(x[0]))}


def diagnostic(rows,strata_fields,min_stratum_rows=20):
    by_category=grouped(rows,lambda r:f"{r['position']}|{r['stat']}")
    by_season_category=grouped(rows,lambda r:f"{r['season']}|{r['position']}|{r['stat']}")
    strata={}
    for field in strata_fields:
        groups=defaultdict(list)
        for row in rows:
            value=row["strata"].get(field,"")
            if value:
                groups[(field,value)].append(row)
        kept={
            f"{field}={value}":summarize(gr)
            for (field,value),gr in sorted(groups.items())
            if len(gr)>=min_stratum_rows
        }
        if kept:
            strata[field]=kept

    priorities=[]
    for category,summary in by_category.items():
        if not summary: continue
        gap=-float(summary["native_improvement_vs_external_pct"])
        priorities.append({
            "category":category,
            "n":summary["n"],
            "external_advantage_pct":gap,
            "native_bias":summary["native_bias"],
            "external_bias":summary["external_bias"],
            "priority_score":gap*math.sqrt(max(1,summary["n"])),
        })
    priorities.sort(key=lambda x:x["priority_score"],reverse=True)

    return {
        "schema_version":"1.0",
        "status":"PASS",
        "production_behavior_changed":False,
        "overall":summarize(rows),
        "by_position":grouped(rows,lambda r:r["position"]),
        "by_category":by_category,
        "by_season_category":by_season_category,
        "strata":strata,
        "native_improvement_priorities":priorities,
        "governance":{
            "external_projection_used_as_native_training_target":False,
            "target_season_actuals_used_for_feature_generation":False,
            "diagnostic_is_posthoc":True,
            "purpose":"Identify stable Native residual weaknesses and candidate preseason-known feature families for later leakage-safe testing.",
            "promotion_authority":False,
        },
    }


def self_test():
    rows=[]
    for season in (2022,2023,2024):
        for i in range(30):
            actual=3000+i*10
            rows.append({
                "season":season,"position":"QB","player_name":f"q{i}","stat":"passing_yards",
                "native_projection":actual+200,"external_projection":actual+50,"actual":actual,
                "strata":{"opening_is_first_team":"1" if i<20 else "0"},
            })
            actual_rb=180+i
            rows.append({
                "season":season,"position":"RB","player_name":f"r{i}","stat":"carries",
                "native_projection":actual_rb+5,"external_projection":actual_rb+20,"actual":actual_rb,
                "strata":{"opening_is_first_team":"1" if i<15 else "0"},
            })
    out=diagnostic(rows,["opening_is_first_team"],min_stratum_rows=20)
    assert out["by_category"]["QB|passing_yards"]["external_mae"] < out["by_category"]["QB|passing_yards"]["native_mae"]
    assert out["by_category"]["RB|carries"]["native_mae"] < out["by_category"]["RB|carries"]["external_mae"]
    assert out["native_improvement_priorities"][0]["category"]=="QB|passing_yards"
    assert out["production_behavior_changed"] is False
    print("native projection residual diagnostic self-test: PASS")


def main():
    p=argparse.ArgumentParser()
    p.add_argument("input_csv",nargs="?")
    p.add_argument("--output",default="data/model_validation/native_projection_residual_diagnostic.json")
    p.add_argument("--strata",default="")
    p.add_argument("--min-stratum-rows",type=int,default=20)
    p.add_argument("--self-test",action="store_true")
    a=p.parse_args()
    if a.self_test:
        self_test(); return
    if not a.input_csv:
        p.error("input_csv required unless --self-test")
    rows,optional=load_rows(Path(a.input_csv))
    requested=[x.strip() for x in a.strata.split(",") if x.strip()]
    unknown=[x for x in requested if x not in optional]
    if unknown:
        p.error(f"unknown strata columns: {unknown}; available optional columns: {optional}")
    result=diagnostic(rows,requested,min_stratum_rows=a.min_stratum_rows)
    target=Path(a.output); target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(result,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print(json.dumps({"status":"PASS","rows":len(rows),"output":str(target)},indent=2))


if __name__=="__main__":
    main()
