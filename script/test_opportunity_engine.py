#!/usr/bin/env python3
"""Fast regression for Opportunity Engine governed composition."""
from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent
APP = SCRIPT / "opportunity_engine" / "application.py"
spec = importlib.util.spec_from_file_location("fsffl_opportunity_engine_test", APP)
if spec is None or spec.loader is None:
    raise RuntimeError("Unable to import Opportunity Engine")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

source = {
    "model_version": "FSFFL-GM-Team-Improvement-Lab-1.4",
    "generated_for_user_id": "focus",
    "team_name": "Test Team",
    "team_state": "contender",
    "recommended_action": {
        "channel": "TRADE",
        "description": "Trade A for B",
        "team_improvement_score": 12.0,
    },
    "best_trade_options": [
        {"channel": "TRADE", "description": "Trade A for B", "team_improvement_score": 12.0},
        {"channel": "TRADE", "description": "Trade C for D", "team_improvement_score": 9.0},
    ],
    "best_waiver_options": [
        {"channel": "WAIVER", "description": "Add E", "team_improvement_score": 7.0},
    ],
    "top_cross_channel_options": [
        {"channel": "TRADE", "description": "Trade A for B", "team_improvement_score": 12.0},
        {"channel": "WAIVER", "description": "Add E", "team_improvement_score": 7.0},
        {"channel": "TRADE", "description": "Trade C for D", "team_improvement_score": 9.0},
    ],
    "hold_benchmark": {"channel": "HOLD", "description": "Hold current roster", "team_improvement_score": 0.0},
    "search_summary": {"trade_candidates_screened": 20, "waiver_candidates_screened": 20},
}
board = mod.build_board(source)

assert [x["description"] for x in board["ranked_single_step_opportunities"]] == [
    "Trade A for B", "Add E", "Trade C for D"
], "Opportunity Engine must preserve upstream governed order rather than resorting"
assert board["best_move_available"]["description"] == "Trade A for B"
assert board["best_trade_opportunity"]["opportunity_engine_status"] == "CANDIDATE_REQUIRES_TRADE_DECISION_REVIEW"
assert board["best_waiver_opportunity"]["opportunity_engine_status"] == "GOVERNED_SINGLE_STEP_OPPORTUNITY"
assert board["provenance"]["opportunity_engine_rescoring_applied"] is False
assert board["provenance"]["opportunity_engine_reranking_applied"] is False
assert board["policy"]["creates_new_valuation_model"] is False
assert board["capability_status"]["multi_step_portfolio_optimization"] is False
print("Opportunity Engine governed composition regression passed")
