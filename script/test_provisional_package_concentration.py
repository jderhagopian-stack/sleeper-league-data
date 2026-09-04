#!/usr/bin/env python3
"""Regression tests for governed package concentration authority."""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SCRIPT=ROOT/"script"


def load(path,name):
    spec=importlib.util.spec_from_file_location(name,path)
    mod=importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


PKG=load(SCRIPT/"package_concentration.py","pkg_prod_test")
DU=load(SCRIPT/"decision_utility.py","du_prod_test")


def sim(sent, received, market_delta, trade_players):
    return {
        "strategic":{
            "sent":sent,
            "received":received,
            "market_dynasty_delta":market_delta,
            "objective_weights":{"current":0.0,"future":1.0,"liquidity":0.0,"resilience":0.0},
            "incremental_channel_authorization":{"liquidity":False,"resilience":False},
        },
        "focus_delta":{},
        "league_reference":{},
        "trade_actions":[{"type":"trade","players":trade_players,"picks":[]}],
    }


def row(pid,val,name=None):
    return {"asset_id":f"player:{pid}","name":name or f"P{pid}","market_dynasty":float(val)}


def main():
    prior=PKG.PRIOR
    assert prior["authority_mode"]=="ACTIVE_EMPIRICALLY_SUPPORTED_STANDARD"
    assert prior["active_curve"]=="strong"
    assert prior["empirically_supported"] is True
    assert prior["empirically_calibrated"] is False
    assert prior["calibration_status"]=="SUPPORTED_BUT_NOT_POINT_OPTIMIZED"
    assert "research_challengers" not in prior
    assert prior["invariants"]["research_challenger_cannot_self_promote"] is True

    nontrade=sim([row(1,300)],[row(2,100),row(3,100),row(4,100)],0,[1,2,3,4])
    nontrade["trade_actions"]=[]
    unchanged=PKG.transform_future_value(nontrade,"center")
    assert unchanged["package_effective_future_value"] == 0.0
    assert unchanged["package_transform_applied"] is False
    assert unchanged["non_trade_future_value_preserved"] == 0.0

    compat=sim(
        [row(1,300),row(9,80,"AUTO CUT")],
        [row(2,200),row(3,150)],
        -30,
        [1,2,3],
    )
    compat["effective_actions"]=[
        {"type":"trade","from_user_id":"u","to_user_id":"v","players":[1],"picks":[]},
        {"type":"trade","from_user_id":"v","to_user_id":"u","players":[2,3],"picks":[]},
        {"type":"cut","user_id":"u","players":[9]},
    ]
    compat["trade_actions"]=[]
    compat_center=PKG.transform_future_value(compat,"center")
    assert compat_center["package_transform_applied"] is True
    assert compat_center["trade_action_source"]=="effective_actions_filtered_to_trade"
    assert compat_center["raw_trade_package_future_value"]==50.0
    assert compat_center["non_trade_future_value_preserved"]==-80.0
    assert all(x["asset_id"]!="player:9" for x in compat_center["sent_parts"])

    one=sim([row(1,100)],[row(2,100)],0,[1,2])
    for curve in ("mild","center","strong"):
        out=PKG.transform_future_value(one,curve)
        assert out["package_effective_future_value"] == 0.0
        assert out["concentration_residual_vs_additive"] == 0.0

    frag=sim([row(1,300)],[row(2,100),row(3,100),row(4,100)],0,[1,2,3,4])
    vals={c:PKG.transform_future_value(frag,c)["package_effective_future_value"] for c in ("mild","center","strong")}
    assert vals["mild"] < 0 and vals["center"] < 0 and vals["strong"] < 0
    assert vals["strong"] < vals["center"] < vals["mild"]

    cut=sim(
        [row(1,300),row(9,80,"AUTO CUT")],
        [row(2,200),row(3,150)],
        -30,
        [1,2,3],
    )
    center=PKG.transform_future_value(cut,"center")
    assert center["raw_trade_package_future_value"] == 50.0
    assert center["non_trade_future_value_preserved"] == -80.0
    assert center["automatic_cuts_excluded_from_package_concentration"] is True
    assert center["non_trade_future_effects_preserved_exactly_once"] is True
    assert all(x["asset_id"] != "player:9" for x in center["sent_parts"])

    scored=DU.score(frag)
    assert scored["model_version"] == "FSFFL-Shared-Decision-Utility-2.2"
    assert scored["primitive_blocks"]["future"] == vals["strong"]
    assert scored["package_concentration_prior_scores"]["strong"] == scored["score"]
    assert scored["package_concentration_prior_range_decision_robustness"] == "ROBUST_NEGATIVE_ACROSS_PRIOR_RANGE"
    diag=scored["diagnostics"]
    assert diag["package_concentration_active_curve"] == "strong"
    assert diag["package_concentration_authority"] == "ACTIVE_EMPIRICALLY_SUPPORTED_STANDARD"
    assert diag["package_concentration_empirically_supported"] is True
    assert diag["package_concentration_empirically_calibrated"] is False
    assert diag["package_concentration_calibration_status"]=="SUPPORTED_BUT_NOT_POINT_OPTIMIZED"
    assert diag["package_concentration_replaces_future_additivity"] is True
    assert diag["package_concentration_new_channel_created"] is False
    assert diag["package_concentration"]["commercial_provenance"]["material_external_calibration_dependency"] is False

    print({
        "active_curve":"strong",
        "nontrade_consumer_unchanged":True,
        "effective_actions_trade_compatibility":True,
        "one_for_one_invariant":True,
        "fragmentation_strong_future":vals["strong"],
        "forced_cut_preserved":center["non_trade_future_value_preserved"],
        "prior_scores":scored["package_concentration_prior_scores"],
        "robustness":scored["package_concentration_prior_range_decision_robustness"],
        "research_challengers_separated_from_production_prior":True,
    })


if __name__=="__main__":
    main()
