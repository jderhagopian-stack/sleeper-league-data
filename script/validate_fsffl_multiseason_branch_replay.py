#!/usr/bin/env python3
"""Hard validation for FSFFL Alternate History 0.7a v2 branch replay."""

from __future__ import annotations

import json
from pathlib import Path

import alternate_history_engine as ah
from run_fsffl_downstream_dependencies import load
from run_fsffl_multiseason_branch_replay_v2 import run

SCENARIO = Path("data/alternate_history/scenarios/puka_vs_van_2023.json")


def main() -> None:
    out = run(SCENARIO, max_branches=256)
    report = load(out)
    summary = report.get("summary") or {}
    branches = int(summary.get("final_retained_branches") or 0)
    conditional_mass = float(summary.get("final_conditional_probability_mass") or 0.0)
    coverage = float(summary.get("global_probability_coverage_retained") or 0.0)
    audited = int(summary.get("audited_branch_events") or 0)
    fast = int(summary.get("invariant_fast_path_events") or 0)

    errors = []
    if branches <= 0:
        errors.append("zero final branches")
    if abs(conditional_mass - 1.0) > 1e-8:
        errors.append(f"conditional branch mass != 1: {conditional_mass}")
    if coverage <= 0.0 or coverage > 1.0:
        errors.append(f"invalid global probability coverage: {coverage}")
    if audited <= 0:
        errors.append("no audited branch events")
    if fast <= 0:
        errors.append("invariant fast path was not exercised")
    if errors:
        raise ah.AlternateHistoryError("0.7a v2 validation failed: " + "; ".join(errors))

    print(json.dumps({
        "status": "PASS",
        "final_retained_branches": branches,
        "final_conditional_probability_mass": conditional_mass,
        "global_probability_coverage_retained": coverage,
        "audited_branch_events": audited,
        "invariant_fast_path_events": fast,
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
