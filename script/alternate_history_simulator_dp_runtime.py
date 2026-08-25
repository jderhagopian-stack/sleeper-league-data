#!/usr/bin/env python3
"""Exact memoized replacement for Simulator 1.0 generic lineup DFS.

This module changes only redundant recursion. Candidate construction, projected
objective, eligibility, slot ordering, empty-slot behavior, strict tie handling,
and final lineup shape intentionally mirror the validated reference optimizer.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Dict, List, Tuple

import build_fsffl_season_simulator as core

_ORIGINAL_OPTIMIZE = core.optimize_weekly_lineup


def optimize_weekly_lineup_memoized(roster, week, league, players, projections):
    candidates: List[Dict[str, Any]] = []
    taxi = set(roster.get("taxi") or [])
    for pid in roster.get("players") or []:
        if pid in taxi:
            continue
        meta = core.player_meta(players, projections, pid)
        pos = meta.get("position")
        pr = core.projection_for(projections, pid, week)
        if not pos or pr is None or pr["active_probability"] <= 0:
            continue
        candidates.append({**meta, **pr})

    slots = core.lineup_slots(league)
    slot_priority = {"QB": 0, "TE": 1, "RB": 2, "WR": 2, "SUPER_FLEX": 3, "FLEX": 4}
    ordered_slots = sorted(enumerate(slots), key=lambda x: slot_priority.get(x[1], 5))

    # Preserve the reference optimizer's stable per-state ordering exactly.
    option_indexes: List[Tuple[int, ...]] = []
    values: List[float] = []
    for c in candidates:
        values.append(float(c["mean"]) * float(c["active_probability"]))
    for _, slot in ordered_slots:
        eligible_indexes = [
            idx for idx, c in enumerate(candidates)
            if core.eligible(c["position"], slot)
        ]
        eligible_indexes.sort(key=lambda idx: values[idx], reverse=True)
        option_indexes.append(tuple(eligible_indexes))

    # Return (best remaining value, assignments in ordered-slot traversal order).
    # Strict > below matches the reference DFS: equal-valued later branches never
    # replace the first branch encountered.
    @lru_cache(maxsize=None)
    def solve(i: int, used_ids: frozenset[str]):
        if i == len(ordered_slots):
            return 0.0, ()

        original_idx, slot = ordered_slots[i]
        feasible = [
            idx for idx in option_indexes[i]
            if str(candidates[idx]["player_id"]) not in used_ids
        ]
        if not feasible:
            tail_value, tail_assign = solve(i + 1, used_ids)
            return tail_value, ((original_idx, slot, -1),) + tail_assign

        best_value = -1e18
        best_assign: Tuple[Tuple[int, str, int], ...] = ()
        for idx in feasible:
            pid = str(candidates[idx]["player_id"])
            tail_value, tail_assign = solve(i + 1, used_ids | {pid})
            total = values[idx] + tail_value
            if total > best_value:
                best_value = total
                best_assign = ((original_idx, slot, idx),) + tail_assign
        return best_value, best_assign

    _, assignment = solve(0, frozenset())
    ordered_assignment = sorted(assignment, key=lambda x: x[0])
    lineup = []
    for _, slot, idx in ordered_assignment:
        if idx < 0:
            lineup.append({
                "slot": slot,
                "player_id": None,
                "name": "EMPTY",
                "position": None,
                "mean": 0.0,
                "sd": 0.1,
                "active_probability": 0.0,
            })
        else:
            lineup.append({"slot": slot, **candidates[idx]})
    return lineup


def install() -> None:
    core.optimize_weekly_lineup = optimize_weekly_lineup_memoized
