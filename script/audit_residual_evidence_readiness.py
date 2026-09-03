#!/usr/bin/env python3
"""Assess what residual parameter families can responsibly advance with evidence already in-repo."""
from __future__ import annotations
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"data/audit/residual_evidence_readiness.json"

def exists(path):
    return (ROOT/path).is_file()

def main():
    files={
        "trade_ledger":exists("data/trade_ledger.json"),
        "player_trade_instances":exists("data/player_trade_instances.json"),
        "transaction_performance":exists("data/transaction_performance_index.json"),
        "current_market_snapshot":exists("data/market_values_fantasycalc.json"),
        "historical_reconstruction_registry":exists("data/historical_gm3/reconstruction_parameter_registry.json"),
        "historical_single_case":exists("data/historical_gm3/sources/2023-04-10-josh-allen.json"),
        "simulator":exists("script/run_fsffl_season_simulator_preproduction.py"),
        "roster_fragility":exists("data/roster_fragility_index.json"),
        "package_challenger":exists("script/decision_utility_package_challenger.py"),
    }

    families=[
        {
            "family_id":"PACKAGE-CONCENTRATION-RESIDUAL-001",
            "concept_evidence_ready":True,
            "bounded_challenger_ready":True,
            "point_estimate_ready":False,
            "why":"Completed one-for-many trade topology exists and two inherited package curves provide an explicit uncertainty envelope. Full frozen contemporaneous package-value history and rejected/choice-frontier evidence are still incomplete.",
            "next_action":"Continue challenger/regression use; accept no claim that either inherited curve is the true coefficient.",
        },
        {
            "family_id":"OPTIONALITY-RESIDUAL-001",
            "concept_evidence_ready":True,
            "bounded_challenger_ready":False,
            "point_estimate_ready":False,
            "why":"Current optionality diagnostics mix distinct age/pedigree signals with market-derived spread/trend. The repo lacks a sufficiently broad frozen multi-horizon market-value panel needed to residualize optionality against current market value.",
            "next_action":"Wait for/consume historical reconstruction outputs that provide dated market anchors and future horizons; do not activate legacy optionality.",
        },
        {
            "family_id":"LIQUIDITY-RESIDUAL-001",
            "concept_evidence_ready":True,
            "bounded_challenger_ready":False,
            "point_estimate_ready":False,
            "why":"Completed trade history exists, but an accepted-trade ledger alone lacks the opportunity/rejection denominator required to distinguish true asset liquidity from owner behavior and market value.",
            "next_action":"Build exposure/choice denominators from offers or defensible opportunity sets before fitting asset-level liquidity residuals.",
        },
        {
            "family_id":"RESILIENCE-RESIDUAL-001",
            "concept_evidence_ready":True,
            "bounded_challenger_ready":False,
            "point_estimate_ready":False,
            "why":"Current Simulator and fragility data can generate stress diagnostics, but converting hypothetical depth protection into expected economic value requires calibrated stress probabilities/horizon or an explicit risk-preference parameter. Current-season replacement effects are already modeled.",
            "next_action":"Use simulation ablation for diagnostics and historical availability data for stress probabilities; do not add a second current-season resilience value.",
        },
    ]
    payload={
        "schema_version":"1.0",
        "audit_family":"residual evidence readiness",
        "production_behavior_changed":False,
        "files":files,
        "families":families,
        "summary":{
            "bounded_challenger_ready":[x["family_id"] for x in families if x["bounded_challenger_ready"]],
            "point_estimate_ready":[x["family_id"] for x in families if x["point_estimate_ready"]],
            "safe_to_activate_legacy_formula":[],
        },
        "central_finding":"Available evidence supports advancing package concentration as a bounded non-production challenger now. It does not yet justify directly activating legacy optionality, liquidity, or resilience formulas.",
    }
    OUT.parent.mkdir(parents=True,exist_ok=True)
    OUT.write_text(json.dumps(payload,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print(json.dumps(payload["summary"],indent=2))
    assert payload["summary"]["bounded_challenger_ready"]==["PACKAGE-CONCENTRATION-RESIDUAL-001"]
    assert payload["summary"]["point_estimate_ready"]==[]

if __name__=="__main__":
    main()
