#!/usr/bin/env python3
"""Exact memoized replacement for Simulator 1.0 generic lineup DFS.

This module changes only redundant recursion. Candidate construction, projected
objective, eligibility, slot ordering, empty-slot behavior, forward floating
arithmetic, strict tie handling, and final lineup shape intentionally mirror the
validated reference optimizer.
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

    # Preserve the reference optimizer's stable per-state option ordering.
    option_indexes: List[Tuple[int, ...]] = []
    values: List[float] = []
    for c in candidates:
        values.append(c["mean"] * c["active_probability"])
    for _, slot in ordered_slots:
        eligible_indexes = [
            idx for idx, c in enumerate(candidates)
            if core.eligible(c["position"], slot)
        ]
        eligible_indexes.sort(key=lambda idx: values[idx], reverse=True)
        option_indexes.append(tuple(eligible_indexes))

    # Include the forward accumulated float in the cache key. The reference DFS
    # adds projected values from left to right and uses strict > at the leaf;
    # carrying `total` preserves both floating-point accumulation and the exact
    # first-encountered tie path while still collapsing genuinely identical
    # subproblems reached through redundant recursion.
    @lru_cache(maxsize=None)
    def solve(i: int, used_ids: frozenset[str], total: float):
        if i == len(ordered_slots):
            return total, ()

        original_idx, slot = ordered_slots[i]
        feasible = [
            idx for idx in option_indexes[i]
            if candidates[idx]["player_id"] not in used_ids
        ]
        if not feasible:
            final_total, tail_assign = solve(i + 1, used_ids, total)
            return final_total, ((original_idx, slot, -1),) + tail_assign

        best_final = -1e18
        best_assign: Tuple[Tuple[int, str, int], ...] = ()
        for idx in feasible:
            pid = candidates[idx]["player_id"]
            final_total, tail_assign = solve(
                i + 1,
                used_ids | {pid},
                total + values[idx],
            )
            if final_total > best_final:
                best_final = final_total
                best_assign = ((original_idx, slot, idx),) + tail_assign
        return best_final, best_assign

    _, assignment = solve(0, frozenset(), 0.0)
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
