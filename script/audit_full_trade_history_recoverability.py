#!/usr/bin/env python3
"""Inventory recoverability of the full completed FSFFL trade ledger.

This is research/data-governance only. It does not value trades or fit a model.
Its purpose is to stop hard-filtering uncertain observations out of the evidence
base without understanding why they are excluded.

Every completed trade is assigned to one primary recovery bucket plus topology
metadata. The buckets describe the NEXT DATA/RECONSTRUCTION TASK required to
make that observation useful for package-concentration calibration.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/"data"
OUT=DATA/"audit/full_trade_history_recoverability.json"


def asset_count(side):
    return len(side.get("sent_players") or []) + len(side.get("sent_picks") or [])


def topology(sides):
    if len(sides)!=2:
        return "MULTI_PARTY"
    a,b=[asset_count(x) for x in sides]
    if a==1 and b==1:
        return "ONE_FOR_ONE"
    if a==1 and b>1:
        return "ONE_FOR_MANY"
    if a>1 and b==1:
        return "MANY_FOR_ONE"
    return "MANY_FOR_MANY"


def player_counts(sides):
    return [len(x.get("sent_players") or []) for x in sides]


def pick_counts(sides):
    return [len(x.get("sent_picks") or []) for x in sides]


def classify(t):
    season=int(t.get("season") or 0)
    sides=t.get("sides") or []
    if len(sides)!=2:
        return "SPECIAL_MULTI_PARTY_RECONSTRUCTION"
    pcs=pick_counts(sides)
    pls=player_counts(sides)
    has_picks=any(x>0 for x in pcs)
    has_players=any(x>0 for x in pls)

    if season < 2023:
        return "NEEDS_PRE_2023_HISTORICAL_RECONSTRUCTION_BASE"
    if has_picks:
        return "NEEDS_HISTORICAL_PICK_VALUE_COORDINATE"
    # Player-only 2023+ trades can use the current reconstructed-at-time
    # intrinsic player coordinate. Unequal packages are direct concentration
    # evidence; equal package sizes are controls/composition evidence.
    if has_players:
        counts=[asset_count(x) for x in sides]
        if counts==[1,1]:
            return "READY_PLAYER_ONLY_ONE_FOR_ONE_CONTROL"
        if counts[0]==counts[1]:
            return "READY_PLAYER_ONLY_EQUAL_COUNT_CONTROL"
        return "READY_PLAYER_ONLY_UNEQUAL_PACKAGE_EVIDENCE"
    return "SPECIAL_OTHER"


def main():
    rows=json.loads((DATA/"trade_ledger.json").read_text(encoding="utf-8"))
    completed=[x for x in rows if x.get("status")=="complete"]

    records=[]
    buckets=Counter()
    topologies=Counter()
    seasons=Counter()
    bucket_by_season=defaultdict(Counter)
    topology_by_bucket=defaultdict(Counter)

    for t in completed:
        sides=t.get("sides") or []
        bucket=classify(t)
        topo=topology(sides)
        season=str(t.get("season") or "")
        buckets[bucket]+=1
        topologies[topo]+=1
        seasons[season]+=1
        bucket_by_season[season][bucket]+=1
        topology_by_bucket[bucket][topo]+=1
        records.append({
            "transaction_id":str(t.get("transaction_id") or ""),
            "season":int(t.get("season") or 0),
            "created_utc":t.get("created_utc"),
            "participant_count":len(sides),
            "topology":topo,
            "side_asset_counts":[asset_count(x) for x in sides],
            "side_player_counts":player_counts(sides),
            "side_pick_counts":pick_counts(sides),
            "recovery_bucket":bucket,
        })

    immediately_usable=sum(v for k,v in buckets.items() if k.startswith("READY_"))
    needs_pick=buckets["NEEDS_HISTORICAL_PICK_VALUE_COORDINATE"]
    needs_old=buckets["NEEDS_PRE_2023_HISTORICAL_RECONSTRUCTION_BASE"]
    special=sum(v for k,v in buckets.items() if k.startswith("SPECIAL_"))

    # The most important number: how many are excluded for a solvable data
    # coordinate reason rather than because the observation is intrinsically bad.
    recoverable_with_known_infrastructure_work=immediately_usable+needs_pick+needs_old

    payload={
        "schema_version":"1.0",
        "model_version":"FSFFL-Full-Trade-History-Recoverability-1.0",
        "research_only":True,
        "production_behavior_changed":False,
        "coefficient_fit_performed":False,
        "completed_trade_count":len(completed),
        "bilateral_completed_trade_count":sum(1 for x in completed if len(x.get("sides") or [])==2),
        "topology_counts":dict(sorted(topologies.items())),
        "season_counts":dict(sorted(seasons.items())),
        "recovery_bucket_counts":dict(sorted(buckets.items())),
        "recovery_bucket_by_season":{k:dict(v) for k,v in sorted(bucket_by_season.items())},
        "topology_by_recovery_bucket":{k:dict(v) for k,v in sorted(topology_by_bucket.items())},
        "immediately_usable_reconstructed_player_only_count":immediately_usable,
        "needs_historical_pick_coordinate_count":needs_pick,
        "needs_pre_2023_reconstruction_base_count":needs_old,
        "special_case_count":special,
        "recoverable_with_known_infrastructure_work_count":recoverable_with_known_infrastructure_work,
        "recoverable_fraction":round(recoverable_with_known_infrastructure_work/len(completed),4) if completed else 0,
        "recovery_plan":[
            {
                "priority":1,
                "bucket":"READY_*",
                "action":"Use now as reconstructed-at-time player-only evidence, separating unequal packages from equal-size controls.",
            },
            {
                "priority":2,
                "bucket":"NEEDS_HISTORICAL_PICK_VALUE_COORDINATE",
                "action":"Build a point-in-time pick coordinate from contemporaneous draft-slot/rookie-market evidence or another commercially permitted reconstruction; do not use current pick values.",
            },
            {
                "priority":3,
                "bucket":"NEEDS_PRE_2023_HISTORICAL_RECONSTRUCTION_BASE",
                "action":"Extend the historical state/value provider one season earlier so 2022 trades can be reconstructed without current-value backfill.",
            },
            {
                "priority":4,
                "bucket":"SPECIAL_MULTI_PARTY_RECONSTRUCTION",
                "action":"Handle multi-party trades as a separate residualization problem rather than forcing them into bilateral package math.",
            },
        ],
        "principles":{
            "uncertain_observations_are_not_zero_evidence":True,
            "quality_weighting_preferred_to_blanket_exclusion":True,
            "current_value_backfill_forbidden":True,
            "external_restricted_data_not_required_for_recovery_plan":True,
            "completed_trade_does_not_imply_exact_fair_value":True,
        },
        "records":records,
    }

    OUT.parent.mkdir(parents=True,exist_ok=True)
    OUT.write_text(json.dumps(payload,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print(json.dumps({
        "completed_trade_count":payload["completed_trade_count"],
        "bilateral_completed_trade_count":payload["bilateral_completed_trade_count"],
        "topology_counts":payload["topology_counts"],
        "recovery_bucket_counts":payload["recovery_bucket_counts"],
        "recoverable_with_known_infrastructure_work_count":payload["recoverable_with_known_infrastructure_work_count"],
        "recoverable_fraction":payload["recoverable_fraction"],
    },indent=2))

    assert payload["completed_trade_count"]==144
    assert payload["bilateral_completed_trade_count"]>=140
    assert payload["recoverable_with_known_infrastructure_work_count"]>=140
    assert payload["principles"]["quality_weighting_preferred_to_blanket_exclusion"] is True
    assert payload["principles"]["current_value_backfill_forbidden"] is True


if __name__=="__main__":
    main()
