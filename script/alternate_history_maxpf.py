#!/usr/bin/env python3
"""Historical Max PF utilities for Fantasy Alternate History 0.7b.

Computes best-ball weekly lineup points from immutable realized fantasy points
and the players actually owned by one alternate-history branch in that scoring
week. NFL outcomes never change; only fantasy roster eligibility changes.

Exact results are memoized across equivalent roster/week inputs. The exact
optimizer uses dynamic programming over starter-slot masks rather than subsets
of remaining roster players. With a typical fantasy lineup this bounds the
state space by roughly ``players * 2**starter_slots`` instead of exploring a
large power set of a 15-25 player roster.
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Sequence, Tuple

from run_fsffl_counterfactual_replay import eligible


EMPTY = "0"
_BEST_LINEUP_CACHE: Dict[Tuple[object, ...], Tuple[float, Tuple[str, ...]]] = {}
_CACHE_MAX = 50000
_CACHE_HITS = 0
_CACHE_MISSES = 0


def _cache_key(
    players: Tuple[str, ...],
    slots: Tuple[str, ...],
    positions: Dict[str, str],
    realized_points: Dict[str, float],
) -> Tuple[object, ...]:
    # Only rostered/scored players matter. Including their position + immutable
    # realized score makes the cache safe across weeks and seasons without a
    # caller-supplied week identifier.
    player_inputs = tuple(
        (pid, str(positions.get(pid, "")), float(realized_points.get(pid) or 0.0))
        for pid in players
    )
    return (slots, player_inputs)


def clear_best_lineup_cache() -> None:
    global _CACHE_HITS, _CACHE_MISSES
    _BEST_LINEUP_CACHE.clear()
    _CACHE_HITS = 0
    _CACHE_MISSES = 0


def best_lineup_cache_stats() -> Dict[str, int]:
    return {
        "size": len(_BEST_LINEUP_CACHE),
        "hits": _CACHE_HITS,
        "misses": _CACHE_MISSES,
    }


def _better(
    candidate_score: float,
    candidate_lineup: Tuple[str, ...],
    incumbent: Tuple[float, Tuple[str, ...]] | None,
) -> bool:
    if incumbent is None:
        return True
    incumbent_score, incumbent_lineup = incumbent
    if candidate_score > incumbent_score + 1e-9:
        return True
    if abs(candidate_score - incumbent_score) <= 1e-9 and candidate_lineup < incumbent_lineup:
        return True
    return False


def best_lineup_points(
    roster_players: Iterable[str],
    slots: Sequence[str],
    positions: Dict[str, str],
    realized_points: Dict[str, float],
) -> Tuple[float, List[str]]:
    """Return exact maximum legal lineup score and one maximizing lineup.

    Exact assignment DP over starter-slot masks. Each rostered player is either
    unused or assigned to one eligible unfilled slot. This preserves the same
    legal-lineup semantics as the old remaining-player recursion while making
    runtime depend primarily on the small number of starter slots.
    """
    global _CACHE_HITS, _CACHE_MISSES

    players = tuple(sorted(
        str(pid) for pid in roster_players
        if str(pid) not in {EMPTY, "None", ""}
        and str(pid) in realized_points
    ))
    slot_tuple = tuple(str(x) for x in slots)
    key = _cache_key(players, slot_tuple, positions, realized_points)
    cached = _BEST_LINEUP_CACHE.get(key)
    if cached is not None:
        _CACHE_HITS += 1
        score, lineup = cached
        return score, list(lineup)
    _CACHE_MISSES += 1

    slot_count = len(slot_tuple)
    empty_lineup = tuple(EMPTY for _ in slot_tuple)
    # mask -> (score, lineup tuple indexed by slot). We only retain the best
    # assignment for each filled-slot mask after processing each player.
    dp: Dict[int, Tuple[float, Tuple[str, ...]]] = {0: (0.0, empty_lineup)}

    for pid in players:
        position = positions.get(pid, "")
        points = float(realized_points.get(pid) or 0.0)
        eligible_slots = [
            idx for idx, slot in enumerate(slot_tuple)
            if eligible(position, slot)
        ]
        if not eligible_slots:
            continue

        next_dp = dict(dp)  # player unused
        for mask, (score, lineup) in dp.items():
            for slot_idx in eligible_slots:
                bit = 1 << slot_idx
                if mask & bit:
                    continue
                new_mask = mask | bit
                new_lineup_list = list(lineup)
                new_lineup_list[slot_idx] = pid
                new_lineup = tuple(new_lineup_list)
                candidate_score = score + points
                incumbent = next_dp.get(new_mask)
                if _better(candidate_score, new_lineup, incumbent):
                    next_dp[new_mask] = (candidate_score, new_lineup)
        dp = next_dp

    best_score = -1.0
    best_lineup = empty_lineup
    for score, lineup in dp.values():
        if score > best_score + 1e-9 or (
            abs(score - best_score) <= 1e-9 and lineup < best_lineup
        ):
            best_score, best_lineup = score, lineup

    result = (round(max(best_score, 0.0), 2), best_lineup)
    if len(_BEST_LINEUP_CACHE) >= _CACHE_MAX:
        # Deterministic coarse eviction; cache contents affect speed only.
        _BEST_LINEUP_CACHE.clear()
    _BEST_LINEUP_CACHE[key] = result
    return result[0], list(result[1])


def season_max_pf(
    weekly_rows: Iterable[Tuple[int, Iterable[str], Dict[str, float]]],
    slots: Sequence[str],
    positions: Dict[str, str],
) -> Dict[str, object]:
    total = 0.0
    weeks: List[Dict[str, object]] = []
    for week, roster_players, realized_points in weekly_rows:
        points, lineup = best_lineup_points(roster_players, slots, positions, realized_points)
        total += points
        weeks.append({"week": int(week), "max_pf": points, "lineup": lineup})
    return {"max_pf": round(total, 2), "weeks": weeks}
