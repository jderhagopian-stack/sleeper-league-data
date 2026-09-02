#!/usr/bin/env python3
"""Synthetic Superflex QB opportunity-cost tests using canonical lineup mechanics.

No QB multiplier or second valuation model is introduced. The test asks whether
league-rule eligibility plus legal lineup reoptimization creates the expected
replacement-depth economics.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
ENGINE=ROOT/"script"/"build_fsffl_gm_engine.py"

def load():
    spec=importlib.util.spec_from_file_location("coef_superflex_gm",ENGINE)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load GM engine")
    mod=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

gm=load()

def p(pid,pos,value):
    return {
        "player_id":pid,
        "name":pid,
        "position":pos,
        "market_redraft":float(value),
        "market_dynasty":float(value),
    }

def common_skill_values():
    rows=[
        p("RB1","RB",29),p("RB2","RB",27),p("RB3","RB",22),p("RB4","RB",16),
        p("WR1","WR",30),p("WR2","WR",28),p("WR3","WR",26),p("WR4","WR",23),p("WR5","WR",18),
        p("TE1","TE",20),p("TE2","TE",12),
    ]
    return {x["player_id"]:x for x in rows}

def roster(qb3_value):
    vals=common_skill_values()
    for row in (
        p("QB_ELITE","QB",42),
        p("QB2","QB",31),
        p("QB3","QB",qb3_value),
    ):
        vals[row["player_id"]]=row
    return vals

def lineup(vals,remove=None):
    ids=[x for x in vals if x!=remove]
    return gm.optimize_lineup(ids,vals,"market_redraft")

def drop_when_elite_removed(qb3_value):
    vals=roster(qb3_value)
    before=lineup(vals)
    after=lineup(vals,remove="QB_ELITE")
    return before["total"]-after["total"],before,after

def main():
    if "SUPER_FLEX" not in gm.LINEUP_SLOTS:
        raise AssertionError("FSFFL lineup no longer contains SUPER_FLEX")
    if "QB" not in gm.slot_eligible_positions("SUPER_FLEX"):
        raise AssertionError("QB is not eligible in governed SUPER_FLEX rules")

    deep_drop,deep_before,deep_after=drop_when_elite_removed(25)
    shallow_drop,shallow_before,shallow_after=drop_when_elite_removed(6)
    medium_drop,_,_=drop_when_elite_removed(16)

    if shallow_drop <= deep_drop:
        raise AssertionError(
            f"elite QB removal should hurt shallow QB room more: shallow={shallow_drop}, deep={deep_drop}"
        )
    if not (deep_drop <= medium_drop <= shallow_drop):
        raise AssertionError(
            f"QB replacement improvement is not monotonic: deep={deep_drop}, medium={medium_drop}, shallow={shallow_drop}"
        )

    deep_qbs={
        row["player_id"]
        for row in deep_after["lineup"]
        if row.get("position")=="QB"
    }
    if "QB3" not in deep_qbs:
        raise AssertionError("strong QB3 did not enter optimized lineup after elite QB removal")

    shallow_qbs={
        row["player_id"]
        for row in shallow_after["lineup"]
        if row.get("position")=="QB"
    }
    if "QB3" in shallow_qbs:
        raise AssertionError("replacement-level QB3 incorrectly beat stronger legal Superflex alternatives")

    # Removing the elite QB cannot improve the optimized lineup in either room.
    if deep_after["total"] > deep_before["total"] or shallow_after["total"] > shallow_before["total"]:
        raise AssertionError("removing elite QB improved optimized lineup")

    print({
        "passed":True,
        "authority":"canonical_lineup_optimizer",
        "new_qb_multiplier_introduced":False,
        "deep_qb_room_elite_removal_drop":deep_drop,
        "medium_qb_room_elite_removal_drop":medium_drop,
        "shallow_qb_room_elite_removal_drop":shallow_drop,
        "interpretation":"replacement depth endogenously changes Superflex opportunity cost",
    })

if __name__=="__main__":
    main()
