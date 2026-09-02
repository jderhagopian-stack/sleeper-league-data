#!/usr/bin/env python3
"""Regression coverage for Shared Decision Utility current-season evidence reconciliation."""
from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("decision_utility_under_test", SCRIPT / "decision_utility.py")
mod = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(mod)


def base_sim():
    return {
        "focus_delta": {
            "expected_points_for": 8.16,
            "expected_wins": 0.10,
            "playoff_probability": 0.021,
            "championship_probability": 0.00564,
        },
        "buyer_championship_probability_delta": 0.0,
        "league_reference": {
            "expected_points_for_mean": 1515.0,
            "expected_wins_mean": 7.0,
            "playoff_probability_mean": 0.5,
            "championship_probability_mean": 0.12,
        },
        "strategic": {
            "baseline_team_market_redraft_value": 43286.0,
            "market_dynasty_delta": 840.1,
            "market_redraft_delta": -917.0,
            "liquidity_value_delta": 0.0,
            "resilience_value_delta": 0.0,
            "optionality_value_delta": -2392.13,
            "objective_weights": {
                "current": 0.396136,
                "future": 0.352727,
                "liquidity": 0.101136,
                "resilience": 0.15,
            },
            "incremental_channel_authorization": {
                "current": True,
                "future": True,
                "liquidity": False,
                "resilience": False,
            },
        },
        "roster_diagnosis": {
            "before": {"starter_redraft_value": 38921.0},
            "after": {"starter_redraft_value": 37383.0},
        },
    }


def test_conflicting_current_evidence_is_reconciled():
    sim = base_sim()
    blocks = mod.primitive_blocks(sim)
    diag = blocks["diagnostics"]
    evidence = diag["current_value_evidence"]
    assert set(evidence) == {
        "simulator_outcome_value",
        "transaction_market_redraft_delta",
        "optimized_starter_redraft_delta",
    }
    assert evidence["simulator_outcome_value"] > 0
    assert evidence["transaction_market_redraft_delta"] < 0
    assert evidence["optimized_starter_redraft_delta"] < 0
    assert blocks["current"] == -917.0
    scored = mod.score(sim)
    assert scored["score"] < 0
    assert scored["diagnostics"]["optionality_incremental_value_authorized"] is False


def test_missing_direct_redraft_evidence_preserves_simulator_fallback():
    sim = base_sim()
    sim["strategic"].pop("market_redraft_delta")
    sim.pop("roster_diagnosis")
    blocks = mod.primitive_blocks(sim)
    evidence = blocks["diagnostics"]["current_value_evidence"]
    assert set(evidence) == {"simulator_outcome_value"}
    assert abs(blocks["current"] - evidence["simulator_outcome_value"]) < 0.01


def test_coherent_positive_current_evidence_stays_positive():
    sim = base_sim()
    sim["strategic"]["market_redraft_delta"] = 600.0
    sim["roster_diagnosis"]["after"]["starter_redraft_value"] = 39521.0
    blocks = mod.primitive_blocks(sim)
    assert blocks["current"] > 0


def test_no_player_specific_or_fit_threshold_is_required():
    sim = base_sim()
    sim["strategic"]["market_redraft_delta"] = -100.0
    sim["roster_diagnosis"]["before"]["starter_redraft_value"] = 1000.0
    sim["roster_diagnosis"]["after"]["starter_redraft_value"] = 800.0
    blocks = mod.primitive_blocks(sim)
    assert blocks["diagnostics"]["current_value_aggregation"] == "UNWEIGHTED_MEDIAN_SAME_UNIT_EVIDENCE"
    assert blocks["diagnostics"]["current_value_evidence_count"] == 3


if __name__ == "__main__":
    test_conflicting_current_evidence_is_reconciled()
    test_missing_direct_redraft_evidence_preserves_simulator_fallback()
    test_coherent_positive_current_evidence_stays_positive()
    test_no_player_specific_or_fit_threshold_is_required()
    print("decision utility current-evidence regression passed")
