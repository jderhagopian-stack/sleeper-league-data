#!/usr/bin/env python3
"""Historical Max PF utilities for Fantasy Alternate History 0.7b.

Computes best-ball weekly lineup points from immutable realized fantasy points
and the players actually owned by one alternate-history branch in that scoring
week. NFL outcomes never change; only fantasy roster eligibility changes.

Exact results are memoized across equivalent roster/week inputs. Particle
engines often contain many copies of the same roster state, so recomputing the
same dynamic program per particle wastes substantial runtime without adding any
fidelity.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Dict, Iterable, List, Sequence, Tuple

from run_fsffl_counterfactual_replay import eligible


EMPTY = "0"
_BEST_LINEUP_CACHE: Dict[Tuple[object, ...], Tuple[float, Tuple[str, ...]]] = {}
_CACHE_MAX = 50000


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
    _BEST_LINEUP_CACHE.clear()


def best_lineup_points(
    roster_players: Iterable[str],
    slots: Sequence[str],
    positions: Dict[str, str],
    realized_points: Dict[str, float],
) -> Tuple[float, List[str]]:
    """Return exact maximum legal lineup score and one maximizing lineup."""
    players = tuple(sorted(
        str(pid) for pid in roster_players
        if str(pid) not in {EMPTY, "None", ""}
        and str(pid) in realized_points
    ))
    slot_tuple = tuple(str(x) for x in slots)
    key = _cache_key(players, slot_tuple, positions, realized_points)
    cached = _BEST_LINEUP_CACHE.get(key)
    if cached is not None:
        score, lineup = cached
        return score, list(lineup)

    @lru_cache(maxsize=None)
    def solve(slot_idx: int, remaining: Tuple[str, ...]) -> Tuple[float, Tuple[str, ...]]:
        if slot_idx >= len(slot_tuple):
            return 0.0, ()
        slot = slot_tuple[slot_idx]
        best_score, best_lineup = solve(slot_idx + 1, remaining)
        best_lineup = (EMPTY,) + best_lineup
        for i, pid in enumerate(remaining):
            if not eligible(positions.get(pid, ""), slot):
                continue
            nxt = remaining[:i] + remaining[i + 1 :]
            rest_score, rest_lineup = solve(slot_idx + 1, nxt)
            score = float(realized_points.get(pid) or 0.0) + rest_score
            candidate = (pid,) + rest_lineup
            if score > best_score + 1e-9 or (
                abs(score - best_score) <= 1e-9 and candidate < best_lineup
            ):
                best_score, best_lineup = score, candidate
        return best_score, best_lineup

    score, lineup = solve(0, players)
    result = (round(score, 2), tuple(lineup))
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
