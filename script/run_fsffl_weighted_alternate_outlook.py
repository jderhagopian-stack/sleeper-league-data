#!/usr/bin/env python3
"""Probability-weighted Simulator 1.0 outlook for arbitrary historical forks.

Historical replay is delegated to the generic season-cycle engine, so a fork in
2022, 2023, or any future completed season reaches the active-season boundary
through the same orchestration. Simulator 1.0 runs only after that boundary.
"""

from __future__ import annotations

import argparse
import copy
import json
import math
from pathlib import Path
from typing import Any, Dict, List, Tuple

import alternate_history_engine as ah
import run_fsffl_generic_alternate_history as generic
from run_fsffl_alternate_history import FSFFLHistoricalAdapter
from run_fsffl_downstream_dependencies import load
from run_fsffl_gm30_counterfactual import CounterfactualEngine

DEFAULT_PARTICLES = 5000
DEFAULT_SIMS = 5000
DEFAULT_SEED = 20260824
METRICS = [
    "expected_points_for",
    "expected_wins",
    "playoff_probability",
    "bye_probability",
    "championship_probability",
]


def simulator_rosters_from_state(engine: CounterfactualEngine, state: Dict[str, Any]):
    rosters = copy.deepcopy(engine.rosters)
    by_rid = {str(row.get("roster_id")): row for row in rosters}
    branch_players = state.get("roster_players") or {}
    branch_reserve = state.get("roster_reserve") or {}
    branch_taxi = state.get("roster_taxi") or {}
    for rid, roster in by_rid.items():
        if rid in branch_players:
            roster["players"] = sorted(str(x) for x in (branch_players.get(rid) or []))
        if rid in branch_reserve:
            roster["reserve"] = sorted(str(x) for x in (branch_reserve.get(rid) or []))
        if rid in branch_taxi:
            roster["taxi"] = sorted(str(x) for x in (branch_taxi.get(rid) or []))
    return rosters


def allocate_sims(groups, total_sims: int) -> List[int]:
    """Allocate simulation draws across every state while preserving total budget."""
    n = len(groups)
    if n <= 0:
        raise ah.AlternateHistoryError("weighted outlook received no present-day states")
    if total_sims < n:
        raise ah.AlternateHistoryError(
            f"weighted outlook needs at least one Simulator draw per state: sims={total_sims}, states={n}"
        )
    total_particles = sum(group.count for group in groups)
    remaining = total_sims - n
    raw = [remaining * group.count / total_particles for group in groups]
    base = [1 + int(math.floor(x)) for x in raw]
    leftover = total_sims - sum(base)
    order = sorted(
        range(n),
        key=lambda i: (-(raw[i] - math.floor(raw[i])), -groups[i].count, i),
    )
    for i in order[:leftover]:
        base[i] += 1
    if sum(base) != total_sims or any(x <= 0 for x in base):
        raise ah.AlternateHistoryError("weighted simulation allocation invariant failed")
    return base


def team(result: Dict[str, Any], uid: str) -> Dict[str, Any]:
    return next(
        (row for row in (result.get("teams") or []) if str(row.get("user_id")) == str(uid)),
        {},
    )


def weighted_metric(rows: List[Tuple[float, Dict[str, Any]]], key: str):
    vals = [(weight, row.get(key)) for weight, row in rows if row.get(key) is not None]
    if not vals:
        return None
    covered = sum(weight for weight, _ in vals)
    if covered <= 0:
        return None
    return round(sum(weight * float(value) for weight, value in vals) / covered, 6)


def build_present_groups(scenario_path: Path, *, particles: int, seed: int):
    _, groups, generic_report = generic.run_generic(
        scenario_path,
        particles=particles,
        seed=seed,
        return_groups=True,
    )
    payload = load(scenario_path) or {}
    adapter = FSFFLHistoricalAdapter()
    scenario = ah.scenario_from_json(adapter, payload)
    active_phase = next(
        (row for row in reversed(generic_report.get("phase_audit") or []) if row.get("phase") == "active_season_to_now"),
        {},
    )
    return scenario, groups, {
        "season": str(generic_report.get("active_season") or ""),
        "events_processed": int(active_phase.get("events_processed") or 0),
        "generic_model_version": generic_report.get("model_version"),
    }


