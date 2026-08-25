#!/usr/bin/env python3
"""Exact equivalence checks for Alternate History performance replacements.

These checks compare optimized deterministic primitives directly against their
untouched reference implementations. Performance changes are allowed only when
outputs and exact state-equivalence classes remain unchanged.
"""

from __future__ import annotations

import copy

import alternate_history_performance_runtime as perf
import alternate_history_weekly_cow_runtime as weekly_cow
import run_fsffl_multiseason_particle_replay_v3 as season_v3
from run_fsffl_downstream_dependencies import load
from run_fsffl_counterfactual_replay import player_positions
from run_fsffl_gm30_counterfactual import CounterfactualEngine
from run_fsffl_historical_usage_policy import HistoricalPoints


def validate_draft_cow() -> None:
    state = {
        "roster_players": {"1": ["101", "102"], "2": ["201", "202"]},
        "roster_taxi": {"1": ["102"], "2": []},
        "roster_reserve": {"1": [], "2": ["202"]},
        "pick_owners": {"pick:2026:R1:orig2": "1", "pick:2027:R2:orig1": "2"},
        "faab": {"1": 80.0, "2": 55.0},
        season_v3.LEDGER_KEY: {"2025": {"records": {"1": {"wins": 9}}}},
        season_v3.DRAFT_KEY: {
            "selected_player_ids": ["900"],
            "picks": [{"draft_season": "2025", "player_id": "900"}],
        },
    }
    player = {
        "player_id": "201",
        "player_name": "Reference Rookie",
        "position": "WR",
        "pick_no": 1,
    }
    kwargs = dict(
        draft_season="2026",
        round_no=1,
        slot=2,
        original_roster_id="2",
        controller_roster_id="1",
        controller_user_id="user-1",
        player=player,
    )
    reference = perf._ORIGINAL_DRAFT_PICK(copy.deepcopy(state), **kwargs)
    optimized = perf.apply_draft_pick_cow(state, **kwargs)
    if optimized != reference:
        raise AssertionError("copy-on-write draft transition changed exact state output")
    if optimized[season_v3.LEDGER_KEY] is not state[season_v3.LEDGER_KEY]:
        raise AssertionError("copy-on-write draft transition failed to share immutable ledger")


def validate_state_key_equivalence() -> None:
    base = {
        "roster_players": {"1": ["2", "1"], "2": ["3"]},
        "roster_taxi": {"1": ["2"]},
        "roster_reserve": {"2": ["3"]},
        "pick_owners": {"pick:b": "2", "pick:a": "1"},
        "faab": {"2": 20, "1": 10.0},
        season_v3.DRAFT_KEY: {"selected_player_ids": ["x"], "picks": [{"player_id": "x"}]},
        season_v3.LEDGER_KEY: {"2025": {"season_max_pf": {"1": 123.4}}},
    }
    equivalent = copy.deepcopy(base)
    equivalent["roster_players"]["1"] = ["1", "2"]
    different = copy.deepcopy(base)
    different["roster_taxi"]["1"] = []

    ref_same = season_v3.season_state_key(base) == season_v3.season_state_key(equivalent)
    opt_same = perf._state_key_with_memo(base) == perf._state_key_with_memo(equivalent)
    ref_diff = season_v3.season_state_key(base) == season_v3.season_state_key(different)
    opt_diff = perf._state_key_with_memo(base) == perf._state_key_with_memo(different)
    if (ref_same, ref_diff) != (opt_same, opt_diff):
        raise AssertionError("optimized structural key changed state equivalence classes")


def validate_maxpf() -> None:
    positions = {
        "1": "QB", "2": "QB", "3": "RB", "4": "RB", "5": "RB",
        "6": "WR", "7": "WR", "8": "WR", "9": "WR", "10": "TE",
        "11": "TE", "12": "WR",
    }
    slots = ["QB", "RB", "RB", "WR", "WR", "WR", "TE", "FLEX", "SUPER_FLEX"]
    cases = [
        ({str(i) for i in range(1, 13)}, {str(i): float((i * 7) % 19) for i in range(1, 13)}),
        ({str(i) for i in range(1, 13)}, {str(i): 0.0 for i in range(1, 13)}),
        ({str(i) for i in range(1, 13)}, {**{str(i): float(i) for i in range(1, 10)}, "10": -2.0, "11": 0.0}),
    ]
    for roster, realized in cases:
        reference = perf._ORIGINAL_BEST_LINEUP(roster, slots, positions, realized)
        optimized = perf.best_lineup_points_cached(roster, slots, positions, realized)
        if optimized != reference:
            raise AssertionError(
                f"optimized MaxPF changed exact result: reference={reference} optimized={optimized}"
            )


