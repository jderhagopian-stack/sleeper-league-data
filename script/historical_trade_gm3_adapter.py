#!/usr/bin/env python3
"""Adapter that runs historical trades through the canonical GM 3.0 core.

This module owns no trade-scoring formula. It translates a frozen historical
state and a time-frozen GM input bundle into the same Decision Lab strategic
summary, lineup optimizer, Simulator 1.0 season simulation, and decision
classification used by present-day analysis.

Archived-at-time and reconstructed-at-time bundles are both supported. Archived
bundles are eligible for strict empirical backtesting only when they carry an
explicit archive certification. Reconstructed bundles preserve historical-
analysis functionality but are explicitly excluded from pristine out-of-sample
validation claims and authoritative recommendations until reconstruction
assumptions are empirically validated.
"""
from __future__ import annotations

import copy
import importlib.util
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "script"
MODEL_VERSION = "FSFFL-Historical-GM3-Adapter-1.4"
REQUIRED_BUNDLE_KEYS = {
    "league", "users", "players", "projections", "schedule",
    "gm_asset_maps", "market_player_values", "market_pick_values", "team_states",
}
ARCHIVE_CERTIFICATION_FLAGS = (
    "time_frozen",
    "complete",
    "no_present_day_market_values",
    "no_same_season_future_results",
    "no_future_schedule_info",
)


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


def archive_certification_status(bundle: Dict[str, Any]) -> Dict[str, Any]:
    """Return fail-safe archive certification status.

    Archive/OOS status is never inferred from file presence, path, or a bare
    historical_input_class string. Every required assertion must be explicitly
    true in archive_certification. Missing or partial certification fails closed.
    """
    cert = bundle.get("archive_certification")
    if not isinstance(cert, dict):
        return {"certified": False, "missing": ["archive_certification"]}
    missing = [flag for flag in ARCHIVE_CERTIFICATION_FLAGS if cert.get(flag) is not True]
    if cert.get("certified") is not True:
        missing = ["certified", *missing]
    return {"certified": not missing, "missing": missing}


def bundle_status(bundle: Dict[str, Any] | None) -> Dict[str, Any]:
    if not isinstance(bundle, dict) or not bundle:
        return {
            "ready": False,
            "missing": sorted(REQUIRED_BUNDLE_KEYS),
            "historical_input_class": "UNCLASSIFIED_OR_UNCERTIFIED",
            "strict_out_of_sample_backtest_eligible": False,
            "authoritative_recommendation_allowed": False,
            "archive_certified": False,
            "reason": "No time-frozen GM3 input bundle is available for this trade date.",
        }
    missing = sorted(k for k in REQUIRED_BUNDLE_KEYS if not bundle.get(k))
    provenance = bundle.get("provenance") if isinstance(bundle.get("provenance"), dict) else {}
    requested_basis = str(
        bundle.get("historical_input_class")
        or provenance.get("historical_input_class")
        or "UNCLASSIFIED_OR_UNCERTIFIED"
    )
    cert = archive_certification_status(bundle)
    is_archived = requested_basis == "ARCHIVED_AT_TIME" and cert["certified"]
    if is_archived:
        basis = "ARCHIVED_AT_TIME"
    elif requested_basis == "RECONSTRUCTED_AT_TIME":
        basis = "RECONSTRUCTED_AT_TIME"
    else:
        basis = "UNCLASSIFIED_OR_UNCERTIFIED"
    requested_strict_oos = (
        bundle.get("strict_out_of_sample_backtest_eligible") is True
        or provenance.get("strict_out_of_sample_backtest_eligible") is True
    )
    strict_oos = bool(is_archived and requested_strict_oos)
    authoritative = bool(is_archived and strict_oos)
    reason = None
    if missing:
        reason = "Historical GM3 inputs are incomplete."
    elif requested_basis == "ARCHIVED_AT_TIME" and not cert["certified"]:
        reason = "Historical bundle requested ARCHIVED_AT_TIME but lacks complete explicit archive certification."
    elif basis == "UNCLASSIFIED_OR_UNCERTIFIED":
        reason = "Historical bundle is unclassified or uncertified and cannot be treated as archived/OOS evidence."
    return {
        "ready": not missing,
        "missing": missing,
        "historical_input_class": basis,
        "requested_historical_input_class": requested_basis,
        "strict_out_of_sample_backtest_eligible": strict_oos,
        "authoritative_recommendation_allowed": authoritative,
        "archive_certified": bool(cert["certified"]),
        "archive_certification_missing": cert["missing"],
        "reason": reason,
    }


