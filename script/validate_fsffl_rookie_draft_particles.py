#!/usr/bin/env python3
"""Hard structural validation for 0.7f branch-specific rookie drafts."""

from __future__ import annotations

import json
from pathlib import Path

import alternate_history_engine as ah
from run_fsffl_downstream_dependencies import load
from run_fsffl_rookie_draft_particles import run

SCENARIO = Path("data/alternate_history/scenarios/puka_vs_van_2023.json")
PARTICLES = 20
SEED = 20260824
EXPECTED_TEAMS = 12
EXPECTED_ROUNDS = 3
EXPECTED_PICKS = EXPECTED_TEAMS * EXPECTED_ROUNDS


def main() -> None:
    out = run(SCENARIO, particles=PARTICLES, seed=SEED)
    report = load(out)
    summary = report.get("summary") or {}
    errors = []

    if int(summary.get("final_particles") or 0) != PARTICLES:
        errors.append("particle count not conserved")
    if abs(float(summary.get("final_probability_mass") or 0.0) - 1.0) > 1e-10:
        errors.append("probability mass not conserved")
    if int(summary.get("final_unique_postdraft_states") or 0) <= 0:
        errors.append("zero postdraft states")

    controllers = report.get("pick_controller_probabilities_at_selection") or {}
    if len(controllers) != EXPECTED_PICKS:
        errors.append(f"controller distributions cover {len(controllers)} pick assets, expected {EXPECTED_PICKS}")
    for asset, rows in controllers.items():
        mass = sum(float(row.get("probability") or 0.0) for row in rows or [])
        if abs(mass - 1.0) > 1e-8:
            errors.append(f"controller probability for {asset} sums to {mass}")
            break

    reps = report.get("representative_postdraft_states") or []
    if not reps:
        errors.append("no representative postdraft states")
    for idx, row in enumerate(reps):
        draft = row.get("draft") or {}
        picks = draft.get("selections") or []
        if len(picks) != EXPECTED_PICKS:
            errors.append(f"representative draft {idx} has {len(picks)} picks, expected {EXPECTED_PICKS}")
            break
        pick_nos = sorted(int(p.get("pick_no") or 0) for p in picks)
        if pick_nos != list(range(1, EXPECTED_PICKS + 1)):
            errors.append(f"representative draft {idx} does not cover pick numbers 1-{EXPECTED_PICKS}")
            break
        players = [str(p.get("player_id") or "") for p in picks]
        if len(players) != len(set(players)) or any(not pid for pid in players):
            errors.append(f"representative draft {idx} contains duplicate/empty player selections")
            break
        assets = [str(p.get("pick_asset_key") or "") for p in picks]
        if len(assets) != len(set(assets)) or any(not key for key in assets):
            errors.append(f"representative draft {idx} contains duplicate/empty pick assets")
            break
        if any(not p.get("controller_roster_id") for p in picks):
            errors.append(f"representative draft {idx} contains unresolved pick controller")
            break

    focus = report.get("focus_draft_selection_distributions") or []
    if not focus or len(focus) != EXPECTED_ROUNDS:
        errors.append("focus draft distributions do not cover all rounds")

    boundary_ms = summary.get("season_boundary_cutoff_timestamp_ms")
    draft_ms = summary.get("draft_timestamp_ms")
    if boundary_ms is not None and draft_ms is not None and int(draft_ms) < int(boundary_ms):
        errors.append("draft timestamp precedes season-boundary cutoff")

    if errors:
        raise ah.AlternateHistoryError("0.7f validation failed: " + "; ".join(errors))

    print(json.dumps({
        "status": "PASS",
        "particles": PARTICLES,
        "predraft_events_replayed": summary.get("predraft_events_replayed"),
        "final_unique_postdraft_states": summary.get("final_unique_postdraft_states"),
        "draft_timestamp_ms": draft_ms,
        "focus_draft_selection_distributions": focus,
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
