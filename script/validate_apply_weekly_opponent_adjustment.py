#!/usr/bin/env python3
"""Validate and apply a preseason-known opponent-strength weekly mean adjustment.

The adjustment uses ONLY the prior season's defense-vs-position fantasy points
allowed. Target-season outcomes are used solely for holdout scoring. Production
2026 factors therefore come from 2025 defensive results plus the known 2026
schedule. Season-level projected means are preserved by normalizing each
player's weekly matchup multipliers to average 1.0 over fantasy weeks 1-17.
"""
from __future__ import annotations
import argparse, csv, io, json, statistics, urllib.request
from collections import defaultdict, Counter
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/"data"
WEEKLY_URL="https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_week_{season}.csv"
SCHEDULE_URL="https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv"
POSITIONS=("QB","RB","WR","TE")
LAMBDAS=(0.0,0.25,0.5,0.75,1.0)
SD_FLOOR={"QB":4.0,"RB":3.5,"WR":3.8,"TE":3.0}
TEAM_ALIAS={"JAC":"JAX","JAX":"JAX","LA":"LAR","LAR":"LAR","SD":"LAC","OAK":"LV","STL":"LAR"}

def f(v):
    try: return float(v or 0.0)
    except (TypeError,ValueError): return 0.0

def team(v):
    x=str(v or "").upper().strip()
    return TEAM_ALIAS.get(x,x)

def fetch_csv(url, ua="FSFFL-weekly-opponent-adjustment/1.0"):
    req=urllib.request.Request(url,headers={"User-Agent":ua})
    with urllib.request.urlopen(req,timeout=90) as r:
        return list(csv.DictReader(io.StringIO(r.read().decode("utf-8-sig"))))

_SCHEDULE_CACHE=None
def schedules():
    global _SCHEDULE_CACHE
    if _SCHEDULE_CACHE is None: _SCHEDULE_CACHE=fetch_csv(SCHEDULE_URL)
    return _SCHEDULE_CACHE

def schedule_map(season:int):
    out={}
    for r in schedules():
        try: s=int(float(r.get("season") or 0)); w=int(float(r.get("week") or 0))
        except (TypeError,ValueError): continue
        if s!=season or str(r.get("game_type") or "").upper()!="REG" or not (1<=w<=18): continue
        h=team(r.get("home_team")); a=team(r.get("away_team"))
        if h and a: out[(h,w)]=a; out[(a,w)]=h
    return out

def score(row, scoring):
    return (
      f(row.get("passing_yards"))*f(scoring.get("pass_yd"))+
      f(row.get("passing_tds"))*f(scoring.get("pass_td"))+
      f(row.get("interceptions"))*f(scoring.get("pass_int"))+
      f(row.get("rushing_yards"))*f(scoring.get("rush_yd"))+
      f(row.get("rushing_tds"))*f(scoring.get("rush_td"))+
      f(row.get("receptions"))*f(scoring.get("rec"))+
      f(row.get("receiving_yards"))*f(scoring.get("rec_yd"))+
      f(row.get("receiving_tds"))*f(scoring.get("rec_td"))+
      f(row.get("fumbles_lost"))*f(scoring.get("fum_lost"))
    )

def weekly_rows(season:int, scoring):
    sm=schedule_map(season); rows=[]
    for r in fetch_csv(WEEKLY_URL.format(season=season)):
        if str(r.get("season_type") or "REG").upper()!="REG": continue
        try: w=int(float(r.get("week") or 0))
        except (TypeError,ValueError): continue
        if not (1<=w<=18): continue
        pos=str(r.get("position_group") or r.get("position") or "").upper().strip()
        if pos not in POSITIONS: continue
        t=team(r.get("recent_team") or r.get("team"))
        opp=sm.get((t,w))
        pid=str(r.get("player_id") or r.get("gsis_id") or "").strip()
        if not pid or not t or not opp: continue
        rows.append({"season":season,"week":w,"player_id":pid,"position":pos,"team":t,
                     "opponent":opp,"points":score(r,scoring)})
    return rows

def defense_factors(source_season:int, scoring):
    rows=weekly_rows(source_season,scoring)
    # Sum all player fantasy points by opposing defense/position/game, then
    # average those game totals across the season.
    game_totals=defaultdict(float)
    for r in rows:
        game_totals[(r["opponent"],r["position"],r["week"],r["team"])]+=r["points"]
    vals=defaultdict(list)
    for (opp,pos,w,off),pts in game_totals.items(): vals[(opp,pos)].append(pts)
    league={}
    for pos in POSITIONS:
        all_games=[x for (d,p),arr in vals.items() if p==pos for x in arr]
        league[pos]=statistics.fmean(all_games) if all_games else 1.0
    factors={}
    for (d,pos),arr in vals.items():
        factors[(d,pos)]=statistics.fmean(arr)/league[pos] if league[pos] else 1.0
    return factors,{"source_season":source_season,
      "defense_position_cells":len(factors),
      "league_mean_points_allowed_per_game_by_position":league}

