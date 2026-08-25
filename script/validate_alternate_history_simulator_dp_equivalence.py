#!/usr/bin/env python3
"""A/B equivalence checks for the memoized generic Simulator lineup optimizer."""

from __future__ import annotations

import copy

import alternate_history_simulator_dp_runtime as dp
from run_fsffl_gm30_counterfactual import CounterfactualEngine


def assert_same(roster, week, engine, label):
    reference = dp._ORIGINAL_OPTIMIZE(
        copy.deepcopy(roster), week, engine.league, engine.players, engine.projections
    )
    optimized = dp.optimize_weekly_lineup_memoized(
        copy.deepcopy(roster), week, engine.league, engine.players, engine.projections
    )
    if optimized != reference:
        raise AssertionError(
            f"memoized generic Simulator optimizer changed exact lineup: {label} week={week}\n"
            f"reference={reference}\noptimized={optimized}"
        )


def main() -> None:
    engine = CounterfactualEngine()

    # Real current rosters across early/mid/late projection weeks.
    for roster in engine.rosters:
        rid = roster.get("roster_id")
        for week in (1, 9, 18):
            assert_same(roster, week, engine, f"real-roster-{rid}")

    # Explicit shortage cases exercise the fallback's EMPTY-slot semantics.
    base = copy.deepcopy(engine.rosters[0])
    original_players = list(base.get("players") or [])

    def position(pid):
        p = (engine.players or {}).get(str(pid)) or {}
        q = ((engine.projections or {}).get("players") or {}).get(str(pid)) or {}
        return q.get("position") or p.get("position")

    variants = {
        "no-qb": [pid for pid in original_players if position(pid) != "QB"],
        "no-te": [pid for pid in original_players if position(pid) != "TE"],
        "rb-wr-only": [pid for pid in original_players if position(pid) in {"RB", "WR"}],
        "shallow-seven": original_players[:7],
    }
    for label, ids in variants.items():
        roster = copy.deepcopy(base)
        roster["players"] = ids
        roster["taxi"] = [pid for pid in (roster.get("taxi") or []) if pid in ids]
        roster["reserve"] = [pid for pid in (roster.get("reserve") or []) if pid in ids]
        for week in (1, 9, 18):
            assert_same(roster, week, engine, label)

    print("PASS: memoized generic Simulator optimizer is exact-equivalent")


if __name__ == "__main__":
    main()
