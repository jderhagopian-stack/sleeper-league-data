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
scenario=mod._trade_scenario({
    "channel":"TRADE","description":"Trade A for B","seller_user_id":"seller",
    "target":{"asset_id":"player:B","player_id":"B"},
    "outgoing":[{"asset_id":"player:A","asset_type":"player","player_id":"A"}],
}, "focus", 1)
assert scenario["offer_initiator_user_id"]=="focus"
assert scenario["actions"][0]["from_user_id"]=="focus"
assert scenario["actions"][1]["from_user_id"]=="seller"
summary=mod._summarize_trade_decision({
    "model_version":"FSFFL-Counter-Market-Sweep-1.26",
    "recommended_next_action":"ACCEPT_NOW",
    "current_offer_evaluation":{"simulation":{"focus_delta":{"expected_wins":0.1},"strategic":{"market_dynasty_delta":10}}},
    "governance":{"option_outcome_consistency":{"action_basis":"TEST"}},
})
assert summary["recommended_next_action"]=="OPEN_NEGOTIATION"
assert summary["underlying_trade_decision_action"]=="ACCEPT_NOW"
assert summary["generated_proposal_semantics_applied"] is True
assert summary["generated_proposal_willingness_observed"] is False
assert board["capability_status"]["multi_step_portfolio_optimization"] is False
assert board["policy"]["specialized_views_preserve_upstream_order"] is True
assert board["provenance"]["portfolio_scores_owned_by_gm3_team_improvement"] is True
assert board["provenance"]["specialist_intelligence_changes_ranking"] is False
assert board["model_version"]=="FSFFL-Opportunity-Engine-1.4"
assert board["policy"]["specialist_intelligence_is_context_not_rescoring"] is True
assert board["policy"]["portfolio_search_budget_is_computational_only"] is True
print("Opportunity Engine governed composition regression passed")
