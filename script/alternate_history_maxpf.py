#!/usr/bin/env python3
"""Historical Max PF utilities for Fantasy Alternate History 0.7b.

Computes best-ball weekly lineup points from immutable realized fantasy points
and the players actually owned by one alternate-history branch in that scoring
week. NFL outcomes never change; only fantasy roster eligibility changes.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Dict, Iterable, List, Sequence, Tuple

from run_fsffl_counterfactual_replay import eligible


EMPTY = "0"


def best_lineup_points(
    roster_players: Iterable[str],
    slots: Sequence[str],
    positions: Dict[str, str],
    realized_points: Dict[str, float],
) -> Tuple[float, List[str]]:
    """Return exact maximum legal lineup score and one maximizing lineup.

    This is intentionally a small dynamic program over starter slots. Bench size
    is small in fantasy football, so exact optimization is cheap and avoids any
    greedy-lineup edge cases around FLEX / SUPERFLEX eligibility.
    """
    players = tuple(sorted(
        str(pid) for pid in roster_players
        if str(pid) not in {EMPTY, "None", ""}
        and str(pid) in realized_points
    ))
    slot_tuple = tuple(str(x) for x in slots)

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
    return round(score, 2), list(lineup)


def season_max_pf(weekly_rows: Iterable[Tuple[int, Iterable[str], Dict[str, float]]], slots: Sequence[str], positions: Dict[str, str]) -> Dict[str, object]:
    total = 0.0
    weeks: List[Dict[str, object]] = []
    for week, roster_players, realized_points in weekly_rows:
        points, lineup = best_lineup_points(roster_players, slots, positions, realized_points)
        total += points
        weeks.append({"week": int(week), "max_pf": points, "lineup": lineup})
    return {"max_pf": round(total, 2), "weeks": weeks}
