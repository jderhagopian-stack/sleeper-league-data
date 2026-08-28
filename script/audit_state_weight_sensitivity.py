#!/usr/bin/env python3
"""Executable sensitivity audit for provisional competitive-state objective weights."""
from __future__ import annotations
import argparse,json,subprocess
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
ARTIFACT=ROOT/"data"/"gm"/"state_weight_calibration.json"
RUNNER=ROOT/"script"/"run_trade_market_sweep_v23.py"
MODEL_VERSION="FSFFL-State-Weight-Sensitivity-1.0"

def variant(base,direction,amount=.05):
    d=json.loads(json.dumps(base))
    bounds=d["bounds"]
    for row in d["anchor_points"]:
        w=row["weights"]
        if direction=="current_heavy":
            shift=min(amount,bounds["current"][1]-w["current"],w["future"]-bounds["future"][0])
            w["current"]+=shift; w["future"]-=shift
        elif direction=="future_heavy":
            shift=min(amount,bounds["future"][1]-w["future"],w["current"]-bounds["current"][0])
            w["future"]+=shift; w["current"]-=shift
        else:
            raise ValueError(direction)
        # Preserve exact simplex after floating arithmetic.
        total=sum(w.values())
        for k in w: w[k]/=total
    d["model_version"]=base.get("model_version")+f"-SENSITIVITY-{direction.upper()}"
    d["status"]="SENSITIVITY_VARIANT_NOT_FOR_PRODUCTION"
    return d

def run_case(name,scenario,sims,depth):
    out=Path("/tmp")/f"state-weight-{name}.json"
    subprocess.run([
        "python",str(RUNNER),"--scenario",str(scenario),
        "--quick-sims",str(sims),"--confirm-sims","0",
        "--search-depth",str(depth),"--output",str(out)
    ],cwd=ROOT,check=True,stdout=subprocess.DEVNULL)
    return json.loads(out.read_text())

def key(row):
    return {
      "buyer_user_id":str(row.get("buyer_user_id") or ""),
      "outgoing_assets":sorted(map(str,row.get("outgoing_assets") or [])),
      "return_assets":sorted(map(str,row.get("return_assets") or [])),
      "candidate_type":str(row.get("candidate_type") or "")
    }

def signature(r):
    rows=list(r.get("top_5_alternatives") or r.get("ranked_finalists") or [])
    return {
      "recommended_next_action":r.get("recommended_next_action"),
      "top_option":key(rows[0]) if rows else None,
      "top_five":[key(x) for x in rows[:5]],
      "top_scores":[float((x.get("negotiation_ranking") or {}).get("score") or 0) for x in rows[:5]],
    }

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--scenario",default="data/decision_lab/full_validation_scenario.json")
    ap.add_argument("--quick-sims",type=int,default=100)
    ap.add_argument("--search-depth",type=int,default=40)
    ap.add_argument("--output",default="data/audit/state_weight_sensitivity.json")
    a=ap.parse_args()
    scenario=ROOT/a.scenario if not Path(a.scenario).is_absolute() else Path(a.scenario)
    original_text=ARTIFACT.read_text()
    original=json.loads(original_text)
    cases={
      "production_prior":original,
      "current_heavy":variant(original,"current_heavy"),
      "future_heavy":variant(original,"future_heavy"),
    }
    reports={}
    try:
      for name,art in cases.items():
        ARTIFACT.write_text(json.dumps(art,indent=2)+"\n")
        reports[name]=run_case(name,scenario,a.quick_sims,a.search_depth)
    finally:
      ARTIFACT.write_text(original_text)

    sig={k:signature(v) for k,v in reports.items()}
    base=sig["production_prior"]
    comp={}
    action_sensitive=top_sensitive=order_sensitive=False
    for name,row in sig.items():
      ac=row["recommended_next_action"]!=base["recommended_next_action"]
      tc=row["top_option"]!=base["top_option"]
      oc=row["top_five"]!=base["top_five"]
      action_sensitive|=ac; top_sensitive|=tc; order_sensitive|=oc
      comp[name]={**row,"action_changed_vs_prior":ac,"top_option_changed_vs_prior":tc,"top_five_order_changed_vs_prior":oc}

    payload={
      "model_version":MODEL_VERSION,
      "source_weight_model":original.get("model_version"),
      "perturbation":{
        "current_future_shift_max":.05,
        "within_configured_bounds":True,
        "liquidity_resilience_held_constant":True,
      },
      "interpretation":{
        "historical_validation":False,
        "coefficient_tuning":False,
        "expert_prior_remains_unvalidated":True,
        "reasonable_bound_sensitivity_only":True,
      },
      "comparisons":comp,
      "summary":{
        "recommended_action_sensitive":action_sensitive,
        "top_option_sensitive":top_sensitive,
        "top_five_order_sensitive":order_sensitive,
        "production_weights_empirically_authoritative":False,
      }
    }
    out=Path(a.output); out=out if out.is_absolute() else ROOT/out
    out.parent.mkdir(parents=True,exist_ok=True); out.write_text(json.dumps(payload,indent=2)+"\n")
    assert ARTIFACT.read_text()==original_text
    for art in cases.values():
      for p in art["anchor_points"]:
        assert abs(sum(p["weights"].values())-1)<1e-9
        for k,v in p["weights"].items():
          lo,hi=original["bounds"][k]; assert lo-1e-9<=v<=hi+1e-9
    print(json.dumps(payload["summary"],indent=2))
    print(json.dumps(comp,indent=2))

if __name__=="__main__": main()
