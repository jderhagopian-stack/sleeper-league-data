#!/usr/bin/env python3
"""Adapter that runs historical trades through the canonical GM 3.0 core.

This module owns no trade-scoring formula. It translates a frozen historical
state and a time-frozen GM input bundle into the same Decision Lab strategic
summary, lineup optimizer, Simulator 1.0 season simulation, and decision
classification used by present-day analysis.

If a complete time-frozen input bundle is unavailable, it returns NOT_GRADED
rather than substituting current values or a simplified historical score.
"""
from __future__ import annotations

import copy
import importlib.util
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "script"
MODEL_VERSION = "FSFFL-Historical-GM3-Adapter-1.0"
REQUIRED_BUNDLE_KEYS = {
    "league", "users", "players", "projections", "schedule",
    "gm_asset_maps", "market_player_values", "market_pick_values", "team_states",
}


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to import {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def historical_rosters(state, season_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Convert HistoricalState into the canonical roster shape used by Decision Lab."""
    latest = {str(r.get("roster_id")): r for r in (season_data.get("rosters") or [])}
    rows = []
    for rid, players in sorted(state.roster_players.items(), key=lambda kv: int(kv[0])):
        base = copy.deepcopy(latest.get(str(rid)) or {"roster_id": int(rid)})
        base["roster_id"] = int(rid)
        base["players"] = sorted(str(x) for x in players)
        base["taxi"] = sorted(str(x) for x in state.roster_taxi.get(str(rid), set()))
        base["reserve"] = sorted(str(x) for x in state.roster_reserve.get(str(rid), set()))
        rows.append(base)
    return rows


def bundle_status(bundle: Dict[str, Any] | None) -> Dict[str, Any]:
    if not isinstance(bundle, dict) or not bundle:
        return {
            "ready": False,
            "missing": sorted(REQUIRED_BUNDLE_KEYS),
            "reason": "No time-frozen GM3 input bundle is available for this trade date.",
        }
    missing = sorted(k for k in REQUIRED_BUNDLE_KEYS if not bundle.get(k))
    return {
        "ready": not missing,
        "missing": missing,
        "reason": None if not missing else "Time-frozen GM3 inputs are incomplete.",
    }


def build_all_lineups(simmod, rosters, league, players, projections):
    """Optimize every historical roster from frozen projections; no current cache."""
    lineups = {}
    reg_weeks = simmod.regular_season_weeks(league)
    playoff_start = int((league.get("settings") or {}).get("playoff_week_start") or 15)
    weeks = sorted(set(reg_weeks + [playoff_start, playoff_start + 1, playoff_start + 2]))
    for roster in rosters:
        rid = int(roster.get("roster_id"))
        lineups[rid] = {}
        for week in weeks:
            lineups[rid][week] = simmod.optimize_weekly_lineup(
                roster, week, league, players, projections
            )
    return lineups


def evaluate(state, season_data, actions, participants, bundle, sims=1000, seed=20260821):
    status = bundle_status(bundle)
    if not status["ready"]:
        return {
            "adapter_model_version": MODEL_VERSION,
            "status": "NOT_GRADED",
            "reason": status["reason"],
            "missing_time_frozen_inputs": status["missing"],
            "same_gm3_core_as_current_trade_analysis": True,
            "current_values_used": False,
            "standalone_historical_score_used": False,
        }

    dl = load_module(SCRIPT / "run_roster_decision_lab.py", "historical_gm3_dl")
    teamlab = load_module(SCRIPT / "run_team_improvement_lab.py", "historical_gm3_teamlab")
    simmod = dl.import_simulator()

    league = bundle["league"]
    users = bundle["users"]
    players = bundle["players"]
    projections = bundle["projections"]
    schedule = bundle["schedule"]
    canonical = historical_rosters(state, season_data)

    validation = simmod.validate_inputs(league, canonical, users, players, schedule, projections)
    if not validation.get("validation_passed"):
        return {
            "adapter_model_version": MODEL_VERSION,
            "status": "NOT_GRADED",
            "reason": "Frozen historical simulator inputs failed canonical validation.",
            "validation": validation,
            "same_gm3_core_as_current_trade_analysis": True,
            "current_values_used": False,
            "standalone_historical_score_used": False,
        }

    hypothetical, pick_transfers = dl.apply_actions(canonical, actions)
    baseline_lineups = build_all_lineups(simmod, canonical, league, players, projections)
    hypothetical_lineups, reoptimized = dl.reoptimize_touched_lineups(
        simmod, baseline_lineups, hypothetical, participants,
        league, users, players, projections
    )
    before = dl.simulate_from_lineups(
        simmod, league, canonical, users, schedule, baseline_lineups, sims, seed
    )
    after = dl.simulate_from_lineups(
        simmod, league, hypothetical, users, schedule, hypothetical_lineups, sims, seed
    )
    bidx, aidx = dl.team_index(before), dl.team_index(after)

    # Historical analysis delegates the actual transaction to the normal GM3
    # before/after trade path. No extra per-player What-If simulations are run
    # in the mandatory path; player expectations are already frozen in the
    # dated bundle and GM3 evaluates their combined roster/competitive impact.

    rows = {}
    for uid in participants:
        uid = str(uid)
        b, a = bidx.get(uid), aidx.get(uid)
        if not b or not a:
            raise RuntimeError(f"Historical GM3 simulation missing participant {uid}")
        gm = (bundle.get("gm_asset_maps") or {}).get(uid) or {}
        strategic = dl.strategic_summary_from_maps(
            uid,
            actions,
            gm,
            bundle.get("market_player_values") or {},
            bundle.get("market_pick_values") or {},
        )
        delta = {
            "expected_wins": dl.delta(b.get("expected_wins"), a.get("expected_wins")),
            "expected_points_for": dl.delta(b.get("expected_points_for"), a.get("expected_points_for")),
            "playoff_probability": dl.delta(b.get("playoff_probability"), a.get("playoff_probability")),
            "bye_probability": dl.delta(b.get("bye_probability"), a.get("bye_probability")),
            "division_probability": dl.delta(b.get("division_probability"), a.get("division_probability")),
            "championship_probability": dl.delta(b.get("championship_probability"), a.get("championship_probability")),
        }
        state_label = str((bundle.get("team_states") or {}).get(uid) or "unknown")
        cmp = {
            "focus_before": b,
            "focus_after": a,
            "focus_delta": delta,
            "strategic": strategic,
            "roster_resolution": {},
        }
        rows[uid] = {
            "before": b,
            "after": a,
            "delta": delta,
            "strategic": strategic,
            "decision": dl.classify_decision({"delta": delta, "strategic": strategic}, state_label),
            "state_aware_utility_delta": teamlab.unified_score_with_state(state_label, cmp),
            "team_state": state_label,
        }

    uids = list(rows)
    utility_winner = None
    if len(uids) == 2:
        ordered = sorted(uids, key=lambda u: float(rows[u].get("state_aware_utility_delta") or 0.0), reverse=True)
        utility_winner = ordered[0] if abs(float(rows[ordered[0]].get("state_aware_utility_delta") or 0.0) - float(rows[ordered[1]].get("state_aware_utility_delta") or 0.0)) > 1e-9 else "TIE"
    rational = [u for u in uids if float(rows[u].get("state_aware_utility_delta") or 0.0) > 0]
    assessment = {
        "classification": "WIN_WIN_STATE_RATIONAL" if len(rational) == 2 else "ONE_SIDED_STATE_AWARE" if len(rational) == 1 else "MUTUALLY_COSTLY",
        "both_sides_state_rational": len(rational) == 2,
        "state_aware_utility_winner_user_id": utility_winner,
    }

    return {
        "adapter_model_version": MODEL_VERSION,
        "status": "GRADED_BY_GM3_CORE",
        "same_gm3_core_as_current_trade_analysis": True,
        "decision_lab_model_version": dl.MODEL_VERSION,
        "simulator_model_version": before.get("model_version"),
        "n_sims": int(sims),
        "seed": int(seed),
        "common_random_numbers": True,
        "current_values_used": False,
        "standalone_historical_score_used": False,
        "teams_reoptimized": reoptimized,
        "pick_transfers": pick_transfers,
        "team_results": rows,
        "bilateral_assessment": assessment,
        "team_improvement_model_version": teamlab.MODEL_VERSION,
        "input_bundle_provenance": bundle.get("provenance") or {},
    }
