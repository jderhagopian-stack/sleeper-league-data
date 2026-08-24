#!/usr/bin/env python3
"""Validate arbitrary-year FSFFL Alternate History orchestration.

Two checks:
1. dynamically construct a real 2022 add/drop fork and carry it to the active year;
2. run the established 2023 Puka fork through the same generic loop.
"""

from __future__ import annotations

import json
from pathlib import Path

import alternate_history_engine as ah
import alternate_history_season_cycle as cycle
import run_fsffl_generic_alternate_history as generic
from run_fsffl_alternate_history import FSFFLHistoricalAdapter
from run_fsffl_downstream_dependencies import load

PREFERRED = Path("data/alternate_history/scenarios/puka_vs_van_2023.json")
GENERATED = ah.AH_ROOT / "scenarios" / "generated_2022_generic_regression.json"
PARTICLES_2022 = 6
PARTICLES_2023 = 6
SEED = 20260824


def event_season(event):
    return str(event.get("source_season") or (event.get("metadata") or {}).get("source_season") or "")


def build_2022_scenario() -> Path:
    adapter = FSFFLHistoricalAdapter()
    candidates = []
    for event in adapter.completed_events():
        if event_season(event) != "2022":
            continue
        if str(event.get("type") or "") not in {"waiver", "free_agent"}:
            continue
        adds = {str(pid): str(rid) for pid, rid in (event.get("adds") or {}).items()}
        drops = {str(pid): str(rid) for pid, rid in (event.get("drops") or {}).items()}
        if len(adds) != 1 or len(drops) != 1:
            continue
        add_pid, add_rid = next(iter(adds.items()))
        drop_pid, drop_rid = next(iter(drops.items()))
        if add_rid != drop_rid:
            continue
        created = int(event.get("created") or 0)
        if created <= 0:
            continue
        week = event.get("leg") or event.get("week") or (event.get("metadata") or {}).get("leg") or 1
        try:
            week = int(week or 1)
        except (TypeError, ValueError):
            week = 1
        candidates.append((created, add_rid, add_pid, drop_pid, max(1, week)))

    if not candidates:
        raise ah.AlternateHistoryError("No usable real 2022 add/drop transaction found for generic regression")
    created, rid, add_pid, drop_pid, week = sorted(candidates)[0]
    payload = {
        "scenario_id": "generated-2022-generic-regression",
        "title": "Generated 2022 arbitrary-year regression",
        "league_profile": "fsffl",
        "fork_season": "2022",
        "fork_week": week,
        "fork_timestamp_ms": created - 1,
        "focus_roster_id": rid,
        "actions": [
            {
                "type": "player_swap",
                "roster_id": rid,
                "add_player_id": add_pid,
                "drop_player_id": drop_pid,
            }
        ],
        "notes": "Generated from the earliest usable real 2022 add/drop transaction; regression only.",
    }
    GENERATED.parent.mkdir(parents=True, exist_ok=True)
    GENERATED.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return GENERATED


def validate_report(report, *, expected_fork: int, particles: int) -> None:
    summary = report.get("summary") or {}
    invariants = report.get("design_invariants") or {}
    current = cycle.active_season()
    if int(summary.get("final_particles") or 0) != particles:
        raise ah.AlternateHistoryError("generic regression lost particle mass")
    if abs(float(summary.get("final_probability_mass") or 0.0) - 1.0) > 1e-9:
        raise ah.AlternateHistoryError("generic regression probability mass != 1")
    if int(summary.get("seasons_traversed") or 0) != current - expected_fork + 1:
        raise ah.AlternateHistoryError("generic regression traversed wrong season count")
    if int(summary.get("rookie_drafts_simulated") or 0) != current - expected_fork:
        raise ah.AlternateHistoryError("generic regression simulated wrong draft count")
    required = [
        "calendar_years_are_data_not_code",
        "completed_nfl_history_is_immutable",
        "full_probability_mass_conserved",
        "archived_completed_season_root_anchor",
        "active_season_predraft_rookie_leakage_prevented",
        "predraft_offseason_is_explicit_phase",
        "branch_specific_rookie_drafts",
        "branch_specific_downstream_transactions",
    ]
    for key in required:
        if invariants.get(key) is not True:
            raise ah.AlternateHistoryError(f"generic invariant failed: {key}")
    if invariants.get("active_season_nfl_games_simulated_here") is not False:
        raise ah.AlternateHistoryError("generic historical runner simulated active-season games")

    phases = report.get("phase_audit") or []
    rookie_years = {
        int(row.get("season")) for row in phases
        if row.get("phase") == "rookie_draft"
    }
    expected_years = set(range(expected_fork + 1, current + 1))
    if rookie_years != expected_years:
        raise ah.AlternateHistoryError(
            f"generic draft years mismatch expected={sorted(expected_years)} got={sorted(rookie_years)}"
        )
    if not any(row.get("phase") == "active_season_to_now" and int(row.get("season")) == current for row in phases):
        raise ah.AlternateHistoryError("generic regression did not reach active-season boundary")


def main() -> None:
    generated = build_2022_scenario()
    _, _, report_2022 = generic.run_generic(
        generated,
        particles=PARTICLES_2022,
        seed=SEED,
        return_groups=True,
    )
    validate_report(report_2022, expected_fork=2022, particles=PARTICLES_2022)

    _, _, report_2023 = generic.run_generic(
        PREFERRED,
        particles=PARTICLES_2023,
        seed=SEED,
        return_groups=True,
    )
    validate_report(report_2023, expected_fork=2023, particles=PARTICLES_2023)

    print("PASS: arbitrary-year engine traversed real 2022 and Puka 2023 forks to active season")


if __name__ == "__main__":
    main()