def _reference_roster_state():
    rosters = load(season_v3.DATA / "rosters.json") or []
    return {
        "roster_players": {
            str(row.get("roster_id")): [str(x) for x in (row.get("players") or [])]
            for row in rosters
        },
        "roster_taxi": {
            str(row.get("roster_id")): [str(x) for x in (row.get("taxi") or [])]
            for row in rosters
        },
        "roster_reserve": {
            str(row.get("roster_id")): [str(x) for x in (row.get("reserve") or [])]
            for row in rosters
        },
        season_v3.LEDGER_KEY: {
            "2024": {
                "standings": [{"roster_id": "1", "wins": 8}],
                "season_max_pf": {"1": 2000.0},
            }
        },
    }


def validate_weekly_ledger_cow() -> None:
    season = "2025"
    matchups = load(season_v3.DATA / "stats" / "fsffl" / season / "league_matchups_raw.json") or {}
    weekly_points = HistoricalPoints().season(season)
    positions = player_positions()
    ref_groups = [season_v3.SeasonParticleGroup(1, copy.deepcopy(_reference_roster_state()), [[]])]
    opt_groups = [season_v3.SeasonParticleGroup(1, copy.deepcopy(_reference_roster_state()), [[]])]

    for week in (2, 3, 4):
        kwargs = dict(
            season=season,
            week=week,
            matchup_rows=matchups.get(str(week), []),
            slots=[],
            positions=positions,
            weekly_points=weekly_points,
        )
        source_snapshot = copy.deepcopy(opt_groups[0].state.get(season_v3.LEDGER_KEY) or {})
        reference_audit = perf._ORIGINAL_SCORE_REGULAR_WEEK(ref_groups, **kwargs)
        optimized_audit = weekly_cow.score_regular_week_fine_cow(opt_groups, **kwargs)
        if optimized_audit != reference_audit:
            raise AssertionError(f"fine weekly ledger COW changed scoring audit at week {week}")
        if opt_groups[0].state != ref_groups[0].state:
            raise AssertionError(f"fine weekly ledger COW changed exact scored state at week {week}")
        if source_snapshot.get("2024") != opt_groups[0].state[season_v3.LEDGER_KEY].get("2024"):
            raise AssertionError("fine weekly ledger COW mutated a completed prior-season row")

    # A detached source row must remain unchanged after the next optimized week.
    old_ledger = opt_groups[0].state[season_v3.LEDGER_KEY]
    old_season_row = old_ledger[season]
    old_snapshot = copy.deepcopy(old_season_row)
    kwargs = dict(
        season=season,
        week=5,
        matchup_rows=matchups.get("5", []),
        slots=[],
        positions=positions,
        weekly_points=weekly_points,
    )
    perf._ORIGINAL_SCORE_REGULAR_WEEK(ref_groups, **kwargs)
    weekly_cow.score_regular_week_fine_cow(opt_groups, **kwargs)
    if opt_groups[0].state != ref_groups[0].state:
        raise AssertionError("fine weekly ledger COW changed exact state at week 5")
    if old_season_row != old_snapshot:
        raise AssertionError("fine weekly ledger COW mutated the prior source season row through sharing")


def validate_simulator_cache() -> None:
    engine = CounterfactualEngine()
    for roster in engine.rosters:
        for week in range(1, 19):
            reference = perf._ORIGINAL_SIM_OPTIMIZE(
                roster, week, engine.league, engine.players, engine.projections
            )
            optimized = perf.simulator_lineup_cached(
                roster, week, engine.league, engine.players, engine.projections
            )
            if optimized != reference:
                raise AssertionError(
                    f"Simulator lineup cache changed lineup for roster "
                    f"{roster.get('roster_id')} week {week}"
                )


def main() -> None:
    validate_draft_cow()
    validate_state_key_equivalence()
    validate_maxpf()
    validate_weekly_ledger_cow()
    validate_simulator_cache()
    print("PASS: Alternate History performance replacements are exact-equivalent")


if __name__ == "__main__":
    main()
