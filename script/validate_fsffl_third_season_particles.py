#!/usr/bin/env python3
"""Structural validation for 2025 alternate-history season propagation."""
from __future__ import annotations
import json
from pathlib import Path
import alternate_history_engine as ah
from run_fsffl_downstream_dependencies import load
from run_fsffl_third_season_particles import run

SCENARIO = Path("data/alternate_history/scenarios/puka_vs_van_2023.json")
PARTICLES = 6
SEED = 20260824


def main() -> None:
    report = load(run(SCENARIO, particles=PARTICLES, seed=SEED))
    summary = report.get("summary") or {}
    invariants = report.get("design_invariants") or {}
    errors = []
    if str(report.get("season")) != "2025": errors.append("expected 2025 season")
    if str(report.get("following_draft_season")) != "2026": errors.append("expected 2026 draft handoff")
    if int(summary.get("final_particles") or 0) != PARTICLES: errors.append("particle mass not conserved")
    if abs(float(summary.get("final_probability_mass") or 0)-1.0) > 1e-9: errors.append("probability mass not conserved")
    if int(summary.get("input_postdraft_unique_states") or 0) <= 0: errors.append("no 2025 postdraft input states")
    if int(summary.get("final_unique_states") or 0) <= 0: errors.append("no end-2025 states")
    if not report.get("week_audit"): errors.append("2025 weekly scoring audit empty")
    if not report.get("focus_2026_draft_slot_distribution"): errors.append("2026 draft-slot distribution empty")
    for key in ("completed_nfl_fantasy_points_are_immutable","current_week_points_never_choose_current_week_lineup","postdraft_events_reclassified_from_branch_state","season_feedback_part_of_state_identity","stateful_2025_rookie_draft_is_input"):
        if invariants.get(key) is not True: errors.append(f"required invariant false: {key}")
    for key in ("current_gm3_numeric_values_used","future_nfl_outcomes_used_for_historical_decisions","particle_probability_mass_pruned"):
        if invariants.get(key) is not False: errors.append(f"forbidden invariant true: {key}")
    result={"status":"PASS" if not errors else "FAIL","errors":errors,"season":report.get("season"),"final_particles":summary.get("final_particles"),"final_unique_states":summary.get("final_unique_states")}
    print(json.dumps(result, indent=2, sort_keys=True))
    if errors: raise ah.AlternateHistoryError(f"Third-season validation failed: {errors[:10]}")

if __name__ == "__main__": main()
