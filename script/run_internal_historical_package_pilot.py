#!/usr/bin/env python3
"""Research-only FSFFL historical package-concentration pilot.

Purpose
-------
Use the project's own point-in-time reconstruction to test package-shape
assumptions without importing third-party historical value tables and without
backfilling present-day player values.

The pilot deliberately separates:
- informative unequal-package completed trades (direct concentration evidence);
- one-for-one controls (must be invariant);
- equal-count multi-player controls (distortion/sanity checks).

It does NOT assume completed trades are exactly fair, fit a coefficient, or
change production. Current model methodology is applied to information
reconstructed as of the trade date, so these are RECONSTRUCTED_AT_TIME research
observations rather than pristine archived/out-of-sample observations.
"""
from __future__ import annotations

import importlib.util
import json
import statistics
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SCRIPT=ROOT/"script"
DATA=ROOT/"data"
OUT=DATA/"audit/internal_historical_package_pilot.json"

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
    sys.modules[name]=mod
    spec.loader.exec_module(mod)
    return mod


BUILDER=load(SCRIPT/"build_historical_gm3_bundle.py","historical_package_pilot_builder")
ANALYSIS=load(SCRIPT/"run_historical_trade_analysis.py","historical_package_pilot_analysis")
STATE=load(SCRIPT/"fsffl_historical_state_provider.py","historical_package_pilot_state")
GM=load(SCRIPT/"build_fsffl_gm_engine.py","historical_package_pilot_gm")


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


def eligible_ledger_rows():
    rows=json.loads((DATA/"trade_ledger.json").read_text(encoding="utf-8"))
    out=[]
    for t in rows:
        if t.get("status")!="complete" or len(t.get("sides") or [])!=2:
            continue
        season=int(t.get("season") or 0)
        # 2022 was the live startup draft. Sleeper "pick" order represented
        # nomination mechanics rather than rookie-draft capital, so 2022 is
        # intentionally excluded from package/pick calibration rather than
        # being treated as a missing reconstruction problem.
        if season < 2023:
            continue
        sides=t["sides"]
        if any((s.get("sent_picks") or []) for s in sides):
            continue
        counts=[len(s.get("sent_players") or []) for s in sides]
        if any(x<=0 for x in counts):
            continue
        out.append((str(season),str(t.get("transaction_id")),counts))
    return out


def side_assets(actions,uid):
    sent=[]; received=[]
    for a in actions:
        src=str(a.get("from_user_id") or "")
        dst=str(a.get("to_user_id") or "")
        ids=[str(x) for x in a.get("players") or []]
        if src==uid:
            sent.extend(ids)
        if dst==uid:
            received.extend(ids)
    return sent,received


def reconstructed_intrinsic_values(provider,season,tid,tx):
    data=provider.data(season)
    state=provider.pre_transaction_state(season,tid)
    rosters=BUILDER.historical_rosters(state,data)
    players=BUILDER.player_index()
    ts=int(tx.get("created") or 0)
    prior,baselines,scoring_basis=BUILDER.scoring_as_of(int(season),ts,players)
    values,external_exact_count=BUILDER.build_player_values(
        rosters,players,prior,baselines,{},int(season)
    )
    assert external_exact_count==0
    out={}
    for pid,a in values.items():
        out[str(pid)]=float(GM.fsffl_league_value(a))
    return out,scoring_basis


