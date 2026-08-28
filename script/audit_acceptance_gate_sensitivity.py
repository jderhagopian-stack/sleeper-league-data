#!/usr/bin/env python3
"""Executable sensitivity audit for the hand-set acceptance-band gate.

Runs the same v1.17 state-aware market sweep under:
1) production HIGH+MEDIUM action eligibility,
2) HIGH-only action eligibility,
3) no acceptance-band gate (while preserving bilateral/current-state viability).

This does not tune thresholds or claim acceptance probabilities. It measures
whether the provisional band cutoffs have material leverage over the recommended
negotiation action and selected normal alternatives.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
V18=ROOT/"script"/"run_trade_market_sweep_v18.py"
V21=ROOT/"script"/"run_trade_market_sweep_v21.py"
RUNNER=ROOT/"script"/"run_trade_market_sweep_v23.py"
MODEL_VERSION="FSFFL-Acceptance-Gate-Sensitivity-1.0"

def replace_required(text,old,new):
    if old not in text:
        raise AssertionError(f"replacement marker missing: {old}")
    return text.replace(old,new)

def variants(v18,v21):
    out={"production_high_medium":(v18,v21)}

    h18=replace_required(
        v18,
        'realistic=[r for r in viable if r["acceptance_likelihood"] in {"HIGH","MEDIUM"}]',
        'realistic=[r for r in viable if r["acceptance_likelihood"]=="HIGH"]',
    )
    h21=replace_required(
        v21,
        'if row.get("acceptance_likelihood") not in {"HIGH", "MEDIUM"}:',
        'if row.get("acceptance_likelihood") != "HIGH":',
    )
    out["high_only"]=(h18,h21)

    n18=replace_required(
        v18,
        'realistic=[r for r in viable if r["acceptance_likelihood"] in {"HIGH","MEDIUM"}]',
        'realistic=list(viable)',
    )
    n21=replace_required(
        v21,
        'if row.get("acceptance_likelihood") not in {"HIGH", "MEDIUM"}:',
        'if False:  # audit variant: preserve rationality but remove band gate',
    )
    out["no_acceptance_band_gate"]=(n18,n21)
    return out

def run_case(name,scenario,sims,depth):
    path=Path("/tmp")/f"acceptance-gate-{name}.json"
    cmd=[
        "python",str(RUNNER),
        "--scenario",str(scenario),
        "--quick-sims",str(sims),
        "--confirm-sims","0",
        "--search-depth",str(depth),
        "--output",str(path),
    ]
    subprocess.run(cmd,cwd=ROOT,check=True,stdout=subprocess.DEVNULL)
    return json.loads(path.read_text(encoding="utf-8"))

def row_key(row):
    return {
        "buyer_user_id":str(row.get("buyer_user_id") or ""),
        "candidate_type":str(row.get("candidate_type") or ""),
        "outgoing_assets":sorted(map(str,row.get("outgoing_assets") or [])),
        "return_assets":sorted(map(str,row.get("return_assets") or [])),
    }

def signature(report):
    normal=list(report.get("top_5_alternatives") or report.get("ranked_finalists") or [])
    realistic=list(report.get("realistic_counter_alternatives") or [])
    return {
        "recommended_next_action":report.get("recommended_next_action"),
        "realistic_count":int((report.get("candidate_counts") or {}).get("realistic_acceptance_fit") or len(realistic)),
        "top_option":row_key(normal[0]) if normal else None,
        "top_realistic_option":row_key(realistic[0]) if realistic else None,
        "top_five":[row_key(x) for x in normal[:5]],
    }

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--scenario",default="data/decision_lab/full_validation_scenario.json")
    ap.add_argument("--quick-sims",type=int,default=100)
    ap.add_argument("--search-depth",type=int,default=40)
    ap.add_argument("--output",default="data/audit/acceptance_gate_sensitivity.json")
    args=ap.parse_args()

    scenario=(ROOT/args.scenario) if not Path(args.scenario).is_absolute() else Path(args.scenario)
    original18=V18.read_text(encoding="utf-8")
    original21=V21.read_text(encoding="utf-8")
    cases={}
    try:
        for name,(s18,s21) in variants(original18,original21).items():
            V18.write_text(s18,encoding="utf-8")
            V21.write_text(s21,encoding="utf-8")
            cases[name]=run_case(name,scenario,args.quick_sims,args.search_depth)
    finally:
        V18.write_text(original18,encoding="utf-8")
        V21.write_text(original21,encoding="utf-8")

    sig={k:signature(v) for k,v in cases.items()}
    base=sig["production_high_medium"]
    comparisons={}
    action_sensitive=False
    top_sensitive=False
    for name,row in sig.items():
        action_changed=row["recommended_next_action"]!=base["recommended_next_action"]
        top_changed=row["top_option"]!=base["top_option"]
        action_sensitive|=action_changed
        top_sensitive|=top_changed
        comparisons[name]={
            **row,
            "action_changed_vs_production":action_changed,
            "top_option_changed_vs_production":top_changed,
        }

    payload={
        "model_version":MODEL_VERSION,
        "source_market_sweep_model":"FSFFL-Counter-Market-Sweep-1.17",
        "scenario":str(Path(args.scenario)),
        "simulation":{"quick_sims":args.quick_sims,"search_depth":args.search_depth},
        "interpretation":{
            "historical_validation":False,
            "coefficient_tuning":False,
            "acceptance_fit_is_probability":False,
            "band_gate_only_ablation":True,
            "buyer_current_state_rationality_preserved":True,
            "focal_viability_preserved":True,
        },
        "thresholds":{
            "production_realistic_floor":0.48,
            "high_only_floor":0.68,
            "no_band_gate_floor":None,
        },
        "comparisons":comparisons,
        "summary":{
            "recommended_action_sensitive_to_band_gate":action_sensitive,
            "top_option_sensitive_to_band_gate":top_sensitive,
            "production_recommendation_empirically_authoritative":False,
            "reason":"Acceptance thresholds remain hand-set and lack an accepted/rejected offer denominator; this audit measures leverage, not predictive validity.",
        },
    }
    out=Path(args.output)
    if not out.is_absolute(): out=ROOT/out
    out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(payload,indent=2)+"\n",encoding="utf-8")

    assert V18.read_text(encoding="utf-8")==original18
    assert V21.read_text(encoding="utf-8")==original21
    assert set(cases)=={"production_high_medium","high_only","no_acceptance_band_gate"}
    print(json.dumps(payload["summary"],indent=2))
    print(json.dumps(comparisons,indent=2))

if __name__=="__main__":
    main()
