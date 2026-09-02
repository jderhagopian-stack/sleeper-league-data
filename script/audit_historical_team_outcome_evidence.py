#!/usr/bin/env python3
"""Inventory committed historical team-level outcome evidence for calibration.

This is a schema/readiness audit only. It does not construct a strategy score,
infer alternatives, or modify production economics.
"""
from __future__ import annotations

import json
from pathlib import Path
from collections import Counter

ROOT=Path(__file__).resolve().parent.parent
DATA=ROOT/"data"
OUT=DATA/"audit"/"historical_team_outcome_evidence.json"

def load(path,default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default

def describe_json(obj):
    out={"top_type":type(obj).__name__}
    rows=[]
    if isinstance(obj,list):
        rows=obj
        out["row_count"]=len(obj)
    elif isinstance(obj,dict):
        out["top_keys"]=sorted(map(str,obj.keys()))[:50]
        for key in ("rows","matchups","games","history","seasons","transactions","teams"):
            if isinstance(obj.get(key),list):
                rows=obj[key]
                out["primary_list_key"]=key
                out["row_count"]=len(rows)
                break
    if rows:
        keys=Counter()
        for row in rows[:500]:
            if isinstance(row,dict):
                keys.update(map(str,row.keys()))
        out["sample_row_keys"]=sorted(keys)
        out["sample_rows"]=rows[:3]
    return out

def matchup_files():
    found=[]
    root=DATA/"stats"/"fsffl"
    for season_dir in sorted(root.glob("*")):
        if not season_dir.is_dir():
            continue
        path=season_dir/"league_matchups_raw.json"
        if not path.exists():
            continue
        obj=load(path,None)
        desc=describe_json(obj)
        found.append({
            "season":season_dir.name,
            "path":path.relative_to(ROOT).as_posix(),
            "size_bytes":path.stat().st_size,
            **desc,
        })
    return found

def trade_seasons():
    rows=load(DATA/"trade_ledger.json",[])
    counts=Counter(str(r.get("season") or "") for r in rows if isinstance(r,dict))
    completed=Counter(
        str(r.get("season") or "")
        for r in rows
        if isinstance(r,dict) and str(r.get("status") or "").lower() in {"complete","completed"}
    )
    return {
        "trade_rows":len(rows) if isinstance(rows,list) else 0,
        "by_season":dict(sorted(counts.items())),
        "completed_by_season":dict(sorted(completed.items())),
    }

def transaction_seasons():
    rows=load(DATA/"transactions.json",[])
    counts=Counter(str(r.get("season") or "") for r in rows if isinstance(r,dict))
    return {
        "transaction_rows":len(rows) if isinstance(rows,list) else 0,
        "by_season":dict(sorted(counts.items())),
    }

def main():
    matchups=matchup_files()
    completed_outcome_seasons=[
        x["season"] for x in matchups
        if x["season"].isdigit() and int(x["season"]) <= 2025 and x.get("size_bytes",0)>2
    ]
    trades=trade_seasons()
    tx=transaction_seasons()
    trade_years=set(k for k,v in trades["completed_by_season"].items() if v>0)
    overlap=sorted(trade_years & set(completed_outcome_seasons))

    league_history=DATA/"league_history.json"
    league_desc=describe_json(load(league_history,None)) if league_history.exists() else {"missing":True}

    report={
        "model_version":"FSFFL-Historical-Team-Outcome-Evidence-1.0",
        "authority":"RESEARCH_DISCOVERY_ONLY",
        "production_behavior_changed":False,
        "matchup_files":matchups,
        "trade_ledger":trades,
        "transaction_ledger":tx,
        "league_history":{
            "path":league_history.relative_to(ROOT).as_posix(),
            "exists":league_history.exists(),
            "size_bytes":league_history.stat().st_size if league_history.exists() else 0,
            "schema":league_desc,
        },
        "summary":{
            "committed_completed_matchup_seasons":completed_outcome_seasons,
            "completed_trade_seasons_with_matchup_overlap":overlap,
            "team_level_realized_points_evidence_available":bool(overlap),
            "team_level_realized_win_evidence_candidate":bool(overlap),
            "playoff_outcome_evidence_requires_schema_adjudication":True,
            "counterfactual_alternative_outcomes_directly_observed":False,
            "single_scalar_strategy_target_authorized":False,
            "multi_objective_outcome_construction_feasible_for_completed_seasons":bool(overlap),
        },
        "policy":{
            "actual_matchups_may_support_realized_team_outcomes":True,
            "post_decision_team_outcome_is_not_causal_trade_effect":True,
            "acquired_player_points_are_not_net_trade_value":True,
            "retention_proxy_is_not_asset_value":True,
            "counterfactual_regret_requires_defensible_alternative_reconstruction":True,
            "no_hindsight_features_in_candidate_fit":True,
        },
    }
    OUT.parent.mkdir(parents=True,exist_ok=True)
    OUT.write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(report["summary"],indent=2))
    print(json.dumps({
        "matchup_schema_keys_by_season":{
            x["season"]:x.get("sample_row_keys",[]) for x in matchups
        },
        "league_history_schema":league_desc,
    },indent=2))

if __name__=="__main__":
    main()
