#!/usr/bin/env python3
"""Regression checks for the read-only Decision / Utility Inspector."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from league_intelligence import decision_inspector as inspector
from league_intelligence import application


def attribution(score: float, current: float, future: float):
    return {
        "model_version": "FSFFL-Decision-Attribution-1.0",
        "shared_decision_utility_model_version": "FSFFL-Shared-Decision-Utility-2.0",
        "final_shared_decision_utility": score,
        "component_sum": score,
        "reconciles": True,
        "channels": [
            {
                "channel": "current",
                "primitive_value": current,
                "objective_weight": 0.6,
                "numeric_contribution": current * 0.6,
                "authorized_for_final_utility": True,
            },
            {
                "channel": "future",
                "primitive_value": future,
                "objective_weight": 0.4,
                "numeric_contribution": future * 0.4,
                "authorized_for_final_utility": True,
            },
        ],
        "creates_independent_score": False,
    }


record = {
    "description": "Synthetic governed trade for view-contract validation",
    "channel": "TRADE",
    "trade_direction": "ACQUIRE",
    "incoming": [{"asset_id": "player:target", "name": "Target Player"}],
    "outgoing": [{"asset_id": "pick:2028:R1:orig9", "name": "2028 1st"}],
    "team_improvement_score": 52.0,
    "counterparty_shared_decision_utility_score": 18.0,
    "decision_attribution": attribution(52.0, 60.0, 40.0),
    "counterparty_decision_attribution": attribution(18.0, -10.0, 60.0),
    "simulation": {
        "effective_actions": [{"type": "trade", "players": ["target"]}],
        "focus_delta": {
            "expected_wins": 0.35,
            "expected_points_for": 42.0,
            "playoff_probability": 0.04,
            "bye_probability": 0.01,
            "championship_probability": 0.015,
        },
        "strategic": {
            "competitive_state": "contender",
            "strategic_posture": "BALANCED_CONTENDER",
            "strategic_posture_source": "model_default",
            "objective_weights": {"current": 0.6, "future": 0.4},
        },
        "roster_resolution": {
            "viewer": {
                "active_players_before_trade": 18,
                "legal_active_players_after_resolution": 18,
                "required_cuts": 1,
                "selected_cuts": [{"name": "Replacement Player"}],
                "roster_legal": True,
            }
        },
        "counterparty": {
            "focus_delta": {
                "expected_wins": -0.1,
                "playoff_probability": -0.01,
                "championship_probability": -0.005,
            },
            "strategic": {
                "competitive_state": "retool",
                "strategic_posture": "AUTO",
                "strategic_posture_source": "model_default",
                "objective_weights": {"current": 0.4, "future": 0.6},
            },
        },
    },
    "negotiation_frontier": {
        "authority": "Trade Decision",
        "bucket": "ACTIONABLE_NEGOTIATION",
        "creates_new_trade_value": False,
        "creates_new_acceptance_probability": False,
    },
    "near_frontier_evidence": {"watchlist_eligible": False},
}

selected = inspector.select_record({"rows": [record]}, "rows.0")
assert selected["description"] == record["description"]

view = inspector.inspect_decision(selected, source_path="synthetic.json", selector="rows.0")
assert view["source_contract"]["fully_reconciled_attribution"] is True
assert view["source_contract"]["partial_inspection"] is False
assert view["focal_team"]["shared_decision_utility"] == 52.0
assert view["counterparty"]["shared_decision_utility"] == 18.0
assert view["focal_team"]["simulator_delta"]["expected_wins"] == 0.35
assert view["counterparty"]["simulator_delta"]["expected_wins"] == -0.1
assert view["negotiation_frontier"]["bucket"] == "ACTIONABLE_NEGOTIATION"
assert view["creates_independent_score"] is False
assert view["creates_trade_value"] is False
assert view["creates_acceptance_probability"] is False
assert view["recommendation"] is False

markdown = inspector.render_markdown(view)
assert "FSFFL Decision / Utility Inspector" in markdown
assert "Focal team" in markdown
assert "Counterparty" in markdown
assert "Counterparty utility channels" in markdown
assert "Strategic context" in markdown
assert "BALANCED_CONTENDER" in markdown
assert "Replacement Player" in markdown
assert "ACTIONABLE_NEGOTIATION" in markdown
assert "does not rescore or recommend" in markdown

with tempfile.TemporaryDirectory() as tmp:
    input_path = Path(tmp) / "decision.json"
    input_path.write_text(json.dumps({"rows": [record]}), encoding="utf-8")
    terminal = application.build_terminal(
        Path(__file__).resolve().parent.parent / "data",
        decision_input_path=input_path,
        decision_selector="rows.0",
    )
    integrated = terminal["views"]["decision_utility_inspector"]
    assert terminal["capability_status"]["decision_utility_inspector"] is True
    assert terminal["contract_health"]["decision_utility_inspector"]["compatible"] is True
    assert integrated["focal_team"]["shared_decision_utility"] == 52.0

partial = inspector.inspect_decision({"description": "No attribution", "team_improvement_score": 5})
assert partial["source_contract"]["partial_inspection"] is True
assert partial["source_contract"]["fully_reconciled_attribution"] is False

decision_lab = {
    "description": "Roster Decision Lab shape",
    "focus_user_id": "viewer",
    "actions": [{"type": "trade"}],
    "simulation": {"n_sims": 100},
    "team_comparisons": {
        "viewer": {
            "delta": {"expected_wins": -0.4, "playoff_probability": -0.05, "championship_probability": -0.02},
            "strategic": {"competitive_state": "contender", "strategic_posture": "AUTO"},
        },
        "seller": {
            "delta": {"expected_wins": 0.2, "playoff_probability": 0.03, "championship_probability": 0.01},
            "strategic": {"competitive_state": "retool", "strategic_posture": "AUTO"},
        },
    },
    "decision_attribution_by_user": {
        "viewer": attribution(-25.0, -50.0, 12.5),
        "seller": attribution(15.0, 20.0, 7.5),
    },
}
decision_lab_view = inspector.inspect_decision(decision_lab)
assert decision_lab_view["focal_team"]["simulator_delta"]["expected_wins"] == -0.4
assert decision_lab_view["counterparty"]["simulator_delta"]["expected_wins"] == 0.2
assert decision_lab_view["focal_team"]["shared_decision_utility"] == -25.0
assert decision_lab_view["counterparty"]["shared_decision_utility"] == 15.0
assert decision_lab_view["source_contract"]["fully_reconciled_attribution"] is True

try:
    inspector.select_record({"rows": [record]})
except ValueError:
    pass
else:
    raise AssertionError("multi-record input was selected without an explicit selector")

print("League Intelligence Decision / Utility Inspector regressions passed")