def run(
    scenario_path: Path,
    *,
    particles: int = DEFAULT_PARTICLES,
    n_sims: int = DEFAULT_SIMS,
    seed: int = DEFAULT_SEED,
) -> Path:
    scenario, groups, current_meta = build_present_groups(
        scenario_path, particles=particles, seed=seed
    )
    groups = sorted(groups, key=lambda group: (-group.count, json.dumps(group.state, sort_keys=True)))
    allocations = allocate_sims(groups, int(n_sims))

    engine = CounterfactualEngine()
    focus_rid = int(scenario.focus_roster_id)
    focus_uid = engine.roster_id_to_uid.get(focus_rid)
    if focus_uid is None:
        raise ah.AlternateHistoryError(f"weighted outlook unable to resolve focus roster {focus_rid}")

    baseline = engine.baseline(int(n_sims))
    baseline_focus = team(baseline, focus_uid)
    weighted_rows: List[Tuple[float, Dict[str, Any]]] = []
    state_results = []
    total_particles = sum(group.count for group in groups)

    for idx, (group, sims) in enumerate(zip(groups, allocations)):
        weight = group.count / total_particles
        rosters = simulator_rosters_from_state(engine, group.state)
        result = engine._run(rosters, int(sims))
        focus_team = team(result, focus_uid)
        weighted_rows.append((weight, focus_team))
        state_results.append({
            "state_index": idx,
            "particles": group.count,
            "probability": round(weight, 8),
            "simulations": int(sims),
            "focus_outlook": {key: focus_team.get(key) for key in METRICS},
        })

    alternate = {key: weighted_metric(weighted_rows, key) for key in METRICS}
    actual = {key: baseline_focus.get(key) for key in METRICS}
    deltas = {
        key: None if alternate.get(key) is None or actual.get(key) is None
        else round(float(alternate[key]) - float(actual[key]), 6)
        for key in METRICS
    }

    report = {
        "model_version": "Fantasy-Alternate-History-1.0-generic-weighted-simulator",
        "scenario_id": scenario.scenario_id,
        "configuration": {
            "historical_particles": int(particles),
            "simulator_total_sims": int(n_sims),
            "seed": int(seed),
        },
        "design_invariants": {
            "generic_arbitrary_year_historical_engine_used": True,
            "completed_historical_nfl_outcomes_are_immutable": True,
            "simulator_1_0_runs_only_after_present_day_boundary": True,
            "all_present_day_states_receive_simulator_coverage": True,
            "alternate_outlook_weighted_by_particle_probability": True,
            "canonical_simulator_inputs_are_read_only": True,
        },
        "summary": {
            "present_day_unique_states": len(groups),
            "present_day_probability_mass": 1.0,
            "simulated_state_probability_mass": round(sum(group.count for group in groups) / total_particles, 8),
            "simulator_draws_allocated": sum(allocations),
            "active_season": current_meta["season"],
            "active_season_transactions_processed": current_meta["events_processed"],
        },
        "actual_current_outlook": actual,
        "weighted_alternate_current_outlook": alternate,
        "deltas": deltas,
        "state_simulation_allocations": state_results,
    }
    out = ah.write_isolated_json(
        f"results/{scenario.scenario_id}/weighted_current_outlook_1_0_generic.json", report
    )
    print(out)
    print(json.dumps({"summary": report["summary"], "deltas": deltas}, indent=2, sort_keys=True))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Run probability-weighted Simulator 1.0 across generic present-day alternate states")
    parser.add_argument("scenario", type=Path)
    parser.add_argument("--particles", type=int, default=DEFAULT_PARTICLES)
    parser.add_argument("--sims", type=int, default=DEFAULT_SIMS)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args()
    run(args.scenario, particles=args.particles, n_sims=args.sims, seed=args.seed)


if __name__ == "__main__":
    main()
