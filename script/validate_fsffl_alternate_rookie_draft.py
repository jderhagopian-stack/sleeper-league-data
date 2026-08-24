#!/usr/bin/env python3
"""Structural validation for the state-aware alternate rookie draft engine."""

from __future__ import annotations

import json
from pathlib import Path

import alternate_history_engine as ah
from run_fsffl_alternate_rookie_draft_particles import run
from run_fsffl_downstream_dependencies import load

SCENARIO = Path("data/alternate_history/scenarios/puka_vs_van_2023.json")
PARTICLES = 30
SEED = 20260824


def main() -> None:
    out = run(SCENARIO, particles=PARTICLES, seed=SEED)
    report = load(out)
    summary = report.get("summary") or {}
    audits = report.get("pick_audit") or []
    errors = []

    if int(summary.get("draft_picks_simulated") or 0) != 36:
        errors.append("expected exactly 36 simulated rookie picks")
    if int(summary.get("final_particles") or 0) != PARTICLES:
        errors.append("particle count was not conserved")
    if abs(float(summary.get("final_probability_mass") or 0.0) - 1.0) > 1e-9:
        errors.append("probability mass was not conserved")
    if len(audits) != 36:
        errors.append(f"pick audit count {len(audits)} != 36")

    for row in audits:
        selection_mass = sum(float(x.get("probability") or 0.0) for x in row.get("selection_distribution") or [])
        controller_mass = sum(float(x.get("probability") or 0.0) for x in row.get("controller_distribution") or [])
        if abs(selection_mass - 1.0) > 1e-6:
            errors.append(f"selection mass != 1 at pick {row.get('pick_no')}: {selection_mass}")
        if abs(controller_mass - 1.0) > 1e-6:
            errors.append(f"controller mass != 1 at pick {row.get('pick_no')}: {controller_mass}")
        ids = [str(x.get("player_id")) for x in row.get("selection_distribution") or []]
        if len(ids) != len(set(ids)):
            errors.append(f"duplicate player entries inside pick distribution {row.get('pick_no')}")

    status = "PASS" if not errors else "FAIL"
    result = {
        "status": status,
        "errors": errors,
        "draft_picks_simulated": summary.get("draft_picks_simulated"),
        "final_particles": summary.get("final_particles"),
        "final_probability_mass": summary.get("final_probability_mass"),
        "final_unique_postdraft_states": summary.get("final_unique_postdraft_states"),
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    if errors:
        raise ah.AlternateHistoryError(f"Alternate rookie draft validation failed: {errors[:10]}")


if __name__ == "__main__":
    main()
