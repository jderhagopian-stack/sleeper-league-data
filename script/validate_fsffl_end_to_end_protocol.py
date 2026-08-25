#!/usr/bin/env python3
"""Single-pass validation of the complete Puka 2023 -> present-day -> Simulator 1.0 protocol.

This deliberately shares one historical particle replay between the present-day
state validation and the weighted current/future Simulator validation. It is a
CI efficiency harness only: model logic remains in the production stages.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import alternate_history_engine as ah
import alternate_history_performance_runtime as perf
import run_fsffl_present_day_particles as present
import run_fsffl_third_season_particles as third_season
import run_fsffl_weighted_alternate_outlook as weighted
from run_fsffl_gm30_counterfactual import CounterfactualEngine
from run_fsffl_next_draft_handoff import replay_rookie_draft_groups

SCENARIO = Path("data/alternate_history/scenarios/puka_vs_van_2023.json")
PARTICLES = 8
SIMS = 16
SEED = 20260824

REFERENCE_ACTUAL = {
    "bye_probability": 0.1875,
    "championship_probability": 0.1875,
    "expected_points_for": 1658.56,
    "expected_wins": 9.0,
    "playoff_probability": 0.875,
}
REFERENCE_ALTERNATE = {
    "bye_probability": 0.375,
    "championship_probability": 0.1875,
    "expected_points_for": 1610.7575,
    "expected_wins": 9.375,
    "playoff_probability": 0.9375,
}


def main() -> None:
    perf.install()
    timings = {}
    total_started = time.perf_counter()

    started = time.perf_counter()
    scenario, end_2025_groups, season_meta = present.build_end_2025_groups(
        SCENARIO, particles=PARTICLES, seed=SEED
    )
    timings["historical_replay_through_2025_seconds"] = round(time.perf_counter() - started, 4)

    started = time.perf_counter()
    groups, draft_meta = replay_rookie_draft_groups(
        end_2025_groups,
        completed_season="2025",
        draft_season="2026",
        particles=PARTICLES,
        seed=SEED,
    )
    timings["2026_rookie_draft_seconds"] = round(time.perf_counter() - started, 4)

    after_timestamp_ms = third_season.draft_boundary_timestamp("2026")
    started = time.perf_counter()
    groups, current_meta = present.replay_current_transactions(
        groups,
        particles=PARTICLES,
        seed=SEED,
        after_timestamp_ms=after_timestamp_ms,
    )
    timings["2026_current_transactions_seconds"] = round(time.perf_counter() - started, 4)

    final_particles = sum(group.count for group in groups)
    if final_particles != PARTICLES:
        raise ah.AlternateHistoryError("end-to-end validator lost particle mass")
    if int(draft_meta.get("draft_picks_simulated") or 0) != 36:
        raise ah.AlternateHistoryError("end-to-end validator did not replay all 36 2026 rookie picks")
    if int(current_meta.get("final_unique_states") or 0) <= 0:
        raise ah.AlternateHistoryError("end-to-end validator produced no present-day states")
    if abs(float(current_meta.get("final_probability_mass") or 0.0) - 1.0) > 1e-9:
        raise ah.AlternateHistoryError("present-day probability mass != 1")
    if int(season_meta.get("final_particles") or 0) != PARTICLES:
        raise ah.AlternateHistoryError("2025 handoff lost particle mass")

    groups = sorted(
        groups,
        key=lambda group: (-group.count, json.dumps(group.state, sort_keys=True)),
    )
    allocations = weighted.allocate_sims(groups, SIMS)
    if sum(allocations) != SIMS or any(value <= 0 for value in allocations):
        raise ah.AlternateHistoryError("Simulator allocation invariant failed")

    started = time.perf_counter()
    engine = CounterfactualEngine()
    timings["simulator_engine_init_seconds"] = round(time.perf_counter() - started, 4)
    focus_rid = int(scenario.focus_roster_id)
    focus_uid = engine.roster_id_to_uid.get(focus_rid)
    if focus_uid is None:
        raise ah.AlternateHistoryError(f"unable to resolve focus roster {focus_rid}")

    started = time.perf_counter()
    baseline = engine.baseline(SIMS)
    timings["baseline_simulator_seconds"] = round(time.perf_counter() - started, 4)
    baseline_focus = weighted.team(baseline, focus_uid)
    total_particles = sum(group.count for group in groups)
    weighted_rows = []
    state_runtime_audit = []
    started = time.perf_counter()
    for idx, (group, sims) in enumerate(zip(groups, allocations)):
        rosters = weighted.simulator_rosters_from_state(engine, group.state)
        roster_sizes = {
            str(row.get("roster_id")): len(row.get("players") or [])
            for row in rosters
        }
        state_started = time.perf_counter()
        result = engine._run(rosters, int(sims))
        wall = round(time.perf_counter() - state_started, 4)
        weighted_rows.append((group.count / total_particles, weighted.team(result, focus_uid)))
        state_runtime_audit.append({
            "state_index": idx,
            "particles": group.count,
            "simulations": int(sims),
            "wall_seconds": wall,
            "simulator_runtime": result.get("runtime") or {},
            "max_roster_size": max(roster_sizes.values()) if roster_sizes else 0,
            "min_roster_size": min(roster_sizes.values()) if roster_sizes else 0,
            "focus_roster_size": roster_sizes.get(str(focus_rid)),
            "roster_sizes": roster_sizes,
        })
    timings["alternate_state_simulator_seconds"] = round(time.perf_counter() - started, 4)

    alternate = {
        key: weighted.weighted_metric(weighted_rows, key)
        for key in weighted.METRICS
    }
    actual = {key: baseline_focus.get(key) for key in weighted.METRICS}
    if not alternate or all(value is None for value in alternate.values()):
        raise ah.AlternateHistoryError("weighted Simulator outlook is empty")

    if actual != REFERENCE_ACTUAL:
        raise ah.AlternateHistoryError(
            f"optimized runtime changed actual Simulator reference: {actual}"
        )
    if alternate != REFERENCE_ALTERNATE:
        raise ah.AlternateHistoryError(
            f"optimized runtime changed alternate Simulator reference: {alternate}"
        )

    timings["total_seconds"] = round(time.perf_counter() - total_started, 4)
    report = {
        "model_version": "Fantasy-Alternate-History-0.9c-end-to-end-validation",
        "scenario_id": scenario.scenario_id,
        "configuration": {
            "historical_particles": PARTICLES,
            "simulator_total_sims": SIMS,
            "seed": SEED,
            "optimized_runtime": True,
        },
        "design_invariants": {
            "completed_nfl_history_is_immutable": True,
            "historical_replay_executed_once": True,
            "2026_nfl_games_simulated_by_historical_engine": False,
            "branch_specific_2026_draft_used": True,
            "branch_specific_2026_transactions_used": True,
            "simulator_1_0_runs_only_after_present_day_boundary": True,
            "all_present_day_states_receive_simulator_coverage": True,
            "alternate_outlook_weighted_by_particle_probability": True,
            "current_gm3_numeric_values_used_for_historical_decisions": False,
            "future_nfl_outcomes_used_for_historical_decisions": False,
            "optimized_runtime_exact_output_reference_passed": True,
            "state_runtime_audit_is_observational_only": True,
        },
        "summary": {
            "final_particles": final_particles,
            "present_day_unique_states": len(groups),
            "present_day_probability_mass": 1.0,
            "2026_draft_picks_simulated": draft_meta["draft_picks_simulated"],
            "2026_transactions_processed": current_meta["events_processed"],
            "simulator_draws_allocated": sum(allocations),
        },
        "phase_timings_seconds": timings,
        "state_runtime_audit": state_runtime_audit,
        "actual_current_outlook": actual,
        "weighted_alternate_current_outlook": alternate,
    }
    out = ah.write_isolated_json(
        f"results/{scenario.scenario_id}/end_to_end_validation_0_9c.json", report
    )
    print(out)
    print("ALTERNATE_HISTORY_END_TO_END_REPORT")
    print(json.dumps(report, indent=2, sort_keys=True))
    print("PASS: Puka 2023 -> present-day weighted state -> Simulator 1.0 single-pass protocol")


if __name__ == "__main__":
    main()