def stable_player_records(target_season:int, scoring, lam:float):
    prior,_=defense_factors(target_season-1,scoring)
    rows=[r for r in weekly_rows(target_season,scoring) if r["week"]<=17]
    by=defaultdict(list)
    for r in rows: by[r["player_id"]].append(r)
    base_err=adj_err=0.0; n=0; by_pos=defaultdict(lambda:[0.0,0.0,0])
    for pid,pr in by.items():
        if len(pr)<8: continue
        teams=Counter(x["team"] for x in pr)
        if len(teams)!=1: continue
        t=next(iter(teams))
        pos=pr[0]["position"]
        mean=statistics.fmean(x["points"] for x in pr)
        sched=schedule_map(target_season)
        mults=[]
        for w in range(1,18):
            opp=sched.get((t,w))
            if opp:
                raw=prior.get((opp,pos),1.0)
                mults.append(1.0+lam*(raw-1.0))
        norm=statistics.fmean(mults) if mults else 1.0
        if norm<=0: norm=1.0
        for x in pr:
            raw=prior.get((x["opponent"],pos),1.0)
            m=(1.0+lam*(raw-1.0))/norm
            b=abs(mean-x["points"]); a=abs(mean*m-x["points"])
            base_err+=b; adj_err+=a; n+=1
            z=by_pos[pos]; z[0]+=b; z[1]+=a; z[2]+=1
    return {"n":n,"baseline_mae":base_err/n if n else None,"adjusted_mae":adj_err/n if n else None,
      "improvement_pct":100*(base_err-adj_err)/base_err if base_err else 0.0,
      "by_position":{p:{"n":z[2],"baseline_mae":z[0]/z[2],"adjusted_mae":z[1]/z[2],
          "improvement_pct":100*(z[0]-z[1])/z[0] if z[0] else 0.0} for p,z in by_pos.items()}}

def validate(scoring):
    train_seasons=(2022,2023,2024); test_season=2025
    grid={}
    for lam in LAMBDAS:
        reps={str(s):stable_player_records(s,scoring,lam) for s in train_seasons}
        grid[str(lam)]={"mean_train_improvement_pct":statistics.fmean(r["improvement_pct"] for r in reps.values()),
                        "by_season":reps}
    selected={}
    test={}
    for pos in POSITIONS:
        candidates=[]
        for lam in LAMBDAS:
            vals=[grid[str(lam)]["by_season"][str(s)]["by_position"].get(pos,{}).get("improvement_pct",0.0) for s in train_seasons]
            candidates.append((statistics.fmean(vals),lam,vals))
        best=max(candidates,key=lambda x:x[0])
        lam=best[1]
        rep=stable_player_records(test_season,scoring,lam)["by_position"].get(pos,{})
        keep=bool(lam>0 and best[0]>0 and rep.get("improvement_pct",0)>0)
        selected[pos]=lam if keep else 0.0
        test[pos]={"selected_lambda_from_2022_2024":lam,
                   "train_mean_improvement_pct":best[0],
                   "train_season_improvements_pct":best[2],
                   "2025_holdout":rep,
                   "retained":keep,
                   "production_lambda":selected[pos]}
    return {"schema_version":"1.0","status":"PASS",
      "experiment":"prior_season_defense_vs_position_weekly_mean_adjustment",
      "training_holdouts":[2022,2023,2024],"final_out_of_sample_holdout":2025,
      "candidate_lambdas":list(LAMBDAS),"grid":grid,"selection_by_position":test,
      "production_lambdas":selected,
      "governance":{"target_season_results_used_as_features":False,
                    "defense_signal_for_each_holdout":"prior completed season only",
                    "2026_defense_signal_source_season":2025,
                    "season_projection_total_preserved":True}}

