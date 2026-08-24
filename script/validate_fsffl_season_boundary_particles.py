#!/usr/bin/env python3
"""Hard validation for 0.7e complete season-boundary particle states."""

from __future__ import annotations

import json
from pathlib import Path

import alternate_history_engine as ah
from run_fsffl_downstream_dependencies import load
from run_fsffl_season_boundary_particles import run

SCENARIO = Path("data/alternate_history/scenarios/puka_vs_van_2023.json")
PARTICLES = 50
SEED = 20260824


def probability_sum(rows):
    return sum(float(row.get("probability") or 0.0) for row in rows or [])


def main() -> None:
    out = run(SCENARIO, particles=PARTICLES, seed=SEED)
    report = load(out)
    summary = report.get("summary") or {}
    errors = []

    if int(summary.get("final_particles") or 0) != PARTICLES:
        errors.append("particle count not conserved")
    if abs(float(summary.get("final_probability_mass") or 0.0) - 1.0) > 1e-10:
        errors.append("probability mass not conserved")
    if int(summary.get("final_unique_season_boundary_states") or 0) <= 0:
        errors.append("zero season-boundary states")
    if float(summary.get("probability_with_scoring_data_gap") or 0.0) != 0.0:
        errors.append("season-boundary states contain scoring data gaps")

    focus_slots = report.get("focus_following_draft_slot_distribution") or []
    if abs(probability_sum(focus_slots) - 1.0) > 1e-8:
        errors.append("focus draft-slot distribution does not sum to 1")
    if any(not (1 <= int(row.get("slot") or 0) <= 12) for row in focus_slots):
        errors.append("focus draft-slot distribution contains invalid slot")

    champions = report.get("champion_distribution") or []
    if abs(probability_sum(champions) - 1.0) > 1e-8:
        errors.append("champion distribution does not sum to 1")

    finishes = report.get("focus_playoff_finish_distribution") or []
    if finishes and abs(probability_sum(finishes) - 1.0) > 1e-8:
        errors.append("focus playoff-finish distribution does not sum to 1")

    all_slot_probs = report.get("draft_slot_probabilities_by_original_roster") or {}
    if len(all_slot_probs) != 12:
        errors.append(f"draft-slot distributions cover {len(all_slot_probs)} rosters, expected 12")
    for rid, rows in all_slot_probs.items():
        if abs(probability_sum(rows) - 1.0) > 1e-8:
            errors.append(f"roster {rid} draft-slot probability does not sum to 1")
            break

    reps = report.get("representative_season_boundary_states") or []
    if not reps:
        errors.append("no representative season-boundary states")
    for idx, row in enumerate(reps):
        slots = row.get("full_following_draft_slots") or {}
        if len(slots) != 12 or sorted(int(x) for x in slots.values()) != list(range(1, 13)):
            errors.append(f"representative state {idx} does not contain exact slots 1-12")
            break
        post = row.get("postseason") or {}
        finish = post.get("finish_by_roster") or {}
        if len(finish) != 6 or sorted(int(x) for x in finish.values()) != [1, 2, 3, 4, 5, 6]:
            errors.append(f"representative state {idx} has invalid six-team playoff finish map")
            break

    week_audit = report.get("week_audit") or []
    weeks = sorted({int(row.get("week")) for row in week_audit})
    if weeks != list(range(1, 18)):
        errors.append(f"season-boundary scoring weeks {weeks}, expected 1-17")

    if errors:
        raise ah.AlternateHistoryError("0.7e validation failed: " + "; ".join(errors))

    print(json.dumps({
        "status": "PASS",
        "particles": PARTICLES,
        "final_unique_states": summary.get("final_unique_season_boundary_states"),
        "focus_draft_slots": focus_slots,
        "focus_playoff_finishes": finishes,
        "champions": champions,
        "weeks_scored": weeks,
        "scoring_gap_probability": summary.get("probability_with_scoring_data_gap"),
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
