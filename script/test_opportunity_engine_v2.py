#!/usr/bin/env python3
"""Fast regression for Opportunity Engine 2.0 search-only authority."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

SCRIPT = Path(__file__).resolve().parent
APP = SCRIPT / "opportunity_engine" / "application_v2.py"
spec = importlib.util.spec_from_file_location("fsffl_opportunity_engine_v2_test", APP)
if spec is None or spec.loader is None:
    raise RuntimeError("Unable to import Opportunity Engine 2.0")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class FakeEvaluator:
    def __init__(self, simulations=100, seed=1):
        self.simulations = simulations
        self.seed = seed

    def evaluate(self, rows):
        # Synthetic fixture only: the evaluator, not Opportunity Engine, owns score.
        score = sum(float(x.get("synthetic_owned_score") or 0.0) for x in rows)
        if len(rows) >= 3:
            score += 25.0
        return {
            "team_improvement_score": score,
            "simulation": {
                "focus_delta": {
                    "expected_wins": score / 100.0,
                    "championship_probability": score / 10000.0,
                },
                "strategic": {"market_dynasty_delta": score},
            },
            "actions": [],
            "authority": "GM3 Team Improvement",
            "shared_decision_utility": "FSFFL-Shared-Decision-Utility-2.0",
        }


mod.gm3_team_improvement.portfolio_evaluator = (
    lambda focus_user_id, simulations=1000, seed=1: FakeEvaluator(simulations, seed)
)


def trade(name, target, outgoing, score, seller):
    return {
        "channel": "TRADE",
        "description": name,
        "target": {"asset_id": target, "player_id": target.split(":")[-1]},
        "outgoing": [{"asset_id": outgoing, "asset_type": "player", "player_id": outgoing.split(":")[-1]}],
        "seller_user_id": seller,
        "synthetic_owned_score": score,
        "team_improvement_score": score,
        "actionable": True,
    }


def waiver(name, target, score):
    return {
        "channel": "WAIVER",
        "description": name,
        "target": {"asset_id": target, "player_id": target.split(":")[-1]},
        "outgoing": [],
        "synthetic_owned_score": score,
        "team_improvement_score": score,
        "actionable": True,
    }


rows = [
    trade("Trade A for B", "player:B", "player:A", 12, "seller1"),
    waiver("Add C", "player:C", 10),
    trade("Trade D for E", "player:E", "player:D", 8, "seller2"),
    waiver("Add F", "player:F", 5),
]
source = {
    "model_version": "FSFFL-GM-Team-Improvement-Lab-1.5",
    "generated_for_user_id": "focus",
    "team_name": "Test Team",
    "team_state": "contender",
    "recommended_action": rows[0],
    "best_trade_options": [rows[0], rows[2]],
    "best_waiver_options": [rows[1], rows[3]],
    "top_cross_channel_options": rows,
    "hold_benchmark": {"channel": "HOLD", "description": "Hold current roster", "team_improvement_score": 0.0},
    "search_summary": {"trade_candidates_screened": 2, "waiver_candidates_screened": 2},
}

portfolio = mod.build_adaptive_portfolio_view(
    source,
    "focus",
    depth=4,
    max_moves=3,
    beam_width=4,
    simulations=20,
    confirm_simulations=40,
    confirm_top=3,
    seed=7,
)
assert portfolio["adaptive_search"] is True
assert portfolio["authority"] == "GM3 Team Improvement"
assert 2 in portfolio["bundle_sizes_evaluated"]
assert 3 in portfolio["bundle_sizes_evaluated"]
assert portfolio["best_portfolio"]["move_count"] == 3
assert portfolio["best_portfolio"]["execution_plan"]["live_ownership_and_availability_must_be_rechecked_before_execution"] is True
assert portfolio["screening_and_confirmation_use_same_gm3_utility"] is True

meta = mod._prospective_metadata(source)
assert meta["contains_future_outcomes"] is False
assert len(meta["source_input_sha256"]) == 64
assert meta["current_player_values_may_not_be_backfilled_when_grading_this_snapshot"] is True

args = SimpleNamespace(
    focus_user_id="focus",
    portfolio_depth=4,
    portfolio_max_moves=3,
    portfolio_beam_width=4,
    portfolio_sims=20,
    portfolio_confirm_sims=40,
    portfolio_confirm_top=3,
    robustness_seeds=2,
    robustness_sims=20,
    seed=7,
    trade_screen=30,
    waiver_screen=30,
    trade_packages_per_target=5,
    quick_sims=200,
    confirm_sims=1000,
    confirm_top=5,
)
board = mod.build_board(source, args, trade_reviews=[])
assert board["model_version"] == "FSFFL-Opportunity-Engine-2.0"
assert board["policy"]["creates_new_valuation_model"] is False
assert board["policy"]["creates_new_cross_channel_utility"] is False
assert board["policy"]["adaptive_search_creates_new_utility"] is False
assert board["policy"]["robustness_diagnostics_change_primary_ranking"] is False
assert board["provenance"]["adaptive_portfolio_scores_owned_by_gm3_team_improvement"] is True
assert board["capability_status"]["adaptive_multi_step_portfolio_optimization"] is True
assert board["capability_status"]["prospective_validation_snapshots"] is True
assert board["robustness"]["best_single_step"]["enabled"] is True
assert board["robustness"]["best_single_step"]["diagnostic_not_new_ranking_utility"] is True
print("Opportunity Engine 2.0 governed adaptive-search regression passed")
