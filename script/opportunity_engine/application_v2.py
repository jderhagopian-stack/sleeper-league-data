#!/usr/bin/env python3
"""FSFFL Opportunity Engine 2.0.

Phase 2 extends search coverage and execution diagnostics without creating a new
valuation, simulation, or recommendation authority. GM3 Team Improvement still
owns cross-channel utility and bundle evaluation; Simulator owns competitive
outcomes; Trade Decision owns generated-trade execution advice.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import inspect
import json
import math
import os
import statistics
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent
ROOT = SCRIPT.parent
TEAM_IMPROVEMENT = SCRIPT / "gm3" / "team_improvement.py"
if str(SCRIPT) not in sys.path:
    sys.path.insert(0, str(SCRIPT))
from opportunity_engine import application as v1
from gm3 import team_improvement as gm3_team_improvement

MODEL_VERSION = "FSFFL-Opportunity-Engine-2.0"


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
        "--trade-packages-per-target", str(args.trade_packages_per_target),
        "--strategic-posture", str(getattr(args, "strategic_posture", "AUTO")),
        "--seed", str(args.seed),
        "--output", str(raw_output),
    ]
    subprocess.run(cmd, cwd=ROOT, check=True)


def _portfolio_evaluator(focus_user_id, simulations, seed, strategic_posture="AUTO"):
    """Call the stable GM3 evaluator while preserving compatibility test doubles."""
    fn = gm3_team_improvement.portfolio_evaluator
    if "strategic_posture" in inspect.signature(fn).parameters:
        return fn(
            str(focus_user_id),
            simulations=int(simulations),
            seed=int(seed),
            strategic_posture=strategic_posture,
        )
    return fn(str(focus_user_id), simulations=int(simulations), seed=int(seed))

def _bundle_key(indices):
    return tuple(sorted(int(x) for x in indices))


def _compatible_bundle(rows, candidate):
    return all(v1._compatible(row, candidate) for row in rows)


def _execution_plan(rows):
    steps = []
    for ordinal, row in enumerate(rows, 1):
        channel = str(row.get("channel") or "")
        target = row.get("target") or {}
        if channel == "TRADE":
            incoming=list(row.get("incoming") or [])
            if not incoming and target:
                incoming=[target]
            steps.append({
                "step": ordinal,
                "channel": channel,
                "description": row.get("description"),
                "preconditions": {
                    "counterparty_user_id": row.get("counterparty_user_id") or row.get("seller_user_id"),
                    "incoming_asset_ids_must_remain_with_counterparty": [x.get("asset_id") for x in incoming],
                    "focal_outgoing_assets_must_remain_owned": [x.get("asset_id") for x in (row.get("outgoing") or [])],
                    "counterparty_willingness_observed": False,
                },
                "execution_authority": "Trade Decision",
            })
        elif channel == "WAIVER":
            steps.append({
                "step": ordinal,
                "channel": channel,
                "description": row.get("description"),
                "preconditions": {
                    "target_asset_id": target.get("asset_id"),
                    "target_must_remain_unowned": True,
                    "roster_legality_and_endogenous_drop_rechecked": True,
                },
                "execution_authority": "GM3 Team Improvement",
            })
    return {
        "steps": steps,
        "dependencies_are_structural_only": True,
        "live_ownership_and_availability_must_be_rechecked_before_execution": True,
        "counterparty_acceptance_is_not_assumed": True,
    }


def _portfolio_result(rows, result, screen_sims=None):
    out = v1._portfolio_result(rows, result)
    out["move_count"] = len(rows)
    out["execution_plan"] = _execution_plan(rows)
    if screen_sims is not None:
        out["screen_team_improvement_score"] = out.get("team_improvement_score")
        out["screen_simulations"] = int(screen_sims)
    return out


def _evaluate_bundle(evaluator, candidates, indices, screen_sims):
    rows = [candidates[i] for i in indices]
    result = evaluator.evaluate(rows)
    row = _portfolio_result(rows, result, screen_sims=screen_sims)
    row["_indices"] = list(indices)
    return row


def build_adaptive_portfolio_view(source, focus_user_id, depth=8, max_moves=3,
                                  beam_width=8, simulations=500,
                                  confirm_simulations=5000, confirm_top=3,
                                  seed=20260821, limit=5, strategic_posture="AUTO"):
    """Beam-search compatible 2..N move bundles; GM3 evaluates every bundle."""
    candidates = [
        copy.deepcopy(x)
        for x in (source.get("top_cross_channel_options") or [])[: max(0, int(depth))]
        if str(x.get("channel") or "") in {"TRADE", "WAIVER"}
    ]
    if len(candidates) < 2 or int(max_moves) < 2:
        return {
            "best_portfolio": None,
            "top_portfolios": [],
            "candidate_bundles_evaluated": 0,
            "candidate_pairs_evaluated": 0,
            "authority": "GM3 Team Improvement",
            "adaptive_search": True,
        }

    screen_evaluator = _portfolio_evaluator(
        focus_user_id, simulations, seed, strategic_posture
    )
    all_screened = []
    current_level = []
    seen = set()

    # Level 2 is exhaustive within the candidate pool.
    for i in range(len(candidates)):
        for j in range(i + 1, len(candidates)):
            if not v1._compatible(candidates[i], candidates[j]):
                continue
            key = (i, j)
            seen.add(key)
            row = _evaluate_bundle(screen_evaluator, candidates, key, simulations)
            current_level.append(row)
            all_screened.append(row)
    current_level.sort(key=lambda x: float(x.get("team_improvement_score") or 0.0), reverse=True)
    frontier = current_level[: max(1, int(beam_width))]

    # Levels 3+ expand only the leading frontier; no new scoring function is used.
    for move_count in range(3, max(2, int(max_moves)) + 1):
        expanded = []
        for parent in frontier:
            indices = list(parent.get("_indices") or [])
            rows = [candidates[i] for i in indices]
            for idx, candidate in enumerate(candidates):
                if idx in indices or not _compatible_bundle(rows, candidate):
                    continue
                key = _bundle_key(indices + [idx])
                if len(key) != move_count or key in seen:
                    continue
                seen.add(key)
                row = _evaluate_bundle(screen_evaluator, candidates, key, simulations)
                expanded.append(row)
                all_screened.append(row)
        if not expanded:
            break
        expanded.sort(key=lambda x: float(x.get("team_improvement_score") or 0.0), reverse=True)
        frontier = expanded[: max(1, int(beam_width))]

    if not all_screened:
        return {
            "best_portfolio": None,
            "top_portfolios": [],
            "candidate_bundles_evaluated": 0,
            "candidate_pairs_evaluated": 0,
            "authority": "GM3 Team Improvement",
            "adaptive_search": True,
        }

    all_screened.sort(key=lambda x: float(x.get("team_improvement_score") or 0.0), reverse=True)
    finalists = all_screened[: min(max(1, int(confirm_top)), len(all_screened))]
    confirm_count = int(confirm_simulations) if int(confirm_simulations) > int(simulations) else int(simulations)
    confirm_evaluator = _portfolio_evaluator(
        focus_user_id, confirm_count, seed, strategic_posture
    )
    confirmed = []
    for row in finalists:
        source_rows = row.get("_source_rows") or []
        result = confirm_evaluator.evaluate(source_rows)
        out = _portfolio_result(source_rows, result)
        out["screen_team_improvement_score"] = row.get("screen_team_improvement_score")
        out["screen_simulations"] = int(simulations)
        out["confirmed"] = True
        out["confirmation_simulations"] = confirm_count
        out["_indices"] = copy.deepcopy(row.get("_indices") or [])
        confirmed.append(out)
    confirmed.sort(key=lambda x: float(x.get("team_improvement_score") or 0.0), reverse=True)

    best_single_row = (source.get("top_cross_channel_options") or [None])[0]
    comparable_single = None
    if best_single_row:
        single_result = confirm_evaluator.evaluate([best_single_row])
        comparable_single = {
            "description": best_single_row.get("description"),
            "team_improvement_score": single_result.get("team_improvement_score"),
            "decision_attribution": single_result.get("decision_attribution"),
            "simulation_count": confirm_count,
            "authority": "GM3 Team Improvement",
        }

    top = confirmed[: max(1, int(limit))]
    preferred = False
    if top and comparable_single:
        incremental = float(top[0].get("team_improvement_score") or 0.0) - float(comparable_single.get("team_improvement_score") or 0.0)
        top[0]["incremental_score_vs_best_single_step_same_precision"] = round(incremental, 2)
        top[0]["preferred_to_best_single_step_on_same_gm3_utility"] = incremental > 0
        preferred = incremental > 0

    best_source_rows = copy.deepcopy((top[0].get("_source_rows") or [])) if top else []
    for row in top:
        row.pop("_source_rows", None)
        row.pop("_indices", None)

    sizes = sorted({int(x.get("move_count") or 0) for x in all_screened if int(x.get("move_count") or 0) >= 2})
    return {
        "best_portfolio": top[0] if top else None,
        "top_portfolios": top,
        "_best_source_rows": best_source_rows,
        "candidate_bundles_evaluated": len(all_screened),
        "candidate_pairs_evaluated": sum(1 for x in all_screened if int(x.get("move_count") or 0) == 2),
        "bundle_sizes_evaluated": sizes,
        "deep_confirmed_portfolios": len(confirmed),
        "search_depth": int(depth),
        "max_moves": int(max_moves),
        "beam_width": int(beam_width),
        "adaptive_search": True,
        "screen_simulation_count_per_bundle": int(simulations),
        "confirmation_simulation_count_per_finalist": confirm_count,
        "best_single_step_same_precision": comparable_single,
        "best_portfolio_preferred_to_best_single_step": preferred,
        "authority": "GM3 Team Improvement",
        "search_budget_is_computational_not_decision_authority": True,
        "screening_and_confirmation_use_same_gm3_utility": True,
        "portfolio_sequence_feasibility_requires_live_precondition_recheck": True,
    }


def _robustness(rows, focus_user_id, simulations, seeds, strategic_posture='AUTO'):
    rows = list(rows or [])
    seeds = list(seeds or [])
    if not rows or not seeds or int(simulations) <= 0:
        return {"enabled": False}
    samples = []
    for seed in seeds:
        evaluator = _portfolio_evaluator(
            focus_user_id, simulations, seed, strategic_posture
        )
        result = evaluator.evaluate(rows)
        sim = result.get("simulation") or {}
        focus = sim.get("focus_delta") or {}
        samples.append({
            "seed": int(seed),
            "team_improvement_score": float(result.get("team_improvement_score") or 0.0),
            "expected_wins_delta": float(focus.get("expected_wins") or 0.0),
            "championship_probability_delta": float(focus.get("championship_probability") or 0.0),
        })
    scores = [x["team_improvement_score"] for x in samples]
    return {
        "enabled": True,
        "authority": "GM3 Team Improvement",
        "simulation_count_per_seed": int(simulations),
        "seeds": [int(x) for x in seeds],
        "samples": samples,
        "score_mean": round(statistics.fmean(scores), 4),
        "score_min": round(min(scores), 4),
        "score_max": round(max(scores), 4),
        "score_population_stddev": round(statistics.pstdev(scores), 4) if len(scores) > 1 else 0.0,
        "sign_stable": all(x > 0 for x in scores) or all(x <= 0 for x in scores),
        "diagnostic_not_new_ranking_utility": True,
    }


def _prospective_metadata(source):
    encoded = json.dumps(source, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return {
        "schema_version": "FSFFL-Opportunity-Prospective-Snapshot-1.0",
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "source_revision": os.getenv("GITHUB_SHA") or None,
        "source_input_sha256": hashlib.sha256(encoded).hexdigest(),
        "contains_future_outcomes": False,
        "intended_use": "timestamped prospective recommendation evidence for later validation",
        "current_player_values_may_not_be_backfilled_when_grading_this_snapshot": True,
    }


def build_board(source, args, trade_reviews):
    board = v1.build_board(
        source,
        focus_user_id=args.focus_user_id,
        portfolio_depth=0,
        seed=args.seed,
        trade_reviews=trade_reviews,
    )
    portfolio = build_adaptive_portfolio_view(
        source,
        args.focus_user_id,
        depth=args.portfolio_depth,
        max_moves=args.portfolio_max_moves,
        beam_width=args.portfolio_beam_width,
        simulations=args.portfolio_sims,
        confirm_simulations=args.portfolio_confirm_sims,
        confirm_top=args.portfolio_confirm_top,
        seed=args.seed,
        strategic_posture=getattr(args, "strategic_posture", "AUTO"),
    )
    best_portfolio_rows = portfolio.pop("_best_source_rows", [])
    best_single = (source.get("top_cross_channel_options") or [None])[0]
    robustness_seeds = [args.seed + i * 1009 for i in range(max(0, int(args.robustness_seeds)))]
    board["model_version"] = MODEL_VERSION
    board["strategic_posture"] = copy.deepcopy(source.get("strategic_posture") or {})
    board["portfolio_optimization"] = portfolio
    board["robustness"] = {
        "best_single_step": _robustness([best_single] if best_single else [], args.focus_user_id, args.robustness_sims, robustness_seeds, getattr(args, "strategic_posture", "AUTO")),
        "best_portfolio": _robustness(best_portfolio_rows, args.focus_user_id, args.robustness_sims, robustness_seeds, getattr(args, "strategic_posture", "AUTO")),
        "common_random_number_seed_family": robustness_seeds,
        "used_for_primary_ranking": False,
    }
    board["prospective_validation"] = _prospective_metadata(source)
    board["search_configuration"] = {
        "trade_candidates": int(args.trade_screen),
        "waiver_candidates": int(args.waiver_screen),
        "trade_packages_per_target": int(args.trade_packages_per_target),
        "quick_sims": int(args.quick_sims),
        "confirm_sims": int(args.confirm_sims),
        "confirm_top": int(args.confirm_top),
        "portfolio_candidate_depth": int(args.portfolio_depth),
        "portfolio_max_moves": int(args.portfolio_max_moves),
        "portfolio_beam_width": int(args.portfolio_beam_width),
        "portfolio_screen_sims": int(args.portfolio_sims),
        "portfolio_confirm_sims": int(args.portfolio_confirm_sims),
        "robustness_seeds": int(args.robustness_seeds),
        "robustness_sims_per_seed": int(args.robustness_sims),
        "strategic_posture": str(getattr(args, "strategic_posture", "AUTO")),
    }
    board.setdefault("capability_status", {})["adaptive_multi_step_portfolio_optimization"] = True
    board["capability_status"]["portfolio_search_up_to_three_or_more_moves"] = int(args.portfolio_max_moves) >= 3
    board["capability_status"]["prospective_validation_snapshots"] = True
    board["capability_status"]["recommendation_robustness_diagnostics"] = int(args.robustness_seeds) > 0
    board.setdefault("policy", {})["adaptive_search_creates_new_utility"] = False
    board["policy"]["strategic_posture_changes_competitive_state"] = False
    board["policy"]["strategic_posture_search_guidance_creates_new_utility"] = False
    board["policy"]["strategic_posture_uses_existing_governed_weight_curve"] = True
    board["policy"]["robustness_diagnostics_change_primary_ranking"] = False
    board["policy"]["portfolio_execution_rechecks_live_preconditions"] = True
    board["policy"]["waiver_discovery_fixed_cross_unit_coefficients_active"] = False
    board["policy"]["trade_package_depth_is_search_budget_only"] = True
    board.setdefault("provenance", {})["phase2_base_composition"] = "FSFFL-Opportunity-Engine-1.4"
    board["provenance"]["adaptive_portfolio_scores_owned_by_gm3_team_improvement"] = True
    return board


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--focus-user-id", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--team-improvement-input")
    ap.add_argument("--quick-sims", type=int, default=200)
    ap.add_argument("--confirm-sims", type=int, default=1000)
    ap.add_argument("--trade-screen", type=int, default=30)
    ap.add_argument("--waiver-screen", type=int, default=30)
    ap.add_argument("--confirm-top", type=int, default=5)
    ap.add_argument("--trade-packages-per-target", type=int, default=5)
    ap.add_argument("--portfolio-depth", type=int, default=8)
    ap.add_argument("--portfolio-max-moves", type=int, default=3)
    ap.add_argument("--portfolio-beam-width", type=int, default=8)
    ap.add_argument("--portfolio-sims", type=int, default=500)
    ap.add_argument("--portfolio-confirm-sims", type=int, default=5000)
    ap.add_argument("--portfolio-confirm-top", type=int, default=3)
    ap.add_argument("--trade-review-depth", type=int, default=1)
    ap.add_argument("--trade-review-quick-sims", type=int, default=200)
    ap.add_argument("--trade-review-confirm-sims", type=int, default=50000)
    ap.add_argument("--trade-review-search-depth", type=int, default=60)
    ap.add_argument("--robustness-seeds", type=int, default=0)
    ap.add_argument("--robustness-sims", type=int, default=500)
    ap.add_argument("--strategic-posture", default="AUTO")
    ap.add_argument("--seed", type=int, default=20260821)
    args = ap.parse_args()

    output = Path(args.output)
    if args.team_improvement_input:
        raw_output = Path(args.team_improvement_input)
    else:
        raw_output = output.with_suffix(".team-improvement.json")
        _run_team_improvement(args, raw_output)
    source = v1.load_json(raw_output)
    if str(source.get("generated_for_user_id")) != str(args.focus_user_id):
        raise RuntimeError("Team Improvement output does not match requested focus user")

    trade_reviews = v1.review_trade_candidates(
        source,
        args.focus_user_id,
        depth=args.trade_review_depth,
        quick_sims=args.trade_review_quick_sims,
        confirm_sims=args.trade_review_confirm_sims,
        search_depth=args.trade_review_search_depth,
        seed=args.seed,
        strategic_posture=args.strategic_posture,
    )
    board = build_board(source, args, trade_reviews)
    v1.write_json(output, board)
    print(json.dumps({
        "model_version": MODEL_VERSION,
        "team": board.get("team_name"),
        "best_move": (board.get("best_move_available") or {}).get("description"),
        "ranked_opportunities": len(board.get("ranked_single_step_opportunities") or []),
        "portfolio_bundles_evaluated": (board.get("portfolio_optimization") or {}).get("candidate_bundles_evaluated"),
        "trade_decision_reviews": len(board.get("trade_decision_reviews") or []),
        "output": str(output),
    }, indent=2))


if __name__ == "__main__":
    main()
