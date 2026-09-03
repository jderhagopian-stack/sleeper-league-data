#!/usr/bin/env python3
"""Regression: user-facing trade language must follow authoritative decision value.

A multi-asset trade can have positive raw additive market value while governed
package-adjusted FUTURE ASSET VALUE is negative. Reports must never present the
raw additive reference as the model's authoritative long-term verdict.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPT=Path(__file__).resolve().parent


def load(path,name):
    spec=importlib.util.spec_from_file_location(name,path)
    mod=importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name]=mod
    spec.loader.exec_module(mod)
    return mod


CTX=load(SCRIPT/"trade_report_context.py","trade_report_context_authority_test")
RUN=load(SCRIPT/"run_trade_report.py","run_trade_report_authority_test")


def row():
    return {
        "shared_decision_utility_score":-515.26,
        "decision_attribution":{
            "final_shared_decision_utility":-515.26,
            "channels":[
                {"channel":"current","primitive_value":-917.0},
                {"channel":"future","primitive_value":-64.07},
                {"channel":"liquidity","primitive_value":0.0},
                {"channel":"resilience","primitive_value":0.0},
            ],
            "package_concentration_prior_scores":{
                "mild":-343.13,
                "center":-515.26,
                "strong":-687.37,
            },
            "package_concentration_prior_range_decision_robustness":"ROBUST_NEGATIVE_ACROSS_PRIOR_RANGE",
            "diagnostics":{
                "package_concentration":{
                    "package_transform_applied":True,
                    "raw_additive_future_value":840.10,
                    "raw_trade_package_future_value":1192.10,
                    "package_effective_trade_future_value":287.93,
                    "non_trade_future_value_preserved":-352.0,
                    "package_effective_future_value":-64.07,
                }
            },
        },
        "simulation":{
            "focus_delta":{
                "expected_wins":0.10,
                "playoff_probability":0.021,
                "championship_probability":0.0056,
            },
            "strategic":{
                "market_dynasty_delta":840.10,
                "liquidity_value_delta":0.0,
                "strategic_value_delta":9999.0,
            },
            "roster_resolution":{"focus":{"required_cuts":1}},
        },
    }


def main():
    cur=row()
    report={
        "recommended_next_action":"DECLINE",
        "current_offer_evaluation":cur,
        "focus_user_id":"focus",
        "suggested_counteroffers":[],
        "market_sweep_alternatives":[],
        "simulation":{"adaptive_confirmation":{"triggered":False}},
    }

    profile=CTX.recommendation_profile(report)
    assert profile["label"]=="DECLINE"
    assert profile["package_prior"]["robustness"]=="ROBUST_NEGATIVE_ACROSS_PRIOR_RANGE"
    assert "stays negative" in profile["basis"].lower()

    short=RUN.summary(report)
    assert "Future Asset Value -64" in short, short
    assert "overall decision value -515" in short, short
    assert "+840" not in short, short
    assert "9,999" not in short, short
    assert "stays negative" in short.lower(), short

    # Verify the new value-context contract independently of live standings/pick
    # enrichment: authoritative future and raw additive reference remain distinct.
    future=CTX._decision_channel(cur,"future")
    prior=CTX._package_prior_profile(cur)
    assert float(future["primitive_value"])==-64.07
    assert prior["center_score"]==-515.26
    assert prior["robustness"]=="ROBUST_NEGATIVE_ACROSS_PRIOR_RANGE"

    print({
        "authoritative_future_asset_value":future["primitive_value"],
        "raw_additive_reference":cur["simulation"]["strategic"]["market_dynasty_delta"],
        "overall_decision_value":cur["decision_attribution"]["final_shared_decision_utility"],
        "short_answer":short,
        "profile":profile,
    })


if __name__=="__main__":
    main()
