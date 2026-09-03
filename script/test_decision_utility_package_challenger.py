#!/usr/bin/env python3
"""Regression tests for the non-production package-concentration challenger."""
from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location(
    "package_challenger_under_test",
    SCRIPT / "decision_utility_package_challenger.py",
)
mod = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(mod)


def sim(sent, received):
    raw_delta = sum(x["market_dynasty"] for x in received) - sum(x["market_dynasty"] for x in sent)
    return {
        "focus_delta": {
            "expected_points_for": 0.0,
            "expected_wins": 0.0,
            "playoff_probability": 0.0,
            "championship_probability": 0.0,
        },
        "buyer_championship_probability_delta": 0.0,
        "league_reference": {
            "expected_points_for_mean": 1500.0,
            "expected_wins_mean": 7.0,
            "playoff_probability_mean": 0.5,
            "championship_probability_mean": 0.1,
        },
        "strategic": {
            "sent": sent,
            "received": received,
            "baseline_team_market_redraft_value": 40000.0,
            "market_dynasty_delta": raw_delta,
            "market_redraft_delta": 0.0,
            "liquidity_value_delta": 0.0,
            "resilience_value_delta": 0.0,
            "objective_weights": {
                "current": 0.4,
                "future": 0.6,
                "liquidity": 0.0,
                "resilience": 0.0,
            },
            "incremental_channel_authorization": {
                "current": True,
                "future": True,
                "liquidity": False,
                "resilience": False,
            },
        },
    }


def asset(aid, value):
    return {"asset_id": aid, "name": aid, "market_dynasty": float(value)}


def test_one_for_one_is_unchanged():
    x = sim([asset("A", 4000)], [asset("B", 4500)])
    base = mod.BASE.score(x)
    ch = mod.score(x, "gm22_strong_bound")
    assert ch["package_concentration"]["concentration_residual_vs_additive"] == 0.0
    assert ch["score"] == base["score"]


def test_one_for_many_replaces_future_without_fifth_channel():
    x = sim([asset("ELITE", 4300)], [asset("A", 2400), asset("B", 1900)])
    base = mod.BASE.score(x)
    ch = mod.score(x, "legacy_mild_bound")
    assert base["primitive_blocks"]["future"] == 0.0
    assert ch["primitive_blocks"]["future"] < 0.0
    assert set(ch["components"]) == {"current", "future", "liquidity", "resilience"}
    assert ch["double_count_policy"]["raw_future_replaced_not_summed"] is True
    assert ch["double_count_policy"]["new_utility_channel_created"] is False


def test_fragmentation_penalty_increases_across_inherited_bounds():
    x = sim(
        [asset("ELITE", 4300)],
        [asset("A", 2200), asset("B", 1200), asset("C", 900)],
    )
    mild = mod.score(x, "legacy_mild_bound")
    mid = mod.score(x, "inherited_curve_midpoint")
    strong = mod.score(x, "gm22_strong_bound")
    assert mild["primitive_blocks"]["future"] > mid["primitive_blocks"]["future"]
    assert mid["primitive_blocks"]["future"] > strong["primitive_blocks"]["future"]


def test_consolidating_many_into_one_gets_symmetric_credit():
    x = sim(
        [asset("A", 2200), asset("B", 1200), asset("C", 900)],
        [asset("ELITE", 4300)],
    )
    mild = mod.score(x, "legacy_mild_bound")
    assert mild["primitive_blocks"]["future"] > 0.0
    assert mild["score_delta_vs_production"] > 0.0


def test_forced_cut_is_excluded_from_package_but_preserved_in_future():
    trade_sent = asset("player:ELITE", 4300)
    trade_a = asset("player:A", 2400)
    trade_b = asset("player:B", 2200)
    forced_cut = asset("player:CUT", 300)
    x = sim([trade_sent, forced_cut], [trade_a, trade_b])
    # Base strategic future is +0: 4600 received - 4600 sent, but the actual
    # negotiated trade itself is +300 before the forced cut.
    x["trade_actions"] = [
        {
            "type": "trade",
            "players": ["ELITE"],
            "picks": [],
        },
        {
            "type": "trade",
            "players": ["A", "B"],
            "picks": [],
        },
    ]
    mild = mod.score(x, "legacy_mild_bound")
    pkg = mild["package_concentration"]
    assert pkg["trade_asset_filter_applied"] is True
    assert pkg["raw_trade_package_future_value"] == 300.0
    assert pkg["non_trade_future_value_preserved"] == -300.0
    assert pkg["automatic_cuts_excluded_from_package_concentration"] is True
    assert pkg["non_trade_future_effects_preserved_exactly_once"] is True
    assert pkg["package_effective_future_value"] < 0.0


if __name__ == "__main__":
    test_one_for_one_is_unchanged()
    test_one_for_many_replaces_future_without_fifth_channel()
    test_fragmentation_penalty_increases_across_inherited_bounds()
    test_consolidating_many_into_one_gets_symmetric_credit()
    test_forced_cut_is_excluded_from_package_but_preserved_in_future()
    print("package concentration challenger regression passed")
