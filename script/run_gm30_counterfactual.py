#!/usr/bin/env python3
"""
GM 3.0 exact counterfactual trade simulator.

Applies a two-team player trade to an in-memory copy of current FSFFL rosters,
calls the SAME Simulator 1.0 preproduction simulation function, and stores the
comparison under data/gm3/counterfactuals/.

It never writes into data/simulator/.

Example:
  python script/run_gm30_counterfactual.py \
      --give 6794,8150 --get 9221 --simulations 20000 --label "Gibbs idea"
"""
from __future__ import annotations
import argparse, copy, json, re, sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR=Path(__file__).resolve().parent
sys.path.insert(0,str(SCRIPT_DIR))
import build_fsffl_season_simulator as core
import run_fsffl_season_simulator_preproduction as pre

DATA=Path("data")
OUT=DATA/"gm3"/"counterfactuals"
USER_ID="846634401482792960"

def load(p,default=None):
    p=Path(p)
    if not p.exists(): return default
    with p.open("r",encoding="utf-8") as f:return json.load(f)

def ids(s):
    return [x.strip() for x in str(s or "").split(",") if x.strip()]

def roster_by_owner(rosters,uid):
    return next((r for r in rosters if str(r.get("owner_id"))==str(uid)),None)

def owner_of_player(rosters,pid):
    for r in rosters:
        if str(pid) in {str(x) for x in (r.get("players") or [])}:
            return str(r.get("owner_id"))
    return None

def remove_everywhere(roster,pid):
    for key in ("players","starters","taxi","reserve"):
        if isinstance(roster.get(key),list):
            roster[key]=[x for x in roster[key] if str(x)!=str(pid)]

def ensure_player(roster,pid):
    roster.setdefault("players",[])
    if str(pid) not in {str(x) for x in roster["players"]}:
        roster["players"].append(str(pid))

def teamrow(rows,uid):
    return next((x for x in rows if str(x.get("user_id"))==str(uid)),{})

def delta(after,before,key):
    try:return round(float(after.get(key,0))-float(before.get(key,0)),5)
    except:return None

def safe_label(s):
    s=re.sub(r"[^A-Za-z0-9._-]+","_",s or "trade").strip("_")
    return s[:80] or "trade"

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--give",required=True,help="Comma-separated player IDs sent by Hurts So Good")
    ap.add_argument("--get",required=True,help="Comma-separated player IDs received by Hurts So Good")
    ap.add_argument("--simulations",type=int,default=20000)
    ap.add_argument("--label",default="trade")
    args=ap.parse_args()
    if args.simulations<1000: raise SystemExit("--simulations must be >= 1000")

    give,get=ids(args.give),ids(args.get)
    league=load(DATA/"league.json"); rosters=load(DATA/"rosters.json",[]); users=load(DATA/"users.json",[])
    players=load(DATA/"players.json",{})
    if not league: raise SystemExit("Missing data/league.json")
    season=str(league["season"])
    schedule=load(DATA/"stats"/"fsffl"/season/"league_matchups_raw.json",{})
    projections=load(DATA/"simulator"/season/"inputs"/"player_weekly_projections.json")
    baseline=load(DATA/"simulator"/season/"outputs"/"standings_projection.json",{})
    if not projections: raise SystemExit("Missing Simulator 1.0 projection input")

    user_roster=roster_by_owner(rosters,USER_ID)
    if not user_roster: raise SystemExit("Hurts So Good roster not found")

    for pid in give:
        if owner_of_player(rosters,pid)!=USER_ID:
            raise SystemExit(f"Cannot give {pid}: not currently rostered by Hurts So Good")
    counterparties={owner_of_player(rosters,pid) for pid in get}
    if None in counterparties: raise SystemExit("One or more incoming players are unrostered; use waiver/FA evaluation instead")
    if len(counterparties)!=1: raise SystemExit("All incoming players must come from one counterparty for a two-team trade")
    counter_uid=next(iter(counterparties))
    if counter_uid==USER_ID: raise SystemExit("Incoming players are already on Hurts So Good")

    cf=copy.deepcopy(rosters)
    u=roster_by_owner(cf,USER_ID); c=roster_by_owner(cf,counter_uid)
    if not c: raise SystemExit("Counterparty roster not found")
    for pid in give:
        remove_everywhere(u,pid); ensure_player(c,pid)
    for pid in get:
        remove_everywhere(c,pid); ensure_player(u,pid)

    result=pre.run_preproduction_simulation(
        league=league,rosters=cf,users=users,players=players,raw_schedule=schedule,
        projections=projections,n_sims=args.simulations
    )

    before_rows=baseline.get("teams",[])
    after_rows=result.get("teams",[])
    comparison=[]
    for uid in (USER_ID,counter_uid):
        b=teamrow(before_rows,uid); a=teamrow(after_rows,uid)
        comparison.append({
            "user_id":uid,"manager":a.get("manager") or b.get("manager"),"team_name":a.get("team_name") or b.get("team_name"),
            "baseline":{"expected_wins":b.get("expected_wins"),"expected_points_for":b.get("expected_points_for"),
                        "playoff_probability":b.get("playoff_probability"),"bye_probability":b.get("bye_probability"),
                        "championship_probability":b.get("championship_probability")},
            "counterfactual":{"expected_wins":a.get("expected_wins"),"expected_points_for":a.get("expected_points_for"),
                            "playoff_probability":a.get("playoff_probability"),"bye_probability":a.get("bye_probability"),
                            "championship_probability":a.get("championship_probability")},
            "delta":{"expected_wins":delta(a,b,"expected_wins"),"expected_points_for":delta(a,b,"expected_points_for"),
                     "playoff_probability":delta(a,b,"playoff_probability"),"bye_probability":delta(a,b,"bye_probability"),
                     "championship_probability":delta(a,b,"championship_probability")}
        })

    now=datetime.now(timezone.utc).isoformat()
    payload={
        "generated_at_utc":now,"model_version":"FSFFL-GM-3.0","simulator_engine":"Simulator 1.0 preproduction in-memory",
        "label":args.label,"simulations":args.simulations,
        "trade":{"user_user_id":USER_ID,"counterparty_user_id":counter_uid,"give_player_ids":give,"get_player_ids":get},
        "comparison":comparison,
        "integrity":{"simulator_files_mutated":False,"output_scope":"data/gm3/counterfactuals only"},
        "note":"Baseline and counterfactual may have Monte Carlo sampling noise; increase --simulations for close calls."
    }
    OUT.mkdir(parents=True,exist_ok=True)
    stamp=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path=OUT/f"{stamp}_{safe_label(args.label)}.json"
    with path.open("w",encoding="utf-8") as f:json.dump(payload,f,indent=2)
    print(json.dumps(payload,indent=2))
    print(f"Saved: {path}")

if __name__=="__main__": main()