def build_all_lineups(simmod, rosters, league, players, projections):
    """Optimize every historical roster from frozen projections."""
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


def frozen_cached_lineups(bundle: Dict[str, Any], canonical: List[Dict[str, Any]]):
    """Return the builder's frozen baseline lineup cache when structurally complete.

    The reconstruction builder creates this cache from the exact same frozen
    rosters and projections used by the adapter. Reusing it removes duplicate
    optimizer work without changing lineup semantics. Any missing roster/week
    forces a safe fallback to canonical optimization rather than partial cache use.
    """
    payload = bundle.get("optimized_lineup_cache") or {}
    raw = payload.get("lineups") or {}
    weeks = [int(w) for w in (payload.get("weeks") or [])]
    if not raw or not weeks:
        return None
    expected_rids = {int(r.get("roster_id")) for r in canonical}
    if {int(rid) for rid in raw.keys()} != expected_rids:
        return None
    out = {}
    for rid in expected_rids:
        row = raw.get(str(rid)) or raw.get(rid) or {}
        if any(str(week) not in row and week not in row for week in weeks):
            return None
        out[rid] = {
            week: copy.deepcopy(row.get(str(week), row.get(week)))
            for week in weeks
        }
    return out


def evaluate(state, season_data, actions, participants, bundle, sims=1000, seed=20260821):
    status = bundle_status(bundle)
    if not status["ready"]:
        return {
            "adapter_model_version": MODEL_VERSION,
            "status": "NOT_GRADED",
            "reason": status["reason"],
            "missing_historical_inputs": status["missing"],
            "same_gm3_core_as_current_trade_analysis": True,
            "current_values_used": False,
            "standalone_historical_score_used": False,
            "authoritative_recommendation_allowed": False,
        }

    dl = load_module(SCRIPT / "run_roster_decision_lab.py", "historical_gm3_dl")
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
            "reason": "Historical simulator inputs failed canonical validation.",
            "validation": validation,
            "same_gm3_core_as_current_trade_analysis": True,
            "current_values_used": False,
            "standalone_historical_score_used": False,
            "authoritative_recommendation_allowed": False,
        }

    hypothetical, pick_transfers = dl.apply_actions(canonical, actions)
    baseline_lineups = frozen_cached_lineups(bundle, canonical)
    baseline_lineup_source = "frozen_bundle_cache" if baseline_lineups is not None else "canonical_reoptimization"
    if baseline_lineups is None:
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
        cmp = {"before": b, "after": a, "delta": delta, "strategic": strategic}
        rows[uid] = {
            **cmp,
            "decision": dl.classify_decision(cmp, state_label),
            "team_state": state_label,
        }

    basis = status.get("historical_input_class")
    is_archived = basis == "ARCHIVED_AT_TIME"
    return {
        "adapter_model_version": MODEL_VERSION,
        "status": "GRADED_ARCHIVED_AT_TIME" if is_archived else "GRADED_RECONSTRUCTED_AT_TIME",
        "historical_input_class": basis,
        "strict_out_of_sample_backtest_eligible": bool(status.get("strict_out_of_sample_backtest_eligible")),
        "authoritative_recommendation_allowed": bool(status.get("authoritative_recommendation_allowed")),
        "recommendation_authority": "AUTHORITATIVE_ELIGIBLE" if status.get("authoritative_recommendation_allowed") else "NON_AUTHORITATIVE_RECONSTRUCTION",
        "authority_reason": None if status.get("authoritative_recommendation_allowed") else (
            status.get("reason") or
            "Reconstructed-at-time inputs preserve analysis functionality but include bounded, unvalidated reconstruction assumptions; "
            "the result cannot be used as an authoritative recommendation or pristine out-of-sample validation observation."
        ),
        "archive_certified": bool(status.get("archive_certified")),
        "same_gm3_core_as_current_trade_analysis": True,
        "decision_lab_model_version": dl.MODEL_VERSION,
        "simulator_model_version": before.get("model_version"),
        "n_sims": int(sims),
        "seed": int(seed),
        "common_random_numbers": True,
        "current_values_used": False,
        "standalone_historical_score_used": False,
        "baseline_lineup_source": baseline_lineup_source,
        "teams_reoptimized": reoptimized,
        "pick_transfers": pick_transfers,
        "team_results": rows,
        "input_bundle_provenance": bundle.get("provenance") or {},
    }
