#!/usr/bin/env python3
"""Exact memoized replacement for Simulator 1.0's generic lineup DFS fallback.

This module preserves the canonical candidate construction, slot ordering,
empty-slot behavior, candidate ordering, left-to-right floating-point addition,
strict tie rule, and output schema. It only memoizes repeated DFS subproblems so
pathological/short-handed alternate rosters do not repeatedly traverse the same
search tree.

Set AH_VALIDATE_EXACT_FALLBACK=1 to compare every memoized fallback result
against the original canonical DFS and fail immediately on any difference.
"""
from __future__ import annotations

import os
from functools import lru_cache
from typing import Dict, List, Tuple

import build_fsffl_season_simulator as core

_ORIGINAL = core.optimize_weekly_lineup
_INSTALLED = False
_STATS = {"calls": 0, "memo_states": 0, "validated_calls": 0}


def stats() -> Dict[str, int]:
    return dict(_STATS)


def _candidate_rows(roster, week, players, projections):
    candidates = []
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
    return candidates


def _memoized_result(roster, week, league, players, projections):
    candidates = _candidate_rows(roster, week, players, projections)
    slots = core.lineup_slots(league)
    slot_priority = {"QB": 0, "TE": 1, "RB": 2, "WR": 2, "SUPER_FLEX": 3, "FLEX": 4}
    ordered_slots = sorted(enumerate(slots), key=lambda x: slot_priority.get(x[1], 5))

    # Canonical DFS filters candidates in original roster order, then performs a
    # stable descending sort by projected contribution for every slot. Filtering
    # a precomputed stable order is identical, so cache the slot orders once.
    option_indices: List[Tuple[int, ...]] = []
    for _, slot in ordered_slots:
        idxs = [i for i, c in enumerate(candidates) if core.eligible(c["position"], slot)]
        idxs.sort(
            key=lambda i: candidates[i]["mean"] * candidates[i]["active_probability"],
            reverse=True,
        )
        option_indices.append(tuple(idxs))

    values = [c["mean"] * c["active_probability"] for c in candidates]

    @lru_cache(maxsize=None)
    def solve(i: int, used_mask: int, running_total: float):
        """Return canonical best final total and suffix from this exact DFS state.

        running_total is intentionally part of the memo key. The canonical
        implementation adds each selected player's float contribution from left
        to right. Reassociating those additions can change the last bit and thus
        alter which equal-value path wins the strict `>` tie rule. Carrying the
        exact running float reproduces that arithmetic order byte-for-byte.
        """
        _STATS["memo_states"] += 1
        if i == len(ordered_slots):
            return running_total, ()

        original_idx, slot = ordered_slots[i]
        available = [idx for idx in option_indices[i] if not (used_mask & (1 << idx))]
        if not available:
            final_total, suffix = solve(i + 1, used_mask, running_total)
            return final_total, ((original_idx, slot, -1),) + suffix

        best_final = -1e18
        best_assign = None
        # Same option order, same forward float addition, same strict > rule.
        # Therefore an exact tie keeps the first path encountered by canonical DFS.
        for idx in available:
            next_total = running_total + values[idx]
            final_total, suffix = solve(i + 1, used_mask | (1 << idx), next_total)
            if final_total > best_final:
                best_final = final_total
                best_assign = ((original_idx, slot, idx),) + suffix
        return best_final, best_assign or ()

    _, assignment = solve(0, 0, 0.0)
    best_assign = list(assignment)
    best_assign.sort(key=lambda x: x[0])

    lineup = []
    for _, slot, idx in best_assign:
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


def optimize_weekly_lineup_memoized(roster, week, league, players, projections):
    """Return the canonical DFS result while memoizing exact repeated states."""
    _STATS["calls"] += 1
    result = _memoized_result(roster, week, league, players, projections)
    if os.getenv("AH_VALIDATE_EXACT_FALLBACK") == "1":
        reference = _ORIGINAL(roster, week, league, players, projections)
        _STATS["validated_calls"] += 1
        if result != reference:
            raise RuntimeError(
                "Memoized Simulator fallback changed canonical lineup result "
                f"for roster_id={roster.get('roster_id')} week={week}: "
                f"memoized={result!r} reference={reference!r}"
            )
    return result


def original_optimize_weekly_lineup(roster, week, league, players, projections):
    return _ORIGINAL(roster, week, league, players, projections)


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True
    core.optimize_weekly_lineup = optimize_weekly_lineup_memoized
