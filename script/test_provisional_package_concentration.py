#!/usr/bin/env python3
"""Regression tests for bounded provisional package concentration authority."""
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
    # Shared utility consumers without explicit trade actions must remain additive.
    nontrade=sim([row(1,300)],[row(2,100),row(3,100),row(4,100)],0,[1,2,3,4])
    nontrade["trade_actions"]=[]
    unchanged=PKG.transform_future_value(nontrade,"center")
    assert unchanged["package_effective_future_value"] == 0.0
    assert unchanged["package_transform_applied"] is False
    assert unchanged["non_trade_future_value_preserved"] == 0.0

    # One-for-one must be invariant under every prior curve.
    one=sim([row(1,100)],[row(2,100)],0,[1,2])
    for curve in ("mild","center","strong"):
        out=PKG.transform_future_value(one,curve)
        assert out["package_effective_future_value"] == 0.0
        assert out["concentration_residual_vs_additive"] == 0.0

    # Equal additive 1-for-3 fragmentation must become worse, not better.
    frag=sim([row(1,300)],[row(2,100),row(3,100),row(4,100)],0,[1,2,3,4])
    vals={c:PKG.transform_future_value(frag,c)["package_effective_future_value"] for c in ("mild","center","strong")}
    assert vals["mild"] < 0 and vals["center"] < 0 and vals["strong"] < 0
    assert vals["strong"] < vals["center"] < vals["mild"]

    # Automatic cut is in strategic sent rows but not in negotiated trade_actions.
    # It must be preserved once as non-trade future value and never concentration-discounted.
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
    assert scored["primitive_blocks"]["future"] == vals["center"]
    assert scored["package_concentration_prior_scores"]["center"] == scored["score"]
    assert scored["package_concentration_prior_range_decision_robustness"] == "ROBUST_NEGATIVE_ACROSS_PRIOR_RANGE"
    diag=scored["diagnostics"]
    assert diag["package_concentration_authority"] == "ACTIVE_BOUNDED_PROVISIONAL_PRIOR"
    assert diag["package_concentration_empirically_calibrated"] is False
    assert diag["package_concentration_replaces_future_additivity"] is True
    assert diag["package_concentration_new_channel_created"] is False
    assert diag["package_concentration"]["commercial_provenance"]["material_external_calibration_dependency"] is False

    print({
        "nontrade_consumer_unchanged":True,
        "one_for_one_invariant":True,
        "fragmentation_center_future":vals["center"],
        "forced_cut_preserved":center["non_trade_future_value_preserved"],
        "prior_scores":scored["package_concentration_prior_scores"],
        "robustness":scored["package_concentration_prior_range_decision_robustness"],
    })


if __name__=="__main__":
    main()
