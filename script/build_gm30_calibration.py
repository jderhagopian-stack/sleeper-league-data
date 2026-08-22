#!/usr/bin/env python3
"""
GM 3.0 calibration and historical-proxy audit.

This is deliberately conservative:
- uses existing FSFFL draft/waiver outcome proxies to quantify league behavior;
- reports sample sizes and limitations;
- does NOT claim that retention equals fantasy success;
- reports whether true prospect-feature backtesting is available.
"""
from __future__ import annotations
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

DATA=Path("data"); OUT=DATA/"gm3"; OUT.mkdir(parents=True,exist_ok=True)

def load(p,d=None):
    p=Path(p)
    if not p.exists(): return d
    with p.open("r",encoding="utf-8") as f:return json.load(f)

def summarize(rows,keys):
    groups=defaultdict(lambda:{"n":0,"rostered":0,"retained":0,"active":0})
    for r in rows or []:
        key=tuple(str(r.get(k)) for k in keys)
        g=groups[key];g["n"]+=1
        g["rostered"]+=bool(r.get("currently_rostered_in_league"))
        g["retained"]+=bool(r.get("still_with_original_drafter"))
        g["active"]+=bool(r.get("currently_active_nfl"))
    out=[]
    for key,g in groups.items():
        n=max(g["n"],1)
        row={k:v for k,v in zip(keys,key)}
        row.update({"sample":g["n"],"rostered_rate":round(g["rostered"]/n,3),
                    "original_drafter_retention_rate":round(g["retained"]/n,3),
                    "active_nfl_rate":round(g["active"]/n,3)})
        out.append(row)
    return sorted(out,key=lambda x:(tuple(x[k] for k in keys)))

def main():
    draft=load(DATA/"draft_outcome_proxy_ledger.json",[])
    waiver=load(DATA/"waiver_outcome_proxy_ledger.json",[])
    txperf=load(DATA/"transaction_performance_index.json",{})
    ownercal=load(DATA/"owner_calibration_report.json",{})
    prospect_history=load(DATA/"gm3_prospect_history.json")
    report={
      "generated_at_utc":datetime.now(timezone.utc).isoformat(),
      "model_version":"FSFFL-GM-3.0",
      "draft_proxy":{"sample":len(draft or []),
                     "by_round_position":summarize(draft,["round","position"]),
                     "by_season_round":summarize(draft,["season","round"])},
      "waiver_proxy":{"sample":len(waiver or []),
                      "warning":"Waiver retention/acquisition proxy is useful for owner behavior, not a pure player-hit label."},
      "existing_transaction_performance_available":txperf is not None,
      "existing_owner_calibration_available":ownercal is not None,
      "true_prospect_feature_backtest":{
          "available":prospect_history is not None,
          "required_file":"data/gm3_prospect_history.json",
          "status":"READY_TO_RUN" if prospect_history is not None else "DATA_GAP",
          "why_it_matters":"Breakout age, YPRR, dominator, athleticism and draft-capital weights should not be optimized against retention proxies."
      },
      "calibration_policy":{
          "allowed_now":["owner behavior priors","pick-location priors","waiver aggression priors","retention/roster survival sanity checks"],
          "not_claimed_without_true_history":["causal prospect hit rates","Puka-style sleeper precision","college metric coefficients"],
          "future_metrics":["precision_at_k","recall_of_breakouts","false_positive_rate","market_value_gain_30_90_365d","Brier/log-loss where probabilistic labels exist"]
      }
    }
    with (OUT/"calibration_report.json").open("w",encoding="utf-8") as f:json.dump(report,f,indent=2)
    print(f"GM 3.0 calibration audit complete: {len(draft or [])} draft proxy rows; true prospect backtest={report['true_prospect_feature_backtest']['status']}")
if __name__=="__main__":main()
