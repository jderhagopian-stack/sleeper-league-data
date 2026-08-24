#!/usr/bin/env python3
"""Hard validation for 0.7d fork-season particle feedback."""

from __future__ import annotations

import json
from pathlib import Path

import alternate_history_engine as ah
from run_fsffl_downstream_dependencies import load
from run_fsffl_multiseason_particle_replay_v3 import run

SCENARIO = Path("data/alternate_history/scenarios/puka_vs_van_2023.json")
PARTICLES = 50
SEED = 20260824


def main() -> None:
    out = run(SCENARIO, particles=PARTICLES, seed=SEED)
    report = load(out)
    summary = report.get("summary") or {}
    errors = []

    if int(summary.get("final_particles") or 0) != PARTICLES:
        errors.append("particle count not conserved")
    if abs(float(summary.get("final_probability_mass") or 0.0) - 1.0) > 1e-10:
        errors.append("particle probability mass not conserved")
    if int(summary.get("final_unique_states_with_season_feedback") or 0) <= 0:
        errors.append("zero final season-feedback states")
    if int(summary.get("particles_with_any_scoring_data_gap") or 0) != 0:
        errors.append(
            f"scoring gaps in {summary.get('particles_with_any_scoring_data_gap')} particles"
        )

    week_audit = report.get("week_audit") or []
    weeks = sorted(int(row.get("week")) for row in week_audit)
    expected_weeks = list(range(1, int(report.get("playoff_week_start") or 15)))
    if weeks != expected_weeks:
        errors.append(f"regular-season weeks scored {weeks}, expected {expected_weeks}")

    seed_rows = report.get("focus_seed_distribution") or []
    seed_mass = sum(float(row.get("probability") or 0.0) for row in seed_rows)
    if abs(seed_mass - 1.0) > 1e-8:
        errors.append(f"focus seed distribution mass != 1: {seed_mass}")

    reps = report.get("representative_state_groups") or []
    if not reps:
        errors.append("no representative season states")
    for idx, row in enumerate(reps):
        slots = row.get("nonplayoff_draft_slots") or {}
        if len(slots) != 6 or sorted(int(x) for x in slots.values()) != [1, 2, 3, 4, 5, 6]:
            errors.append(f"representative state {idx} has invalid nonplayoff slot map")
            break

    if errors:
        raise ah.AlternateHistoryError("0.7d validation failed: " + "; ".join(errors))

    print(json.dumps({
        "status": "PASS",
        "particles": PARTICLES,
        "final_unique_states": summary.get("final_unique_states_with_season_feedback"),
        "weeks_scored": weeks,
        "scoring_gap_particles": summary.get("particles_with_any_scoring_data_gap"),
        "focus_seed_distribution": seed_rows,
        "nonplayoff_draft_slot_probabilities": report.get("nonplayoff_draft_slot_probabilities"),
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
