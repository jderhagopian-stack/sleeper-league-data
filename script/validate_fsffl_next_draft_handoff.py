#!/usr/bin/env python3
"""Structural validation for the 2025 alternate rookie-draft handoff."""

from __future__ import annotations

import json
from pathlib import Path

import alternate_history_engine as ah
from run_fsffl_downstream_dependencies import load
from run_fsffl_next_draft_handoff import run

SCENARIO = Path("data/alternate_history/scenarios/puka_vs_van_2023.json")
PARTICLES = 8
SEED = 20260824


def main() -> None:
    out = run(SCENARIO, particles=PARTICLES, seed=SEED)
    report = load(out)
    summary = report.get("summary") or {}
    invariants = report.get("design_invariants") or {}
    errors = []

    if str(report.get("draft_season")) != "2025":
        errors.append(f"expected 2025 draft handoff, got {report.get('draft_season')}")
    if int(summary.get("input_particles") or 0) != PARTICLES:
        errors.append("input particle count mismatch")
    if int(summary.get("final_particles") or 0) != PARTICLES:
        errors.append("next-draft particle count was not conserved")
    if abs(float(summary.get("final_probability_mass") or 0.0) - 1.0) > 1e-9:
        errors.append("next-draft probability mass was not conserved")
    if int(summary.get("draft_picks_simulated") or 0) <= 0:
        errors.append("no 2025 rookie draft picks were simulated")
    if int(summary.get("final_unique_postdraft_states") or 0) <= 0:
        errors.append("no postdraft states survived")

    audit = report.get("draft_pick_audit") or []
    if len(audit) != int(summary.get("draft_picks_simulated") or 0):
        errors.append("draft pick audit length does not match simulated pick count")
    for row in audit:
        controllers = sum(int(x) for x in (row.get("controller_counts") or {}).values())
        selections = sum(int(x) for x in (row.get("selection_counts") or {}).values())
        if controllers != PARTICLES or selections != PARTICLES:
            errors.append(
                f"pick {row.get('pick_no')} did not conserve controller/selection particles"
            )
            break

    required_true = [
        "completed_nfl_outcomes_are_immutable",
        "historical_same_draft_market_only",
        "branch_specific_pick_ownership_used",
        "branch_specific_roster_need_used",
    ]
    for key in required_true:
        if invariants.get(key) is not True:
            errors.append(f"required invariant is not true: {key}")
    required_false = [
        "future_nfl_outcomes_used_for_draft_decisions",
        "current_gm3_numeric_values_used",
        "particle_probability_mass_pruned",
    ]
    for key in required_false:
        if invariants.get(key) is not False:
            errors.append(f"required invariant is not false: {key}")

    if not (report.get("focus_2025_draft_outcome_distribution") or []):
        errors.append("focus 2025 draft outcome distribution is empty")
    if not (report.get("representative_postdraft_states") or []):
        errors.append("representative 2025 postdraft states are empty")

    result = {
        "status": "PASS" if not errors else "FAIL",
        "errors": errors,
        "draft_season": report.get("draft_season"),
        "draft_picks_simulated": summary.get("draft_picks_simulated"),
        "final_particles": summary.get("final_particles"),
        "final_unique_postdraft_states": summary.get("final_unique_postdraft_states"),
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    if errors:
        raise ah.AlternateHistoryError(f"Next-draft handoff validation failed: {errors[:10]}")


if __name__ == "__main__":
    main()