def apply_current(season:int, scoring, scorecard=None):
    scorecard=scorecard or validate(scoring)
    lambdas=scorecard["production_lambdas"]
    factors,factor_audit=defense_factors(season-1,scoring)
    root=DATA/"simulator"/str(season)
    path=root/"inputs"/"player_weekly_projections.json"
    out=json.loads(path.read_text())
    sched=schedule_map(season)
    adjusted=0; missing_team=0; missing_opp=0
    examples=[]
    for sid,p in (out.get("players") or {}).items():
        pos=str(p.get("position") or "").upper(); lam=float(lambdas.get(pos,0.0))
        t=team(p.get("team"))
        if not t: missing_team+=1; continue
        active=[]
        for w in range(1,18):
            wk=p["weeks"].get(str(w),{})
            if wk.get("is_bye"): continue
            opp=sched.get((t,w))
            if not opp: continue
            raw=factors.get((opp,pos),1.0)
            active.append((w,opp,1.0+lam*(raw-1.0)))
        if not active:
            missing_opp+=1; continue
        norm=statistics.fmean(x[2] for x in active)
        if norm<=0: norm=1.0
        before=[]; after=[]
        for w,opp,m0 in active:
            wk=p["weeks"][str(w)]
            old=float(wk.get("mean") or 0.0)
            mult=m0/norm
            mean=max(0.0,old*mult)
            cv=float(p.get("volatility_cv") or 0.0)
            sd=max(SD_FLOOR.get(pos,3.0),mean*cv)
            median=max(0.0,mean-0.08*sd)
            wk.update({"mean":round(mean,3),"median":round(median,3),"sd":round(sd,3),
                       "p25":round(max(0.0,mean-0.67448975*sd),3),
                       "p75":round(max(0.0,mean+0.67448975*sd),3),
                       "opponent":opp,"opponent_adjustment_multiplier":round(mult,5),
                       "opponent_adjustment_source_season":season-1})
            before.append(old); after.append(mean)
        p["opponent_adjustment_lambda"]=lam
        p["opponent_adjustment_mean_preservation_error"]=round((statistics.fmean(after)-statistics.fmean(before)) if before else 0.0,8)
        adjusted+=1
        if len(examples)<8 and lam>0:
            ex=sorted([(w,opp,m/norm) for w,opp,m in active],key=lambda x:x[2])
            examples.append({"player":p.get("name"),"position":pos,
                "hardest":{"week":ex[0][0],"opponent":ex[0][1],"multiplier":round(ex[0][2],4)},
                "easiest":{"week":ex[-1][0],"opponent":ex[-1][1],"multiplier":round(ex[-1][2],4)}})
    out["model_stage"]="interim_external_season_means_validated_opponent_adjusted_weekly"
    out["opponent_adjustment"]={"method":"prior-season defense-vs-position points allowed",
        "source_season":season-1,"production_lambdas":lambdas,"season_mean_preserved":True}
    path.write_text(json.dumps(out,indent=2,sort_keys=True)+"\n")
    audit_path=root/"outputs"/"weekly_projection_audit.json"
    aud=json.loads(audit_path.read_text())
    aud["opponent_adjustment"]={"validated":True,"source_season":season-1,
      "production_lambdas":lambdas,"players_adjusted":adjusted,
      "missing_team_players":missing_team,"missing_schedule_players":missing_opp,
      "factor_audit":factor_audit,"examples":examples}
    aud["important_limitations"]=[x for x in aud.get("important_limitations",[]) if "No opponent-specific weekly matchup adjustment yet." not in str(x)]
    aud["important_limitations"].append("Opponent adjustment uses prior-season defense-vs-position strength; it does not yet incorporate current-season defensive injuries/personnel changes or betting-market game environment.")
    audit_path.write_text(json.dumps(aud,indent=2,sort_keys=True)+"\n")
    return {"players_adjusted":adjusted,"missing_team":missing_team,"missing_schedule":missing_opp,"lambdas":lambdas,"examples":examples}

def main():
    p=argparse.ArgumentParser(); p.add_argument("--season",type=int,default=2026)
    p.add_argument("--scorecard",type=Path,default=DATA/"model_validation"/"weekly_opponent_adjustment_scorecard.json")
    p.add_argument("--validate-only",action="store_true"); a=p.parse_args()
    league=json.loads((DATA/"league.json").read_text()); scoring=league.get("scoring_settings") or {}
    card=validate(scoring); a.scorecard.parent.mkdir(parents=True,exist_ok=True)
    a.scorecard.write_text(json.dumps(card,indent=2,sort_keys=True)+"\n")
    result={"status":"PASS","production_lambdas":card["production_lambdas"],
            "selection_by_position":card["selection_by_position"]}
    if not a.validate_only: result["production_apply"]=apply_current(a.season,scoring,card)
    print(json.dumps(result,indent=2))
if __name__=="__main__": main()
