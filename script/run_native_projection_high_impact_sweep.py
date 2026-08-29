#!/usr/bin/env python3
"""Final high-impact feature sweep on top of the deployable Native V2 model.

Tests only preseason-safe information families with plausible material value:
1) position-specific development/career-stage shape;
2) lagged opportunity-share/usage concentration from the prior completed season.

No target-season realized statistics are used. Candidates must improve rolling
2021-24 temporal holdouts by at least 0.5 percentage points on average and in
all but at most one holdout to be retained.
"""
from __future__ import annotations

import json
from pathlib import Path

from native_projection_challenger import temporal_holdout
from run_native_projection_core_context_benchmark import AGE, DURABILITY, enrich, fetch_players, fval
from run_native_projection_nflverse_benchmark import FEATURES as BASE_FEATURES, TARGETS, fetch_csv, make_lagged_rows, normalize_season
from run_native_projection_v2_refinement_benchmark import ROLE, add_feature_team, attach

QB_REFINEMENT=["opening_team_known","opening_team_changed","qb1_x_lag1_attempts","qb1_x_lag1_pass_yards","qb1_x_lag1_rush_yards"]
PROD_EXTRA={
    "QB":list(DURABILITY["QB"])+ROLE+QB_REFINEMENT,
    "RB":ROLE,
    "WR":list(AGE["WR"])+ROLE,
    "TE":list(AGE["TE"])+ROLE,
}

DEV=["career_year_sq","career_year_1","career_year_2","career_year_3","career_year_4plus"]
AGE_DEV=["age_available","target_age","target_age_sq"]+DEV
SHARES={
    "QB":["lag1_team_attempt_share","lag1_team_rush_share"],
    "RB":["lag1_team_carry_share","lag1_team_target_share"],
    "WR":["lag1_team_target_share"],
    "TE":["lag1_team_target_share"],
}


def add_high_impact_features(rows, source):
    team={}
    for r in source:
        key=(int(r["season"]),str(r.get("team") or ""))
        if not key[1]: continue
        t=team.setdefault(key,{"attempts":0.0,"carries":0.0,"targets":0.0})
        t["attempts"]+=fval(r.get("attempts")); t["carries"]+=fval(r.get("carries")); t["targets"]+=fval(r.get("targets"))
    out=[]
    for raw in rows:
        r=dict(raw)
        cy=max(0,int(round(fval(r.get("career_year")))))
        r["career_year_sq"]=float(cy*cy)
        r["career_year_1"]=int(cy==1); r["career_year_2"]=int(cy==2); r["career_year_3"]=int(cy==3); r["career_year_4plus"]=int(cy>=4)
        t=team.get((int(r["feature_season"]),str(r.get("feature_team") or "")),{})
        att=max(1.0,fval(t.get("attempts"))); car=max(1.0,fval(t.get("carries"))); tar=max(1.0,fval(t.get("targets")))
        r["lag1_team_attempt_share"]=fval(r.get("lag1_attempts"))/att
        r["lag1_team_rush_share"]=fval(r.get("lag1_carries"))/car
        r["lag1_team_carry_share"]=fval(r.get("lag1_carries"))/car
        r["lag1_team_target_share"]=fval(r.get("lag1_targets"))/tar
        out.append(r)
    return out


def evaluate(rows,pos,extra,holdouts):
    by={}
    for h in holdouts:
        rep=temporal_holdout([r for r in rows if int(r["season"])<=h],pos,list(BASE_FEATURES[pos])+list(extra),TARGETS[pos])
        vals=list(rep["targets"].values())
        by[str(h)]={"mean_improvement_vs_persistence_pct":sum(float(v.get("improvement_vs_persistence_pct",0)) for v in vals)/len(vals),"targets_beating_persistence":sum(bool(v.get("beats_persistence")) for v in vals)}
    return {"mean_improvement_vs_persistence_pct":sum(x["mean_improvement_vs_persistence_pct"] for x in by.values())/len(by),"by_season":by}


def compare(base,cur):
    deltas={s:cur["by_season"][s]["mean_improvement_vs_persistence_pct"]-base["by_season"][s]["mean_improvement_vs_persistence_pct"] for s in base["by_season"]}
    delta=cur["mean_improvement_vs_persistence_pct"]-base["mean_improvement_vs_persistence_pct"]
    improved=sum(v>0 for v in deltas.values())
    return {"delta_vs_production_pp":delta,"seasons_improved":improved,"season_deltas_pp":deltas,"passes_gate":bool(delta>=0.5 and improved>=max(2,len(deltas)-1))}


def main():
    source=[]
    for s in range(2016,2025): source.extend(normalize_season(fetch_csv(s),s))
    rows=add_feature_team(enrich(make_lagged_rows(source),source,fetch_players()),source)
    seasons=sorted({int(r["season"]) for r in rows}); rows=attach(rows,seasons); rows=add_high_impact_features(rows,source)
    holdouts=[2021,2022,2023,2024]
    result={"schema_version":"1.0","status":"PASS","holdouts":holdouts,"positions":{},"governance":{"target_season_realized_stats_used":False,"retention_gate":"At least +0.5 pp mean improvement vs deployable V2 and improves at least 3 of 4 holdouts."}}
    for pos in ("QB","RB","WR","TE"):
        base=evaluate(rows,pos,PROD_EXTRA[pos],holdouts)
        candidates={
            "development":PROD_EXTRA[pos]+AGE_DEV,
            "opportunity_share":PROD_EXTRA[pos]+SHARES[pos],
            "development_plus_opportunity":PROD_EXTRA[pos]+AGE_DEV+SHARES[pos],
        }
        rec={"production_v2":base,"candidates":{},"selection":{}}
        for name,features in candidates.items():
            cur=evaluate(rows,pos,features,holdouts); rec["candidates"][name]=cur; rec["selection"][name]=compare(base,cur)
        passing=[(n,x["delta_vs_production_pp"]) for n,x in rec["selection"].items() if x["passes_gate"]]
        rec["selected"]=max(passing,key=lambda x:x[1])[0] if passing else None
        result["positions"][pos]=rec
    out=Path("data/model_validation/native_projection_high_impact_sweep.json"); out.parent.mkdir(parents=True,exist_ok=True); out.write_text(json.dumps(result,indent=2,sort_keys=True)+"\n")
    print(json.dumps({p:{"selected":r["selected"],"selection":r["selection"]} for p,r in result["positions"].items()},indent=2))

if __name__=="__main__": main()
