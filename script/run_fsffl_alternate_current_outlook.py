#!/usr/bin/env python3
"""Alternate History current-season outlook for the deterministic reference path.

This is a bridge to the existing Simulator 1.0/GM counterfactual simulation
engine. Historical NFL outcomes remain immutable; only once the alternate
history reaches the current league state do we use current/future Monte Carlo.

The output is explicitly a REFERENCE PATH, not the final branch-weighted
expected alternate outlook.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
from typing import Any, Dict

import alternate_history_engine as ah
from run_fsffl_downstream_dependencies import load
from run_fsffl_reference_present_day import run as run_reference

# Import the existing paired current-season simulation engine. We intentionally
# reuse its validated Simulator 1.0 pathway rather than duplicate simulation.
from run_fsffl_gm30_counterfactual import CounterfactualEngine

DEFAULT_SIMS = int(os.getenv("ALTERNATE_HISTORY_CURRENT_SIMS", "2500"))


def mutate_from_differences(rosters, differences):
    out = copy.deepcopy(rosters)
    by_rid = {str(r.get("roster_id")): r for r in out}

    for diff in differences:
        pid = str(diff.get("player_id"))
        target = diff.get("alternate_roster_id")
        # Remove from every current roster/list first so player ownership remains unique.
        for roster in out:
            for key in ("players", "reserve", "taxi"):
                roster[key] = [
                    str(x) for x in (roster.get(key) or []) if str(x) != pid
                ]
        if target is not None:
            roster = by_rid.get(str(target))
            if roster is None:
                raise ah.AlternateHistoryError(f"Alternate target roster {target} missing")
            players = [str(x) for x in (roster.get("players") or [])]
            if pid not in players:
                players.append(pid)
            roster["players"] = players
    return out


def team(result: Dict[str, Any], uid: str) -> Dict[str, Any]:
    return next(
        (x for x in (result.get("teams") or []) if str(x.get("user_id")) == str(uid)),
        {},
    )


def metric_delta(after: Dict[str, Any], before: Dict[str, Any], key: str):
    a, b = after.get(key), before.get(key)
    if a is None or b is None:
        return None
    return round(float(a) - float(b), 5)


def run(scenario_path: Path, n_sims: int = DEFAULT_SIMS) -> Path:
    reference_path = run_reference(scenario_path)
    reference = load(reference_path)

    engine = CounterfactualEngine()
    baseline = engine.baseline(int(n_sims))
    mutated_rosters = mutate_from_differences(
        engine.rosters,
        reference.get("player_ownership_differences") or [],
    )
    alternate = engine._run(mutated_rosters, int(n_sims))

    focus_rid = str(load(scenario_path).get("focus_roster_id") or "")
    if not focus_rid:
        # Scenario normally resolves owner name rather than explicit roster ID.
        from run_fsffl_alternate_history import FSFFLHistoricalAdapter
        adapter = FSFFLHistoricalAdapter()
        scenario = ah.scenario_from_json(adapter, load(scenario_path))
        focus_rid = str(scenario.focus_roster_id)

    focus_uid = engine.roster_id_to_uid.get(int(focus_rid))
    if focus_uid is None:
        raise ah.AlternateHistoryError(f"Unable to resolve focus user for roster {focus_rid}")

    before = team(baseline, focus_uid)
    after = team(alternate, focus_uid)
    keys = [
        "expected_points",
        "expected_wins",
        "playoff_probability",
        "bye_probability",
        "championship_probability",
    ]
    deltas = {k: metric_delta(after, before, k) for k in keys}

    report = {
        "model_version": "Fantasy-Alternate-History-current-outlook-reference-0.8",
        "scenario_id": reference.get("scenario_id"),
        "status": "REFERENCE_PATH_NOT_BRANCH_WEIGHTED",
        "n_sims": int(n_sims),
        "focus_roster_id": focus_rid,
        "focus_user_id": focus_uid,
        "design_invariants": {
            "completed_historical_nfl_outcomes_are_immutable": True,
            "current_future_boundary_uses_existing_simulator_1_0": True,
            "baseline_and_alternate_use_same_deterministic_seed": True,
            "canonical_simulator_and_gm_artifacts_are_read_only": True,
        },
        "actual_current_outlook": before,
        "reference_alternate_current_outlook": after,
        "deltas": deltas,
        "ownership_differences_applied": reference.get("player_ownership_differences") or [],
        "warning": (
            "This uses the deterministic preserved-history reference roster. It is not the final expected alternate timeline. "
            "Behavioral trade/add-drop branches, alternate drafts, and branch weights must be completed before these current-season probabilities are user-facing final estimates."
        ),
    }
    return ah.write_isolated_json(
        f"results/{reference.get('scenario_id')}/current_outlook_reference.json", report
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run current Simulator 1.0 outlook for Alternate History reference path")
    parser.add_argument("scenario", type=Path)
    parser.add_argument("--sims", type=int, default=DEFAULT_SIMS)
    args = parser.parse_args()
    out = run(args.scenario, args.sims)
    report = load(out)
    print(out)
    print(json.dumps({
        "n_sims": report["n_sims"],
        "deltas": report["deltas"],
        "ownership_differences_applied": report["ownership_differences_applied"],
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
