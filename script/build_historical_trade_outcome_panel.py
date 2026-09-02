#!/usr/bin/env python3
"""Build a non-causal historical trade outcome panel from committed FSFFL facts.

The panel attaches completed trade participants to observed team outcomes after
the transaction's Sleeper leg. It is research-only:
- no strategy scalar is created;
- observed post-trade performance is not treated as causal trade impact;
- no counterfactual alternative is invented;
- transaction-leg timing is retained and explicitly qualified.
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
DATA=ROOT/"data"
OUT=DATA/"model_validation"/"historical_trade_outcome_panel.json"
SEASONS=("2022","2023","2024","2025")

def load(path,default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default

def season_num(entry):
    league=entry.get("league") or {}
    return str(league.get("season") or entry.get("season") or "")

def history_index():
    raw=load(DATA/"league_history.json",[])
    return {season_num(x):x for x in raw if isinstance(x,dict) and season_num(x)}

def roster_to_user(entry):
    out={}
    for r in entry.get("rosters") or []:
        rid=r.get("roster_id"); uid=r.get("owner_id")
        if rid is not None and uid is not None:
            out[str(rid)]=str(uid)
    return out

def user_names(entry):
    out={}
    for u in entry.get("users") or []:
        uid=u.get("user_id")
        if uid is None: continue
        meta=u.get("metadata") or {}
        out[str(uid)]=str(meta.get("team_name") or u.get("display_name") or u.get("username") or uid)
    return out

def completed_trades(entry):
    return sorted([
        tx for tx in (entry.get("transactions") or [])
        if str(tx.get("type") or "").lower()=="trade"
        and str(tx.get("status") or "complete").lower() in {"complete","completed"}
    ],key=lambda x:(int(x.get("created") or 0),str(x.get("transaction_id") or "")))

def team_week_outcomes(season,entry):
    raw=load(DATA/"stats"/"fsffl"/season/"league_matchups_raw.json",{})
    r2u=roster_to_user(entry)
    playoff_start=int(((entry.get("league") or {}).get("settings") or {}).get("playoff_week_start") or 15)
    by_user=defaultdict(dict)
    for week_s,records in (raw.items() if isinstance(raw,dict) else []):
        try: week=int(week_s)
        except Exception: continue
        grouped=defaultdict(list)
        for rec in records if isinstance(records,list) else []:
            mid=rec.get("matchup_id")
            if mid is not None:
                grouped[str(mid)].append(rec)
        for pair in grouped.values():
            if len(pair)!=2: continue
            a,b=pair
            arid,brid=str(a.get("roster_id")),str(b.get("roster_id"))
            auid,buid=r2u.get(arid),r2u.get(brid)
            if not auid or not buid: continue
            ap=float(a.get("points") or 0.0); bp=float(b.get("points") or 0.0)
            for uid,pf,pa in ((auid,ap,bp),(buid,bp,ap)):
                by_user[uid][week]={
                    "week":week,
                    "phase":"regular" if week<playoff_start else "postseason",
                    "points_for":round(pf,3),
                    "points_against":round(pa,3),
                    "win":1.0 if pf>pa else 0.5 if pf==pa else 0.0,
                }
    return by_user,playoff_start

def side_assets(tx,rid):
    adds=tx.get("adds") or {}; drops=tx.get("drops") or {}
    received_players=sorted(str(pid) for pid,rr in adds.items() if str(rr)==str(rid))
    sent_players=sorted(str(pid) for pid,rr in drops.items() if str(rr)==str(rid))
    received_picks=[]; sent_picks=[]
    for p in tx.get("draft_picks") or []:
        key=f"pick:{p.get('season')}:R{p.get('round')}:orig{p.get('roster_id')}"
        if str(p.get("owner_id"))==str(rid): received_picks.append(key)
        if str(p.get("previous_owner_id"))==str(rid): sent_picks.append(key)
    return {
        "received_players":received_players,
        "sent_players":sent_players,
        "received_picks":sorted(received_picks),
        "sent_picks":sorted(sent_picks),
    }

def aggregate(weeks,start_week,playoff_start):
    regular=[x for w,x in weeks.items() if w>=start_week and w<playoff_start]
    postseason=[x for w,x in weeks.items() if w>=max(start_week,playoff_start)]
    return {
        "regular_games_observed":len(regular),
        "regular_points_for":round(sum(x["points_for"] for x in regular),3),
        "regular_points_against":round(sum(x["points_against"] for x in regular),3),
        "regular_wins_equivalent":round(sum(x["win"] for x in regular),3),
        "regular_points_per_game":round(sum(x["points_for"] for x in regular)/len(regular),3) if regular else None,
        "postseason_games_observed":len(postseason),
        "postseason_wins_equivalent":round(sum(x["win"] for x in postseason),3),
        "postseason_points_for":round(sum(x["points_for"] for x in postseason),3),
    }

def main():
    history=history_index()
    rows=[]
    diagnostics={
        "missing_season_history":[],
        "trades_without_participant_identity":[],
        "participants_without_post_decision_games":[],
    }
    for season in SEASONS:
        entry=history.get(season)
        if not entry:
            diagnostics["missing_season_history"].append(season); continue
        r2u=roster_to_user(entry); names=user_names(entry)
        outcomes,playoff_start=team_week_outcomes(season,entry)
        for tx in completed_trades(entry):
            tid=str(tx.get("transaction_id") or "")
            leg=max(1,int(tx.get("leg") or 1))
            participant_rids=[str(x) for x in (tx.get("roster_ids") or [])]
            if len(participant_rids)<2:
                diagnostics["trades_without_participant_identity"].append({"season":season,"transaction_id":tid})
                continue
            for rid in participant_rids:
                uid=r2u.get(rid)
                if not uid:
                    diagnostics["trades_without_participant_identity"].append({"season":season,"transaction_id":tid,"roster_id":rid})
                    continue
                observed=aggregate(outcomes.get(uid,{}) ,leg,playoff_start)
                if observed["regular_games_observed"]==0 and observed["postseason_games_observed"]==0:
                    diagnostics["participants_without_post_decision_games"].append({"season":season,"transaction_id":tid,"user_id":uid,"decision_leg":leg})
                rows.append({
                    "season":season,
                    "transaction_id":tid,
                    "decision_created_ms":int(tx.get("created") or 0),
                    "decision_leg":leg,
                    "outcome_window_start_week":leg,
                    "timing_boundary_status":"SLEEPER_LEG_APPROXIMATION_REQUIRES_TIMESTAMP_TO_WEEK_ADJUDICATION",
                    "roster_id":rid,
                    "user_id":uid,
                    "team_name":names.get(uid,uid),
                    "assets":side_assets(tx,rid),
                    "observed_team_outcome_after_leg":observed,
                    "outcome_interpretation":"OBSERVED_POST_DECISION_TEAM_PERFORMANCE_NOT_CAUSAL_TRADE_EFFECT",
                    "counterfactual_alternative_outcome":None,
                    "counterfactual_regret_available":False,
                    "strategy_outcome_score":None,
                    "authoritative_training_row":False,
                })

    by_season=defaultdict(int)
    for row in rows: by_season[row["season"]]+=1
    report={
        "schema_version":"1.0",
        "model_version":"FSFFL-Historical-Trade-Outcome-Panel-1.0",
        "authority":"RESEARCH_PANEL_NON_AUTHORITATIVE",
        "production_behavior_changed":False,
        "rows":rows,
        "summary":{
            "participant_rows":len(rows),
            "trade_count":len({(r["season"],r["transaction_id"]) for r in rows}),
            "participant_rows_by_season":dict(sorted(by_season.items())),
            "seasons":sorted(by_season),
            "rows_with_strategy_outcome_score":0,
            "rows_with_counterfactual_regret":0,
            "authoritative_training_rows":0,
            "observed_team_outcome_panel_available":bool(rows),
        },
        "diagnostics":diagnostics,
        "policy":{
            "post_decision_outcome_is_not_causal_effect":True,
            "no_strategy_scalar_created":True,
            "no_counterfactual_alternative_invented":True,
            "decision_leg_timing_requires_further_adjudication":True,
            "panel_may_support_multi_objective_sensitivity_research":True,
            "panel_alone_cannot_promote_state_weights":True,
        },
    }
    OUT.parent.mkdir(parents=True,exist_ok=True)
    OUT.write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(report["summary"],indent=2))

if __name__=="__main__":
    main()
