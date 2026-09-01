#!/usr/bin/env python3
"""Permanent end-to-end decision-path integration audit.

Guards composition invariants that component-level governance tests can miss.
This audit intentionally combines runtime-like synthetic reconciliation with
production-source authority checks.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "script"


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def synthetic_sim(*, title=.02, buyer=.01, liquidity=500, resilience=400,
                  authorize_liquidity=False, authorize_resilience=False):
    return {
        "focus_delta": {
            "expected_wins": .4,
            "expected_points_for": 35,
            "playoff_probability": .04,
            "championship_probability": title,
        },
        "league_reference": {
            "expected_wins_mean": 7.0,
            "expected_points_for_mean": 1500.0,
            "playoff_probability_mean": .5,
            "championship_probability_mean": 1/12,
        },
        "buyer_championship_probability_delta": buyer,
        "net_title_equity_swing_against_focus": buyer-title,
        "strategic": {
            "baseline_team_market_redraft_value": 24000,
            "market_dynasty_delta": 800,
            "liquidity_value_delta": liquidity,
            "resilience_value_delta": resilience,
            "incremental_channel_authorization": {
                "current": True,
                "future": True,
                "liquidity": authorize_liquidity,
                "resilience": authorize_resilience,
            },
            "objective_weights": {
                "current": .40,
                "future": .35,
                "liquidity": .10,
                "resilience": .15,
            },
        },
    }


def main():
    utility = load(SCRIPT/"decision_utility.py", "integration_audit_utility")
    attribution = load(SCRIPT/"decision_attribution.py", "integration_audit_attribution")

    # Disabled channels cannot consume final-utility weight mass.
    scored = utility.score(synthetic_sim())
    assert scored["incremental_channel_authorization"]["liquidity"] is False
    assert scored["incremental_channel_authorization"]["resilience"] is False
    assert scored["objective_weights"]["liquidity"] == 0
    assert scored["objective_weights"]["resilience"] == 0
    assert abs(scored["objective_weights"]["current"] + scored["objective_weights"]["future"] - 1) < 1e-6
    assert scored["components"]["liquidity"] == 0
    assert scored["components"]["resilience"] == 0

    # Attribution must reconcile exactly to the one authoritative score.
    a = attribution.reconcile(synthetic_sim())
    assert a["reconciles"] is True
    assert a["creates_independent_score"] is False
    assert abs(a["component_sum"] - a["final_shared_decision_utility"]) <= a["reconciliation_tolerance"]

    # Opponent title externality must materially alter the current primitive.
    no_externality = utility.primitive_blocks(synthetic_sim(buyer=0))["current"]
    with_externality = utility.primitive_blocks(synthetic_sim(buyer=.03))["current"]
    assert with_externality != no_externality

    ti = (SCRIPT/"run_team_improvement_lab_v16.py").read_text(encoding="utf-8")
    trade_base = (SCRIPT/"run_trade_market_sweep_v20.py").read_text(encoding="utf-8")
    trade_gov = (SCRIPT/"trade_option_governance.py").read_text(encoding="utf-8")
    roster = (SCRIPT/"run_roster_decision_lab.py").read_text(encoding="utf-8")
    oe = (SCRIPT/"opportunity_engine"/"application_v21.py").read_text(encoding="utf-8")

    # Team Improvement must propagate league context and competitive externality.
    assert "'league_reference':league_reference" in ti
    assert "'net_title_equity_swing_against_focus':round(net_swing,5)" in ti
    assert "counterparty_shared_decision_utility_source" in ti
    assert "same_simulation_same_shared_utility_as_focal" in ti
    assert "decision_attribution" in ti

    # Trade Decision's compatibility score must be explicitly the shared utility.
    assert 'row["shared_decision_utility_score"] = resolved["score"]' in trade_base
    assert 'post_sim_score_is_shared_decision_utility_compatibility_alias' in trade_base
    decision_tuple = trade_gov.split("DECISION_OUTPUTS = (",1)[1].split(")",1)[0]
    assert '"shared_decision_utility_score"' in decision_tuple
    assert '"strategic_value_delta"' not in decision_tuple
    assert 'metric(current, "shared_decision_utility_score")' in trade_gov

    # Standalone roster decisions must delegate final recommendation authority.
    assert '"authority": "Shared Decision Utility / GM3 Team Improvement"' in roster
    assert '"no_independent_roster_decision_score_created": True' in roster
    assert '"decision_attribution": focal_attribution' in roster

    # Opportunity Engine may route but must not create an independent valuation.
    forbidden = (
        "opportunity_utility_score =",
        "opportunity_value_score =",
        "acceptance_probability *",
    )
    assert not any(x in oe for x in forbidden)

    print("end-to-end decision-path integration audit passed")


if __name__ == "__main__":
    main()
