#!/usr/bin/env python3
"""Current-state downstream decision bake-off: FantasyCalc vs Stats Guy player anchor.

This is not a historical winner test. The repository lacks contemporaneous
historical FantasyCalc boards, so no pseudo-backtest is manufactured. Instead
we isolate provider sensitivity: preserve every existing FSFFL owner/team
adjustment and replace only each matched player's dynasty market anchor with a
Stats Guy rank/relative-value ordering mapped onto the existing FSFFL value
distribution. Then run the same governed opportunity engine for one
representative team per competitive state.
"""
from __future__ import annotations

import copy
import json
import urllib.request
from pathlib import Path

import build_fsffl_gm_engine as core
import nonprojection_high_priority_overrides as hp
import package_curve_robustness as robust

ROOT=Path(__file__).resolve().parents[1]
FC=ROOT/"data"/"market_values_fantasycalc.json"
OUT=ROOT/"data"/"audit"/"statsguy_player_market_decision_bakeoff.json"
API="https://api.statsguyfantasy.com/api/v1/players"


def fetch_statsguy():
    req=urllib.request.Request(API,headers={"User-Agent":"FSFFL-player-market-decision-bakeoff/1.1","Accept":"application/json"})
    with urllib.request.urlopen(req,timeout=30) as r:
        raw=json.load(r)
    out={}
    for row in raw.get("players") or []:
        sid=str(row.get("id") or "")
        val=core.safe_float((row.get("value") or {}).get("sf_dynasty"))
        if sid and val>0: out[sid]=val
    return out


def normalized_values():
    """Map Stats Guy ordering onto the observed FSFFL/FantasyCalc value distribution.

    A single multiplicative scale is inappropriate because provider value curves
    have different shapes. Quantile mapping keeps the existing cross-sectional
    value distribution exactly while allowing Stats Guy's relative ordering to
    move players within it. This uses no fitted economic coefficient and avoids
    treating FantasyCalc's raw player-specific values as an answer key.
    """
    fc=json.loads(FC.read_text())
    sg=fetch_statsguy()
    rows={str(r.get("sleeper_id")):r for r in fc.get("dynasty") or [] if r.get("sleeper_id") and core.safe_float(r.get("value"))>0}
    common=[sid for sid in rows if sid in sg and sg[sid]>0]
    if not common: raise SystemExit("No overlapping FantasyCalc/Stats Guy players")
    fc_distribution=sorted(core.safe_float(rows[sid]["value"]) for sid in common)
    sg_order=sorted(common,key=lambda sid:(sg[sid],sid))
    mapped={sid:fc_distribution[i] for i,sid in enumerate(sg_order)}
    return rows,mapped,len(common)


def representative_uids(ctx):
    selected={}
    for uid in sorted(map(str,ctx.get("owners") or {})):
        state,_=core._u_team_objective_weights((ctx.get("teams") or {}).get(uid,{}))
        selected.setdefault(state,uid)
    return [selected[k] for k in sorted(selected)]


def profiles(ctx):
    return {uid:core._u_profile_map(core.build_strategic_asset_profiles_for_team(uid,ctx)) for uid in sorted(map(str,ctx.get("owners") or {}))}


def summary(payload,n=10):
    rows=[x for x in payload.get("opportunities") or [] if x.get("best_candidate_packages")][:n]
    targets=[str(x.get("target_asset_id") or "") for x in rows]
    packages=[]
    for x in rows:
        p=(x.get("best_candidate_packages") or [{}])[0]
        packages.append((str(x.get("target_asset_id") or ""),tuple(sorted(map(str,p.get("focal_outgoing_asset_ids") or [])))))
    return {"targets":targets,"packages":packages,"bands":[str(x.get("best_package_recommendation_band") or "") for x in rows],"rows":[{"target_asset_id":x.get("target_asset_id"),"target_player":x.get("target_player"),"band":x.get("best_package_recommendation_band"),"best_package":(x.get("best_candidate_packages") or [{}])[0].get("focal_outgoing_asset_ids")} for x in rows]}


def compare(a,b):
    denom=max(1,min(len(a["targets"]),len(b["targets"])))
    pden=max(1,min(len(a["packages"]),len(b["packages"])))
    return {
        "same_top_target":bool(a["targets"] and b["targets"] and a["targets"][0]==b["targets"][0]),
        "same_top_package":bool(a["packages"] and b["packages"] and a["packages"][0]==b["packages"][0]),
        "top10_target_overlap":round(len(set(a["targets"])&set(b["targets"]))/denom,4),
        "top10_package_overlap":round(len(set(map(str,a["packages"]))&set(map(str,b["packages"])))/pden,4),
        "band_sequence_identical":a["bands"]==b["bands"],
    }


