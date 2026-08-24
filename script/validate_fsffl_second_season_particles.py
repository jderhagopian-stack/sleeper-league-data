#!/usr/bin/env python3
"""Structural validation for dynamic second-season alternate-history replay."""

from __future__ import annotations

import json
from pathlib import Path

import alternate_history_engine as ah
from run_fsffl_downstream_dependencies import load
from run_fsffl_second_season_particles import run

SCENARIO = Path("data/alternate_history/scenarios/puka_vs_van_2023.json")
PARTICLES = 12
SEED = 20260824


def main() -> None:
    out = run(SCENARIO, particles=PARTICLES, seed=SEED)
    report = load(out)
    summary = report.get("summary") or {}
    errors = []

    if int(summary.get("final_particles") or 0) != PARTICLES:
        errors.append("second-season particle count was not conserved")
    if abs(float(summary.get("final_probability_mass") or 0.0) - 1.0) > 1e-9:
        errors.append("second-season probability mass was not conserved")
    if int(summary.get("postdraft_events_processed") or 0) <= 0:
        errors.append("second-season replay processed zero postdraft events")
    if int(summary.get("actual_pre_event_states_reconstructed") or 0) <= 0:
        errors.append("dynamic policy did not reconstruct/cache any actual pre-event states")

    slot_dist = report.get("focus_2025_draft_slot_distribution") or []
    if not slot_dist:
        errors.append("focus 2025 draft-slot distribution is empty")
    slot_mass = sum(float(row.get("probability") or 0.0) for row in slot_dist)
    if slot_dist and abs(slot_mass - 1.0) > 1e-6:
        errors.append(f"focus 2025 draft-slot probability mass != 1: {slot_mass}")

    champ_dist = report.get("champion_distribution") or []
    champ_mass = sum(float(row.get("probability") or 0.0) for row in champ_dist)
    if not champ_dist:
        errors.append("champion distribution is empty")
    elif abs(champ_mass - 1.0) > 1e-6:
        errors.append(f"champion probability mass != 1: {champ_mass}")

    for row in report.get("representative_second_season_states") or []:
        slots = row.get("full_following_draft_slots") or {}
        values = sorted(int(x) for x in slots.values())
        if len(slots) != 12 or values != list(range(1, 13)):
            errors.append("representative state does not contain a valid complete 1-12 draft slot map")
            break

    result = {
        "status": "PASS" if not errors else "FAIL",
        "errors": errors,
        "postdraft_events_processed": summary.get("postdraft_events_processed"),
        "final_particles": summary.get("final_particles"),
        "final_unique_second_season_states": summary.get("final_unique_second_season_states"),
        "dynamic_policy_event_counts": summary.get("dynamic_policy_event_counts"),
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    if errors:
        raise ah.AlternateHistoryError(f"Second-season validation failed: {errors[:10]}")


if __name__ == "__main__":
    main()
