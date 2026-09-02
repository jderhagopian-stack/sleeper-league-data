#!/usr/bin/env python3
"""Independent synthetic invariance/monotonicity tests for coefficient governance.

These tests assert directional and structural behavior. They do not encode a
preferred trade, team, player, or Opportunity Engine ranking as ground truth.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "script"

def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

utility = load(SCRIPT / "decision_utility.py", "coef_test_utility")
weights = load(SCRIPT / "gm_state_weighting.py", "coef_test_weights")

def sim_row(
    *,
    points=0.0,
    wins=0.0,
    playoffs=0.0,
    title=0.0,
    opponent_title=0.0,
    future=0.0,
    liquidity=0.0,
    resilience=0.0,
    objective=None,
    authorization=None,
    user_id="A",
    team_name="Alpha",
):
    return {
        "focus_user_id": user_id,
        "team_name": team_name,
        "focus_delta": {
            "expected_points_for": points,
            "expected_wins": wins,
            "playoff_probability": playoffs,
            "championship_probability": title,
        },
        "buyer_championship_probability_delta": opponent_title,
        "league_reference": {
            "expected_points_for_mean": 100.0,
            "expected_wins_mean": 7.0,
            "playoff_probability_mean": 0.5,
            "championship_probability_mean": 1.0 / 12.0,
        },
        "strategic": {
            "baseline_team_market_redraft_value": 10000.0,
            "market_dynasty_delta": future,
            "liquidity_value_delta": liquidity,
            "resilience_value_delta": resilience,
            "objective_weights": objective or {
                "current": 0.4,
                "future": 0.35,
                "liquidity": 0.1,
                "resilience": 0.15,
            },
            "incremental_channel_authorization": authorization or {
                "liquidity": True,
                "resilience": True,
            },
        },
    }

def score(**kwargs):
    return utility.score(sim_row(**kwargs))["score"]

def assert_ge(a, b, msg):
    if a + 1e-9 < b:
        raise AssertionError(f"{msg}: {a} < {b}")

def assert_le(a, b, msg):
    if a - 1e-9 > b:
        raise AssertionError(f"{msg}: {a} > {b}")

def test_adding_future_value_cannot_hurt():
    base = score(future=0)
    richer = score(future=500)
    assert_ge(richer, base, "adding buyer future value reduced utility")

def test_adding_future_cost_cannot_help():
    base = score(future=0)
    costly = score(future=-500)
    assert_le(costly, base, "adding buyer cost improved utility")

def test_larger_current_production_gain_cannot_hurt_in_equivalent_context():
    # Move all four correlated current outcomes in the same favorable direction,
    # preserving the current median aggregator's intended monotonic semantics.
    small = score(points=5, wins=.2, playoffs=.02, title=.005)
    large = score(points=10, wins=.4, playoffs=.04, title=.01)
    assert_ge(large, small, "larger current competitive gain reduced utility")

def test_opponent_title_gain_cannot_improve_focal_current_utility():
    no_externality = score(title=.01, opponent_title=0.0)
    with_externality = score(title=.01, opponent_title=.01)
    assert_le(with_externality, no_externality, "opponent title gain improved focal utility")

def test_disabled_channels_cannot_consume_weight_mass():
    row = sim_row(
        future=100,
        liquidity=10000,
        resilience=10000,
        authorization={"liquidity": False, "resilience": False},
    )
    scored = utility.score(row)
    if scored["objective_weights"]["liquidity"] != 0.0:
        raise AssertionError("disabled liquidity retained utility weight")
    if scored["objective_weights"]["resilience"] != 0.0:
        raise AssertionError("disabled resilience retained utility weight")
    if "liquidity" not in scored["suppressed_unauthorized_objective_weight"]:
        raise AssertionError("suppressed liquidity weight was not disclosed")
    if "resilience" not in scored["suppressed_unauthorized_objective_weight"]:
        raise AssertionError("suppressed resilience weight was not disclosed")

def test_team_identity_is_economically_invariant():
    a = utility.score(sim_row(future=250, user_id="A", team_name="Alpha"))
    b = utility.score(sim_row(future=250, user_id="B", team_name="Renamed Team"))
    if a["score"] != b["score"] or a["components"] != b["components"]:
        raise AssertionError("team/user identity changed decision economics")

def test_state_curve_is_continuous_at_descriptive_thresholds():
    cal = weights.load_calibration()
    anchors = cal.get("anchor_points") or []
    for threshold in (0.35, 0.55, 0.78):
        left = weights.interpolate(threshold - 1e-7, anchors)
        right = weights.interpolate(threshold + 1e-7, anchors)
        max_jump = max(abs(left[k] - right[k]) for k in weights.WEIGHT_KEYS)
        if max_jump > 1e-5:
            raise AssertionError(f"state weight curve has economic cliff at {threshold}: {max_jump}")

def test_state_curve_direction_matches_declared_prior_shape():
    cal = weights.load_calibration()
    anchors = cal.get("anchor_points") or []
    grid = [i / 100 for i in range(101)]
    rows = [weights.interpolate(x, anchors) for x in grid]
    for a, b in zip(rows, rows[1:]):
        if b["current"] + 1e-10 < a["current"]:
            raise AssertionError("current weight falls as competitive strength rises")
        if b["future"] - 1e-10 > a["future"]:
            raise AssertionError("future weight rises as competitive strength rises")

def test_utility_has_no_negotiation_or_behavior_incremental_weight():
    scored = utility.score(sim_row())
    if scored.get("negotiation_plausibility_incremental_weight") != 0.0:
        raise AssertionError("negotiation plausibility leaked into franchise utility")
    if scored.get("composite_strategic_and_break_glass_incremental_weight") != 0.0:
        raise AssertionError("duplicate composite utility received incremental weight")

def main():
    tests = [
        test_adding_future_value_cannot_hurt,
        test_adding_future_cost_cannot_help,
        test_larger_current_production_gain_cannot_hurt_in_equivalent_context,
        test_opponent_title_gain_cannot_improve_focal_current_utility,
        test_disabled_channels_cannot_consume_weight_mass,
        test_team_identity_is_economically_invariant,
        test_state_curve_is_continuous_at_descriptive_thresholds,
        test_state_curve_direction_matches_declared_prior_shape,
        test_utility_has_no_negotiation_or_behavior_incremental_weight,
    ]
    for test in tests:
        test()
    print({"passed": True, "test_count": len(tests), "ground_truth_source": "synthetic_structural_invariants"})

if __name__ == "__main__":
    main()
