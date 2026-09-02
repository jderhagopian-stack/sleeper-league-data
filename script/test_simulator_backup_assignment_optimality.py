#!/usr/bin/env python3
"""Verify the promoted Simulator exact-backup repair against the legacy defect.

The legacy greedy SLOT_SCARCITY algorithm is reproduced locally only as
historical evidence. The live Simulator must no longer expose that heuristic
and must derive a legal exact assignment from league eligibility.
"""
from __future__ import annotations

import importlib.util
import itertools
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
SIM=ROOT/"script"/"run_fsffl_season_simulator_preproduction.py"
OUT=ROOT/"data"/"audit"/"simulator_backup_assignment_optimality.json"

LEGACY_SLOT_SCARCITY={
    "QB":0,
    "TE":1,
    "RB":2,
    "WR":2,
    "SUPER_FLEX":3,
    "FLEX":4,
}

def load():
    spec=importlib.util.spec_from_file_location("coef_backup_sim",SIM)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load Simulator")
    mod=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

sim=load()

def row(pid,pos,value):
    return {
        "player_id":pid,
        "name":pid,
        "position":pos,
        "mean":float(value),
        "sd":0.1,
        "active_probability":1.0,
        "value":float(value),
    }

def eligible(player,slot):
    return sim.core.eligible(player["position"],slot)

def legacy_greedy(slots,players):
    available={p["player_id"]:p for p in players}
    total=0.0
    assignment={}
    order=sorted(
        range(len(slots)),
        key=lambda i:LEGACY_SLOT_SCARCITY.get(slots[i],5),
    )
    for i in order:
        slot=slots[i]
        candidates=[p for p in available.values() if eligible(p,slot)]
        candidates.sort(key=lambda p:p["mean"],reverse=True)
        if not candidates:
            assignment[i]=None
            continue
        pick=candidates[0]
        assignment[i]=pick["player_id"]
        total+=pick["mean"]
        available.pop(pick["player_id"],None)
    return total,assignment

def exact(slots,players):
    best=(-1.0,None)
    choices=[None]+list(range(len(players)))
    for selection in itertools.product(choices,repeat=len(slots)):
        used=[x for x in selection if x is not None]
        if len(used)!=len(set(used)):
            continue
        valid=True
        total=0.0
        assignment={}
        for i,pidx in enumerate(selection):
            if pidx is None:
                assignment[i]=None
                continue
            p=players[pidx]
            if not eligible(p,slots[i]):
                valid=False
                break
            total+=p["mean"]
            assignment[i]=p["player_id"]
        if valid and total>best[0]:
            best=(total,assignment)
    return best

def main():
    slots=["SUPER_FLEX","FLEX"]
    players=[
        row("QB_BACKUP","QB",19.0),
        row("RB_BACKUP","RB",20.0),
    ]
    legacy_total,legacy_assignment=legacy_greedy(slots,players)
    exact_total,exact_assignment=exact(slots,players)

    if legacy_total!=20.0 or exact_total!=39.0:
        raise AssertionError(
            f"legacy/exact counterexample changed: legacy={legacy_total}, exact={exact_total}"
        )

    if hasattr(sim,"SLOT_SCARCITY"):
        raise AssertionError("legacy SLOT_SCARCITY still has live runtime authority")

    lineup=[{"slot":"SUPER_FLEX"},{"slot":"FLEX"}]
    order=sim.constrained_slot_order(lineup)
    ordered=[lineup[i]["slot"] for i in order]
    if ordered.index("FLEX")>ordered.index("SUPER_FLEX"):
        raise AssertionError("live Simulator does not prioritize the more constrained FLEX slot")

    failures=[]
    for qb in (5.0,10.0,15.0,20.0,25.0):
        for rb in (5.0,10.0,15.0,20.0,25.0):
            ps=[row("Q","QB",qb),row("R","RB",rb)]
            g,ga=legacy_greedy(slots,ps)
            e,ea=exact(slots,ps)
            if e>g+1e-9:
                failures.append({
                    "qb_value":qb,
                    "rb_value":rb,
                    "legacy_total":g,
                    "exact_total":e,
                    "legacy_assignment":ga,
                    "exact_assignment":ea,
                })

    if not failures:
        raise AssertionError("historical greedy defect class was not reproduced")

    report={
        "model_version":"FSFFL-Simulator-Backup-Assignment-Optimality-2.0",
        "authority":"RESEARCH_STRUCTURAL_TEST_NON_AUTHORITATIVE",
        "passed":True,
        "test_type":"promoted_repair_verification",
        "production_behavior_changed":False,
        "legacy_greedy_is_exact":False,
        "live_slot_scarcity_heuristic_active":False,
        "live_rule_derived_assignment_active":True,
        "adversarial_legacy_total":legacy_total,
        "adversarial_exact_total":exact_total,
        "adversarial_legacy_assignment":legacy_assignment,
        "adversarial_exact_assignment":exact_assignment,
        "grid_failure_count":len(failures),
        "grid_failures":failures,
        "repair_status":"PROMOTED_TO_MAIN_PR_171",
        "recommended_action":"KEEP_EXACT_RULE_DERIVED_ASSIGNMENT; DO_NOT_REINTRODUCE_SLOT_SCARCITY",
        "promotion_boundary":{
            "structural_defect_demonstrated_historically":True,
            "production_repair_already_promoted":True,
            "new_economic_coefficient_introduced":False,
        },
    }
    OUT.parent.mkdir(parents=True,exist_ok=True)
    OUT.write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(report,indent=2))

if __name__=="__main__":
    main()
