#!/usr/bin/env python3
"""Compare Simulator greedy backup allocation with exact legal assignment.

Research-only structural test. It does not change Simulator behavior. The test
constructs simultaneous lineup absences and asks whether the current
SLOT_SCARCITY greedy allocation can leave projected points unused versus an
exact legal assignment over the same available backup players.
"""
from __future__ import annotations

import importlib.util
import itertools
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
SIM=ROOT/"script"/"run_fsffl_season_simulator_preproduction.py"
OUT=ROOT/"data"/"audit"/"simulator_backup_assignment_optimality.json"

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
    }

def eligible(player,slot):
    if slot=="SUPER_FLEX":
        return player["position"] in {"QB","RB","WR","TE"}
    if slot=="FLEX":
        return player["position"] in {"RB","WR","TE"}
    return player["position"]==slot

def greedy(slots,players):
    available={p["player_id"]:p for p in players}
    total=0.0
    assignment={}
    order=sorted(range(len(slots)),key=lambda i:sim.SLOT_SCARCITY.get(slots[i],5))
    for i in order:
        slot=slots[i]
        candidates=[
            p for p in available.values()
            if eligible(p,slot)
        ]
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
    # Assign either one unused eligible player or empty to every open slot.
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
    # Adversarial nested-eligibility case:
    # QB_BACKUP can fill SUPER_FLEX but not FLEX.
    # RB_BACKUP can fill either.
    # Greedy SUPER_FLEX-first allocation should not consume RB_BACKUP if doing
    # so strands the QB and leaves FLEX empty.
    slots=["SUPER_FLEX","FLEX"]
    players=[
        row("QB_BACKUP","QB",19.0),
        row("RB_BACKUP","RB",20.0),
    ]
    greedy_total,greedy_assignment=greedy(slots,players)
    exact_total,exact_assignment=exact(slots,players)

    if exact_total <= greedy_total:
        raise AssertionError(
            "adversarial case did not expose expected greedy assignment loss: "
            f"greedy={greedy_total}, exact={exact_total}"
        )

    # Also enumerate a compact value grid to establish this is a structural
    # class rather than a single magic-number fixture.
    failures=[]
    for qb in (5.0,10.0,15.0,20.0,25.0):
        for rb in (5.0,10.0,15.0,20.0,25.0):
            ps=[row("Q","QB",qb),row("R","RB",rb)]
            g,ga=greedy(slots,ps)
            e,ea=exact(slots,ps)
            if e>g+1e-9:
                failures.append({
                    "qb_value":qb,
                    "rb_value":rb,
                    "greedy_total":g,
                    "exact_total":e,
                    "greedy_assignment":ga,
                    "exact_assignment":ea,
                })

    if not failures:
        raise AssertionError("no structural greedy backup-assignment failures found")

    report={
        "model_version":"FSFFL-Simulator-Backup-Assignment-Optimality-1.0",
        "authority":"RESEARCH_STRUCTURAL_TEST_NON_AUTHORITATIVE",
        "passed":True,
        "test_type":"structural_counterexample_detection",
        "production_behavior_changed":False,
        "greedy_is_exact":False,
        "adversarial_greedy_total":greedy_total,
        "adversarial_exact_total":exact_total,
        "adversarial_greedy_assignment":greedy_assignment,
        "adversarial_exact_assignment":exact_assignment,
        "grid_failure_count":len(failures),
        "grid_failures":failures,
        "recommended_repair":"replace greedy multi-absence backup allocation with exact legal assignment; do not tune SLOT_SCARCITY",
        "promotion_boundary":{
            "structural_defect_demonstrated":True,
            "production_change_requires_separate_pr":True,
            "downstream_shadow_and_regression_required":True,
        },
    }
    OUT.parent.mkdir(parents=True,exist_ok=True)
    OUT.write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(report,indent=2))

if __name__=="__main__":
    main()
