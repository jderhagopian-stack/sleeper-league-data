#!/usr/bin/env python3
"""Research-only internal historical package-concentration pilot.

Reconstructs selected completed FSFFL trades with NO external historical market
source. The value coordinate therefore comes from the existing leakage-safe
historical FSFFL reconstruction path (prior completed-season / completed-at-time
football evidence plus canonical pick fallbacks).

This does NOT fit or promote a coefficient. Completed trades are not assumed to
be exactly fair. The pilot asks only whether package transforms reduce systematic
one-vs-many value imbalance across a small cross-season sample relative to raw
additivity.

Any production-prior update requires a separate governed review.
"""
from __future__ import annotations

import importlib.util
import json
import statistics
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SCRIPT=ROOT/"script"
OUT=ROOT/"data/audit/internal_historical_package_pilot.json"

PILOT=[
    ("2022","879037446661283840"),
    ("2023","936055959821070336"),
    ("2024","1065032468199329792"),
    ("2025","1195790278812782592"),
    ("2026","1377426607064502272"),
]

CURVES={
    "additive":[1.0,1.0,1.0,1.0,1.0],
    "mild":[1.0,.92,.84,.78,.72],
    "center":[1.0,.85,.73,.64,.57],
    "strong":[1.0,.78,.62,.50,.42],
}


def load(path,name):
    spec=importlib.util.spec_from_file_location(name,path)
    mod=importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


BUILDER=load(SCRIPT/"build_historical_gm3_bundle.py","historical_package_pilot_builder")
ANALYSIS=load(SCRIPT/"run_historical_trade_analysis.py","historical_package_pilot_analysis")
STATE=load(SCRIPT/"fsffl_historical_state_provider.py","historical_package_pilot_state")


def tail(curve_name,idx):
    curve=CURVES[curve_name]
    if idx < len(curve):
        return curve[idx]
    if curve_name=="mild":
        return .72
    if curve_name=="strong":
        return max(.28,.42-.06*(idx-len(curve)+1))
    if curve_name=="center":
        return (tail("mild",idx)+tail("strong",idx))/2
    return 1.0


def eff(values,curve_name):
    vals=sorted((float(v) for v in values),reverse=True)
    return sum(v*tail(curve_name,i) for i,v in enumerate(vals))


def catalog(bundle):
    out={}
    for aid,row in (bundle.get("market_player_values") or {}).items():
        out[str(aid)]=float(row.get("dynasty") or 0)
    for aid,row in (bundle.get("market_pick_values") or {}).items():
        out[str(aid)]=float(row.get("dynasty") or 0)
    return out


def side_assets(actions,uid):
    sent=[]; recv=[]
    for a in actions:
        src=str(a.get("from_user_id") or "")
        dst=str(a.get("to_user_id") or "")
        ids=[f"player:{x}" for x in a.get("players") or []] + [str(x) for x in a.get("picks") or []]
        if src==uid:
            sent.extend(ids)
        if dst==uid:
            recv.extend(ids)
    return sent,recv