def apply_statsguy(ctx,sg_norm):
    out=copy.deepcopy(ctx)
    changed=0
    for aid,meta in out["player_meta"].items():
        if not aid.startswith("player:"): continue
        pid=aid.split(":",1)[1]
        new=core.safe_float(sg_norm.get(pid)); old=core.safe_float(meta.get("market_dynasty"))
        if new<=0 or old<=0: continue
        ratio=new/old
        meta["market_dynasty"]=new
        if aid in out["asset_meta"]: out["asset_meta"][aid]["market_dynasty"]=new
        for uid,vals in out["owner_vals"].items():
            if core.safe_float(vals.get(aid))>0: vals[aid]=core.safe_float(vals[aid])*ratio
        changed+=1
    out["_profile_cache"]={}; out["_depth_cache"]={}
    return out,changed


def main():
    fc_rows,sg_norm,matched=normalized_values()
    hp.install(core)
    robust.install(core)
    base=core._u_load_context()
    challenger,changed=apply_statsguy(base,sg_norm)
    uids=representative_uids(base)
    bp=profiles(base); sp=profiles(challenger)
    team_rows=[]
    for uid in uids:
        state,_=core._u_team_objective_weights((base.get("teams") or {}).get(uid,{}))
        b=summary(core.build_universal_trade_opportunities(uid,ctx=base,profile_by_uid=bp))
        s=summary(core.build_universal_trade_opportunities(uid,ctx=challenger,profile_by_uid=sp))
        team_rows.append({"user_id":uid,"team":((base.get("owners") or {}).get(uid) or {}).get("team_name"),"objective_state":state,"fantasycalc":b,"statsguy":s,"comparison":compare(b,s)})

    diffs=[]
    for sid,row in fc_rows.items():
        if sid not in sg_norm: continue
        fcval=core.safe_float(row.get("value")); sgval=core.safe_float(sg_norm[sid])
        if fcval<=0: continue
        diffs.append({"player_id":sid,"name":row.get("name"),"position":row.get("position"),"fantasycalc":round(fcval,1),"statsguy_quantile_mapped":round(sgval,1),"pct_difference":round((sgval/fcval-1)*100,2)})
    diffs.sort(key=lambda x:abs(x["pct_difference"]),reverse=True)
    comps=[x["comparison"] for x in team_rows]
    payload={
        "model_version":"FSFFL-StatsGuy-Player-Market-Decision-Bakeoff-1.1",
        "historical_winner_test":False,
        "reason":"No contemporaneous historical FantasyCalc boards are available in-repo; current FantasyCalc is not treated as ground truth.",
        "design":"Replace only the player dynasty market ordering/relative placement; preserve the existing cross-sectional value distribution, owner-specific adjustment ratios, redraft/current-season inputs, pick treatment, package governance and all other FSFFL logic.",
        "normalization":{"method":"nonparametric quantile mapping: Stats Guy ordering assigned the sorted current FSFFL/FantasyCalc values across the same overlapping players","manual_coefficient":None,"preserves_current_cross_sectional_value_distribution":True,"matched_current_players":matched,"player_assets_changed_in_context":changed},
        "summary":{"representative_states":len(team_rows),"top_target_flips":sum(not x["same_top_target"] for x in comps),"top_package_flips":sum(not x["same_top_package"] for x in comps),"minimum_top10_target_overlap":min([x["top10_target_overlap"] for x in comps] or [1.0]),"minimum_top10_package_overlap":min([x["top10_package_overlap"] for x in comps] or [1.0]),"provider_change_is_decision_relevant":any((not x["same_top_target"]) or x["top10_target_overlap"]<0.8 for x in comps)},
        "teams":team_rows,
        "largest_current_provider_disagreements":diffs[:30],
        "interpretation":{"different_decisions_are_not_automatically_bad":True,"fantasycalc_is_regression_reference_not_answer_key":True,"production_promotion_requires_no_pathological_downstream_behavior_and_clear_provenance":True,"no_projection_behavior_changed":True,"no_hand_set_conversion_coefficient":True,"first_median_scale_attempt_rejected_as_distribution_shape_artifact":True},
    }
    OUT.parent.mkdir(parents=True,exist_ok=True); OUT.write_text(json.dumps(payload,indent=2,sort_keys=True)+"\n")
    print(json.dumps(payload["summary"],indent=2,sort_keys=True))

if __name__=="__main__":main()
