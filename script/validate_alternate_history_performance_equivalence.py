#!/usr/bin/env python3
"""Exact equivalence checks for Alternate History performance replacements.

These checks compare optimized deterministic primitives directly against their
untouched reference implementations. Performance changes are allowed only when
outputs and exact state-equivalence classes remain unchanged.
"""

from __future__ import annotations

import copy

import alternate_history_performance_runtime as perf
import run_fsffl_multiseason_particle_replay_v3 as season_v3
from run_fsffl_gm30_counterfactual import CounterfactualEngine


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


def validate_simulator_lineups() -> None:
    engine = CounterfactualEngine()
    weeks = sorted(int(w) for w in engine.projections.keys() if str(w).isdigit())[:3]
    if not weeks:
        weeks = [1, 2, 3]
    for roster in engine.rosters[:4]:
        for week in weeks:
            reference = perf._ORIGINAL_SIM_OPTIMIZE(
                roster, week, engine.league, engine.players, engine.projections
            )
            optimized = perf.simulator_optimize_exact_presorted(
                roster, week, engine.league, engine.players, engine.projections
            )
            if optimized != reference:
                raise AssertionError(
                    f"pre-sorted Simulator optimizer changed lineup for roster "
                    f"{roster.get('roster_id')} week {week}"
                )


def main() -> None:
    validate_draft_cow()
    validate_state_key_equivalence()
    validate_maxpf()
    validate_simulator_lineups()
    print("PASS: Alternate History performance replacements are exact-equivalent")


if __name__ == "__main__":
    main()