def main():
    provider=STATE.HistoricalStateProvider()
    rows=[]
    for season,tid in PILOT:
        data=provider.data(season)
        tx=ANALYSIS.find_trade(provider,season,tid)
        actions=ANALYSIS.transaction_actions(tx,data)

        # Explicitly pass no external source.
        bundle=BUILDER.build(season,tid,None,provider=provider)
        prov=bundle.get("provenance") or {}
        assert prov.get("current_market_values_used") is False
        assert prov.get("dated_market_anchor_available") is False
        assert prov.get("market_source_file") is None
        assert prov.get("same_season_results_used") is False
        assert prov.get("future_schedule_used") is False

        vals=catalog(bundle)
        users=sorted({
            str(a.get("from_user_id")) for a in actions
        } | {
            str(a.get("to_user_id")) for a in actions
        })
        users=[x for x in users if x]
        if len(users)!=2:
            continue

        side_rows=[]
        curve_abs={}
        for uid in users:
            sent,recv=side_assets(actions,uid)
            missing=[x for x in sent+recv if x not in vals]
            if missing:
                raise RuntimeError(f"{season}/{tid}/{uid}: missing reconstructed values {missing}")
            sent_vals=[vals[x] for x in sent]
            recv_vals=[vals[x] for x in recv]
            curves={}
            for name in CURVES:
                delta=eff(recv_vals,name)-eff(sent_vals,name)
                curves[name]=round(delta,2)
                curve_abs.setdefault(name,[]).append(abs(delta))
            side_rows.append({
                "user_id":uid,
                "sent_assets":sent,
                "received_assets":recv,
                "sent_raw_values":[round(x,2) for x in sent_vals],
                "received_raw_values":[round(x,2) for x in recv_vals],
                "curve_deltas":curves,
            })

        # The two sides are symmetric signs on the same reconstructed coordinate.
        # Use mean absolute side imbalance only as a descriptive clearing-distance
        # diagnostic, not as a claim that every completed trade should equal zero.
        distance={name:round(statistics.mean(v),2) for name,v in curve_abs.items()}
        best=min(distance,key=distance.get)
        rows.append({
            "season":int(season),
            "transaction_id":tid,
            "trade_time_utc":bundle.get("as_of_utc"),
            "historical_value_basis":{
                "external_market_source_used":False,
                "current_market_values_used":False,
                "reconstruction_class":(bundle.get("provenance") or {}).get("historical_input_class"),
                "valuation_architecture":(bundle.get("provenance") or {}).get("valuation_architecture"),
            },
            "sides":side_rows,
            "absolute_clearing_distance":distance,
            "lowest_distance_curve":best,
        })

    aggregate={}
    for name in CURVES:
        vals=[float(x["absolute_clearing_distance"][name]) for x in rows]
        aggregate[name]={
            "mean_absolute_clearing_distance":round(statistics.mean(vals),2) if vals else None,
            "median_absolute_clearing_distance":round(statistics.median(vals),2) if vals else None,
            "wins_lowest_distance":sum(x["lowest_distance_curve"]==name for x in rows),
        }

    ranked=sorted(
        aggregate,
        key=lambda name=(None): (
            aggregate[name]["mean_absolute_clearing_distance"]
            if aggregate[name]["mean_absolute_clearing_distance"] is not None else 1e99
        )
    )

    payload={
        "schema_version":"1.0",
        "model_version":"FSFFL-Internal-Historical-Package-Pilot-1.0",
        "research_only":True,
        "production_behavior_changed":False,
        "coefficient_fit_performed":False,
        "production_prior_changed":False,
        "external_historical_market_source_used":False,
        "current_market_value_backfill_used":False,
        "completed_trade_implies_exact_fair_value":False,
        "pilot_trade_count":len(rows),
        "curve_definitions":CURVES,
        "aggregate":aggregate,
        "descriptive_rank_by_mean_clearing_distance":ranked,
        "trades":rows,
        "interpretation_rule":(
            "Lower reconstructed clearing distance is only directional evidence about package shape. "
            "It is not an empirical coefficient estimate because completed trades need not clear at exact "
            "equal value and reconstructed historical values inherit model assumptions."
        ),
        "next_gate":(
            "If the pilot is stable, scale to all eligible historical one-vs-many trades, use temporal "
            "holdouts, compare systematic residual bias by topology/context, and only then consider "
            "narrowing/replacing the bounded provisional prior."
        ),
    }
    OUT.parent.mkdir(parents=True,exist_ok=True)
    OUT.write_text(json.dumps(payload,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print(json.dumps({
        "pilot_trade_count":payload["pilot_trade_count"],
        "aggregate":aggregate,
        "descriptive_rank":ranked,
    },indent=2))

    assert payload["pilot_trade_count"] >= 4
    assert payload["research_only"] is True
    assert payload["external_historical_market_source_used"] is False
    assert payload["current_market_value_backfill_used"] is False
    assert payload["coefficient_fit_performed"] is False
    assert payload["production_prior_changed"] is False


if __name__=="__main__":
    main()
