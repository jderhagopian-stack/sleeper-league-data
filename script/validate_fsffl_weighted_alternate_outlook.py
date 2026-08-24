#!/usr/bin/env python3
"""Smoke-test the branch-weighted present-day Simulator 1.0 bridge."""

from pathlib import Path

import alternate_history_engine as ah
import run_fsffl_weighted_alternate_outlook as weighted
from run_fsffl_downstream_dependencies import load

SCENARIO = Path("data/alternate_history/scenarios/puka_vs_van_2023.json")


def main() -> None:
    out = weighted.run(SCENARIO, particles=8, n_sims=16, seed=20260824)
    report = load(out)
    summary = report.get("summary") or {}
    if int(summary.get("simulator_draws_allocated") or 0) != 16:
        raise ah.AlternateHistoryError("weighted outlook simulation budget mismatch")
    if abs(float(summary.get("simulated_state_probability_mass") or 0.0) - 1.0) > 1e-9:
        raise ah.AlternateHistoryError("weighted outlook did not cover full state probability mass")
    if not report.get("weighted_alternate_current_outlook"):
        raise ah.AlternateHistoryError("weighted outlook missing alternate result")
    invariants = report.get("design_invariants") or {}
    if invariants.get("all_present_day_states_receive_simulator_coverage") is not True:
        raise ah.AlternateHistoryError("not every present-day state received Simulator coverage")
    if invariants.get("simulator_1_0_runs_only_after_present_day_boundary") is not True:
        raise ah.AlternateHistoryError("Simulator boundary invariant failed")
    print("PASS: branch-weighted Alternate History -> Simulator 1.0 bridge")


if __name__ == "__main__":
    main()
