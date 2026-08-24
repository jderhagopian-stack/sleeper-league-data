#!/usr/bin/env python3
"""Validate the complete Puka 2023 -> present-day 2026 particle chain."""

from pathlib import Path

import alternate_history_engine as ah
import run_fsffl_present_day_particles as present
from run_fsffl_downstream_dependencies import load

SCENARIO = Path("data/alternate_history/scenarios/puka_vs_van_2023.json")
PARTICLES = 24
SEED = 20260824


def main() -> None:
    out = present.run(SCENARIO, particles=PARTICLES, seed=SEED)
    report = load(out)
    summary = report.get("summary") or {}
    if int(summary.get("final_particles") or 0) != PARTICLES:
        raise ah.AlternateHistoryError("present-day validator lost particle mass")
    if abs(float(summary.get("final_probability_mass") or 0.0) - 1.0) > 1e-9:
        raise ah.AlternateHistoryError("present-day validator probability mass != 1")
    if int(summary.get("2026_draft_picks_simulated") or 0) != 36:
        raise ah.AlternateHistoryError("present-day validator did not simulate all 36 2026 rookie picks")
    if int(summary.get("final_unique_states") or 0) <= 0:
        raise ah.AlternateHistoryError("present-day validator produced no states")
    invariants = report.get("design_invariants") or {}
    required_true = [
        "completed_nfl_history_is_immutable",
        "branch_specific_2026_draft_used",
        "branch_specific_2026_transactions_used",
        "simulator_1_0_boundary_reached",
    ]
    for key in required_true:
        if invariants.get(key) is not True:
            raise ah.AlternateHistoryError(f"present-day invariant failed: {key}")
    if invariants.get("2026_nfl_games_simulated_here") is not False:
        raise ah.AlternateHistoryError("historical engine must not simulate active-season NFL games")
    if not report.get("focus_roster_distribution"):
        raise ah.AlternateHistoryError("present-day validator missing focus roster distribution")
    print("PASS: complete Puka 2023 -> present-day 2026 alternate-history chain")


if __name__ == "__main__":
    main()
