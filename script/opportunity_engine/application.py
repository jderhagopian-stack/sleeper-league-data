#!/usr/bin/env python3
"""FSFFL Opportunity Engine.

Application-layer orchestrator for proactive franchise improvement.

Phase 1 intentionally does not create a new valuation or recommendation model.
It invokes the stable GM3 Team Improvement application, preserves that governed
cross-channel ordering, and emits an opportunity board with explicit provenance.

Trades discovered here are candidates for Trade Decision review before execution
advice. Waiver/add-drop evaluation remains owned by GM3 Team Improvement.
"""
from __future__ import annotations

import argparse
import copy
import json
import subprocess
import sys
from pathlib import Path

MODEL_VERSION = "FSFFL-Opportunity-Engine-1.0"
SCRIPT = Path(__file__).resolve().parent.parent
ROOT = SCRIPT.parent
TEAM_IMPROVEMENT = SCRIPT / "gm3" / "team_improvement.py"


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _run_team_improvement(args, raw_output: Path):
    cmd = [
        sys.executable,
        str(TEAM_IMPROVEMENT),
        "--focus-user-id", str(args.focus_user_id),
        "--quick-sims", str(args.quick_sims),
        "--confirm-sims", str(args.confirm_sims),
        "--trade-screen", str(args.trade_screen),
        "--waiver-screen", str(args.waiver_screen),
        "--confirm-top", str(args.confirm_top),
        "--seed", str(args.seed),
        "--output", str(raw_output),
    ]
    subprocess.run(cmd, cwd=ROOT, check=True)


def _annotate(row):
    out = copy.deepcopy(row)
    channel = str(out.get("channel") or "")
    if channel == "TRADE":
        out["opportunity_engine_status"] = "CANDIDATE_REQUIRES_TRADE_DECISION_REVIEW"
        out["decision_authority"] = "GM3_TEAM_IMPROVEMENT_DISCOVERY; TRADE_DECISION_BEFORE_EXECUTION"
    elif channel == "WAIVER":
        out["opportunity_engine_status"] = "GOVERNED_SINGLE_STEP_OPPORTUNITY"
        out["decision_authority"] = "GM3_TEAM_IMPROVEMENT"
    elif channel == "HOLD":
        out["opportunity_engine_status"] = "EXPLICIT_BASELINE"
        out["decision_authority"] = "GM3_TEAM_IMPROVEMENT"
    return out


def build_board(source):
    """Compose a board without rescoring, reranking, or inventing authority."""
    ranked = [_annotate(x) for x in (source.get("top_cross_channel_options") or [])]
    best_trade = next((_annotate(x) for x in (source.get("best_trade_options") or [])), None)
    best_waiver = next((_annotate(x) for x in (source.get("best_waiver_options") or [])), None)
    recommended = _annotate(source.get("recommended_action") or source.get("hold_benchmark") or {})

    return {
        "model_version": MODEL_VERSION,
        "generated_for_user_id": source.get("generated_for_user_id"),
        "team_name": source.get("team_name"),
        "team_state": source.get("team_state"),
        "best_move_available": recommended,
        "best_trade_opportunity": best_trade,
        "best_waiver_opportunity": best_waiver,
        "ranked_single_step_opportunities": ranked,
        "hold_benchmark": _annotate(source.get("hold_benchmark") or {}),
        "search_summary": copy.deepcopy(source.get("search_summary") or {}),
        "provenance": {
            "source_application": "GM3 Team Improvement",
            "source_model_version": source.get("model_version"),
            "cross_channel_order_preserved_from_source": True,
            "opportunity_engine_rescoring_applied": False,
            "opportunity_engine_reranking_applied": False,
            "trade_decision_review_required_before_execution_advice": True,
            "waiver_decision_authority": "GM3 Team Improvement",
        },
        "capability_status": {
            "single_step_trade_search": True,
            "single_step_waiver_search": True,
            "explicit_hold_baseline": True,
            "multi_step_portfolio_optimization": False,
            "sell_high_buy_low_specialized_views": False,
            "negotiation_revisit_queue": False,
            "league_wide_continuous_monitoring": False,
        },
        "policy": {
            "application_layer_orchestrator": True,
            "creates_new_valuation_model": False,
            "creates_new_cross_channel_utility": False,
            "search_heuristics_have_final_decision_authority": False,
            "shared_core_promotion_requires_second_consumer_or_domain_generic_behavior": True,
        },
    }


def architecture():
    return {
        "model_version": MODEL_VERSION,
        "layer": "Application",
        "application": "Opportunity Engine",
        "source_application": "GM3 Team Improvement",
        "rescoring_authority": False,
        "trade_execution_review": "Trade Decision",
        "shared_core_additions_phase_1": [],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--focus-user-id", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--team-improvement-input")
    ap.add_argument("--quick-sims", type=int, default=200)
    ap.add_argument("--confirm-sims", type=int, default=1000)
    ap.add_argument("--trade-screen", type=int, default=20)
    ap.add_argument("--waiver-screen", type=int, default=20)
    ap.add_argument("--confirm-top", type=int, default=5)
    ap.add_argument("--seed", type=int, default=20260821)
    args = ap.parse_args()

    output = Path(args.output)
    if args.team_improvement_input:
        raw_output = Path(args.team_improvement_input)
    else:
        raw_output = output.with_suffix(".team-improvement.json")
        _run_team_improvement(args, raw_output)

    source = load_json(raw_output)
    if str(source.get("generated_for_user_id")) != str(args.focus_user_id):
        raise RuntimeError("Team Improvement output does not match requested focus user")

    board = build_board(source)
    write_json(output, board)
    print(json.dumps({
        "model_version": MODEL_VERSION,
        "team": board.get("team_name"),
        "best_move": (board.get("best_move_available") or {}).get("description"),
        "ranked_opportunities": len(board.get("ranked_single_step_opportunities") or []),
        "output": str(output),
    }, indent=2))


if __name__ == "__main__":
    main()