def main():
    provider=STATE.HistoricalStateProvider()
    trades=[]
    skipped=[]

    for season,tid,ledger_counts in eligible_ledger_rows():
        try:
            data=provider.data(season)
            tx=ANALYSIS.find_trade(provider,season,tid)
            actions=ANALYSIS.transaction_actions(tx,data)
            values,scoring_basis=reconstructed_intrinsic_values(provider,season,tid,tx)
            users=sorted({
                str(a.get("from_user_id") or "") for a in actions
            } | {
                str(a.get("to_user_id") or "") for a in actions
            })
            users=[x for x in users if x]
            if len(users)!=2:
                raise RuntimeError(f"expected two participant users, got {users}")

            side_rows=[]
            for uid in users:
                sent,received=side_assets(actions,uid)
                missing=[x for x in sent+received if x not in values]
                if missing:
                    raise RuntimeError(f"missing reconstructed intrinsic values: {missing}")
                sent_vals=[values[x] for x in sent]
                received_vals=[values[x] for x in received]
                curves={
                    name:round(eff(received_vals,name)-eff(sent_vals,name),2)
                    for name in CURVES
                }
                side_rows.append({
                    "user_id":uid,
                    "sent_player_ids":sent,
                    "received_player_ids":received,
                    "sent_reconstructed_intrinsic_values":[round(x,2) for x in sent_vals],
                    "received_reconstructed_intrinsic_values":[round(x,2) for x in received_vals],
                    "curve_deltas":curves,
                })

            counts=sorted([len(x["sent_player_ids"]) for x in side_rows])
            if counts==[1,1]:
                evidence_role="ONE_FOR_ONE_INVARIANCE_CONTROL"
            elif counts[0]==counts[1]:
                evidence_role="EQUAL_COUNT_MULTI_ASSET_CONTROL"
            else:
                evidence_role="UNEQUAL_PACKAGE_CONCENTRATION_EVIDENCE"

            # Side deltas are equal/opposite on the same league-value coordinate.
            # Mean absolute distance is a descriptive clearing-distance proxy only.
            distance={
                name:round(statistics.mean(abs(x["curve_deltas"][name]) for x in side_rows),2)
                for name in CURVES
            }

            if evidence_role=="ONE_FOR_ONE_INVARIANCE_CONTROL":
                for name in ("mild","center","strong"):
                    if abs(distance[name]-distance["additive"])>0.01:
                        raise AssertionError(f"{season}/{tid}: one-for-one invariance violated")

            trades.append({
                "season":int(season),
                "transaction_id":tid,
                "trade_time_ms":int(tx.get("created") or 0),
                "package_counts":counts,
                "evidence_role":evidence_role,
                "scoring_basis":scoring_basis,
                "historical_value_coordinate":{
                    "class":"FSFFL_RECONSTRUCTED_AT_TIME_INTRINSIC",
                    "external_market_source_used":False,
                    "current_player_values_used":False,
                    "current_model_methodology_applied_to_point_in_time_inputs":True,
                    "strict_out_of_sample_backtest_eligible":False,
                },
                "sides":side_rows,
                "absolute_clearing_distance":distance,
                "lowest_distance_curve":min(distance,key=distance.get),
            })
        except Exception as exc:
            skipped.append({"season":int(season),"transaction_id":tid,"reason":repr(exc)})

    informative=[x for x in trades if x["evidence_role"]=="UNEQUAL_PACKAGE_CONCENTRATION_EVIDENCE"]
    one_for_one=[x for x in trades if x["evidence_role"]=="ONE_FOR_ONE_INVARIANCE_CONTROL"]
    equal_multi=[x for x in trades if x["evidence_role"]=="EQUAL_COUNT_MULTI_ASSET_CONTROL"]

    def aggregate(rows):
        out={}
        for name in CURVES:
            vals=[float(x["absolute_clearing_distance"][name]) for x in rows]
            out[name]={
                "n":len(vals),
                "mean_absolute_clearing_distance":round(statistics.mean(vals),2) if vals else None,
                "median_absolute_clearing_distance":round(statistics.median(vals),2) if vals else None,
                "wins_lowest_distance":sum(x["lowest_distance_curve"]==name for x in rows),
            }
        return out

    informative_agg=aggregate(informative)
    controls_agg=aggregate(one_for_one+equal_multi)
    ranked=[
        name for name in CURVES
        if informative_agg[name]["mean_absolute_clearing_distance"] is not None
    ]
    ranked.sort(key=lambda name:informative_agg[name]["mean_absolute_clearing_distance"])

    payload={
        "schema_version":"1.1",
        "model_version":"FSFFL-Internal-Historical-Package-Pilot-1.1",
        "research_only":True,
        "production_behavior_changed":False,
        "coefficient_fit_performed":False,
        "production_prior_changed":False,
        "external_historical_market_source_used":False,
        "current_market_value_backfill_used":False,
        "completed_trade_implies_exact_fair_value":False,
        "pick_containing_trades_excluded_from_calibration":True,
        "pick_exclusion_reason":"No sufficiently clean historical pick-value coordinate is yet available without using a present-day fallback or restricted external source.",
        "eligible_player_only_trade_count":len(trades),
        "informative_unequal_package_trade_count":len(informative),
        "one_for_one_control_count":len(one_for_one),
        "equal_count_multi_asset_control_count":len(equal_multi),
        "skipped_trade_count":len(skipped),
        "curve_definitions":CURVES,
        "informative_aggregate":informative_agg,
        "control_aggregate":controls_agg,
        "descriptive_rank_on_unequal_packages":ranked,
        "trades":trades,
        "skipped":skipped,
        "interpretation_rule":(
            "This is directional reconstructed-at-time evidence, not a coefficient fit. "
            "Completed trades need not clear at exact equal value, the sample of unequal "
            "player-only packages is small, and current FSFFL methodology is being applied "
            "to point-in-time inputs. Use the result to challenge/narrow priors only when "
            "combined with structural regressions and additional commercially-permitted evidence."
        ),
        "next_gate":(
            "Build a historically defensible pick coordinate, expand the informative sample, "
            "use time-ordered holdouts/context stratification, and test whether any narrowed "
            "prior improves residual behavior without degrading controls."
        ),
    }

    OUT.parent.mkdir(parents=True,exist_ok=True)
    OUT.write_text(json.dumps(payload,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print(json.dumps({
        "eligible_player_only_trade_count":len(trades),
        "informative_unequal_package_trade_count":len(informative),
        "controls":len(one_for_one)+len(equal_multi),
        "skipped":len(skipped),
        "informative_aggregate":informative_agg,
        "descriptive_rank":ranked,
    },indent=2))

    assert payload["research_only"] is True
    assert payload["production_behavior_changed"] is False
    assert payload["coefficient_fit_performed"] is False
    assert payload["production_prior_changed"] is False
    assert payload["external_historical_market_source_used"] is False
    assert payload["current_market_value_backfill_used"] is False
    assert payload["pick_containing_trades_excluded_from_calibration"] is True
    assert payload["informative_unequal_package_trade_count"] >= 3
    assert payload["one_for_one_control_count"] >= 5


if __name__=="__main__":
    main()
