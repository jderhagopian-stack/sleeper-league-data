#!/usr/bin/env python3
"""Executable leverage audit for acceptance-band gating.

Production now treats HIGH/MEDIUM/LOW/VERY_LOW as descriptive/ranking labels,
not candidate or action eligibility gates. This audit temporarily reintroduces
HIGH+MEDIUM and HIGH-only gates to measure how much leverage those unsupported
cutoffs would have if they were made authoritative again.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
V21=ROOT/"script"/"run_trade_market_sweep_v21.py"
V23=ROOT/"script"/"run_trade_market_sweep_v23.py"
RUNNER=V23
MODEL_VERSION="FSFFL-Acceptance-Gate-Sensitivity-2.1"


def replace_required(text,old,new):
    if old not in text:
        raise AssertionError(f"replacement marker missing: {old}")
    return text.replace(old,new,1)


def gate_variant(v21,v23,allowed_expr):
    s21=replace_required(
        v21,
        '    for row in viable:\n        fam = negotiation_family_key(row)',
        f'    for row in viable:\n        if row.get("acceptance_likelihood") not in {allowed_expr}:\n            continue\n        fam = negotiation_family_key(row)',
    )
    # v23 intentionally uses compact formatting; inject the counterfactual gate
    # at the semantic top-option construction rather than depending on spaces.
    old='    top=list(report.get(\'top_5_alternatives\') or report.get(\'ranked_finalists\') or [])'
    new=f'    top=[r for r in list(report.get(\'top_5_alternatives\') or report.get(\'ranked_finalists\') or []) if r.get(\'acceptance_likelihood\') in {allowed_expr}]'
    s23=replace_required(v23,old,new)
    return s21,s23


def variants(v21,v23):
    out={"production_no_band_gate":(v21,v23)}
    out["reintroduced_high_medium_gate"]=gate_variant(v21,v23,'{"HIGH", "MEDIUM"}')
    out["reintroduced_high_only_gate"]=gate_variant(v21,v23,'{"HIGH"}')
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
    return {
        "recommended_next_action":report.get("recommended_next_action"),
        "top_option":row_key(normal[0]) if normal else None,
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
    original21=V21.read_text(encoding="utf-8")
    original23=V23.read_text(encoding="utf-8")
    cases={}
    try:
        for name,(s21,s23) in variants(original21,original23).items():
            V21.write_text(s21,encoding="utf-8")
            V23.write_text(s23,encoding="utf-8")
            cases[name]=run_case(name,scenario,args.quick_sims,args.search_depth)
    finally:
        V21.write_text(original21,encoding="utf-8")
        V23.write_text(original23,encoding="utf-8")

    sig={k:signature(v) for k,v in cases.items()}
    base=sig["production_no_band_gate"]
    comparisons={}
    action_sensitive=False
    top_sensitive=False
    for name,row in sig.items():
        action_changed=row["recommended_next_action"]!=base["recommended_next_action"]
        top_changed=row["top_option"]!=base["top_option"]
        if name != "production_no_band_gate":
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
            "production_band_gate_active":False,
            "counterfactual_gate_reintroduction_only":True,
            "buyer_current_state_rationality_preserved":True,
            "focal_viability_preserved":True,
        },
        "comparisons":comparisons,
        "summary":{
            "recommended_action_sensitive_if_band_gate_reintroduced":action_sensitive,
            "top_option_sensitive_if_band_gate_reintroduced":top_sensitive,
            "production_recommendation_empirically_authoritative":False,
            "reason":"Acceptance thresholds lack an accepted/rejected opportunity denominator. Production therefore reports acceptance fit separately from trade quality; this audit only measures the leverage the old gate would have if restored.",
        },
    }
    out=Path(args.output)
    if not out.is_absolute(): out=ROOT/out
    out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(payload,indent=2)+"\n",encoding="utf-8")

    assert V21.read_text(encoding="utf-8")==original21
    assert V23.read_text(encoding="utf-8")==original23
    assert set(cases)=={"production_no_band_gate","reintroduced_high_medium_gate","reintroduced_high_only_gate"}
    print(json.dumps(payload["summary"],indent=2))
    print(json.dumps(comparisons,indent=2))

if __name__=="__main__":
    main()
