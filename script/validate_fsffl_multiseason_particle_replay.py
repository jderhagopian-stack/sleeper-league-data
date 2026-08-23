#!/usr/bin/env python3
"""Validate FSFFL Alternate History 0.7b grouped particle replay.

CI uses a deliberately small particle count because this test is structural:
conservation, branch legality, and seeded reproducibility. Production/convergence
runs use materially larger samples and are validated separately.
"""

from __future__ import annotations

import json
from pathlib import Path

import alternate_history_engine as ah
from run_fsffl_downstream_dependencies import load
from run_fsffl_multiseason_particle_replay import run

SCENARIO = Path("data/alternate_history/scenarios/puka_vs_van_2023.json")
PARTICLES = 250
SEED = 20260824


def signature(report):
    return ah.stable_hash({
        "summary": report.get("summary"),
        "focus": report.get("focus_roster_player_probabilities"),
        "picks": report.get("pick_owner_probabilities"),
        "groups": report.get("representative_state_groups"),
    })


def main() -> None:
    first_path = run(SCENARIO, particles=PARTICLES, seed=SEED)
    first = load(first_path)
    first_sig = signature(first)
    second_path = run(SCENARIO, particles=PARTICLES, seed=SEED)
    second = load(second_path)
    second_sig = signature(second)

    summary = first.get("summary") or {}
    errors = []
    if int(summary.get("final_particles") or 0) != PARTICLES:
        errors.append("particle count not conserved")
    if abs(float(summary.get("final_probability_mass") or 0.0) - 1.0) > 1e-10:
        errors.append("probability mass not conserved")
    if int(summary.get("final_unique_states") or 0) <= 0:
        errors.append("zero final unique states")
    if int(summary.get("audited_sensitive_or_legality_events") or 0) <= 0:
        errors.append("no sensitive events audited")
    if first_sig != second_sig:
        errors.append("same seed is not reproducible")
    if errors:
        raise ah.AlternateHistoryError("0.7b validation failed: " + "; ".join(errors))

    print(json.dumps({
        "status": "PASS",
        "particles": PARTICLES,
        "final_unique_states": summary.get("final_unique_states"),
        "max_unique_states": summary.get("max_unique_states"),
        "final_probability_mass": summary.get("final_probability_mass"),
        "reproducible_signature": first_sig,
        "note": "Structural CI sample only; production/convergence runs use larger particle counts.",
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
