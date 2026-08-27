#!/usr/bin/env python3
"""Exact runtime optimizations for Alternate History publication.

These caches memoize deterministic calculations only. They do not alter random
number consumption, branch probabilities, model policy, historical evidence,
or publication semantics. Cached values are deep-copied on return so callers
cannot mutate shared cache state.
"""
from __future__ import annotations

import copy
from typing import Any, Dict, Tuple

import run_fsffl_multiseason_particle_replay_v3 as season_v3
from run_fsffl_historical_usage_policy import HistoricalPoints

_INSTALLED = False
_STATS: Dict[str, int] = {
    "lineup_hits": 0,
    "lineup_misses": 0,
    "maxpf_hits": 0,
    "maxpf_misses": 0,
    "trailing_hits": 0,
    "trailing_misses": 0,
}


def stats() -> Dict[str, int]:
    return dict(_STATS)


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    orig_choose = season_v3.choose_branch_lineup
    orig_maxpf = season_v3.best_lineup_points
    orig_trailing = HistoricalPoints.trailing

    lineup_cache: Dict[Tuple[Any, ...], Any] = {}
    maxpf_cache: Dict[Tuple[Any, ...], Any] = {}

    def cached_choose_branch_lineup(
        actual_row,
        active_roster_players,
        *,
        week,
        slots,
        positions,
        weekly_points,
        previous_alt_starters,
    ):
        key = (
            id(weekly_points),
            id(positions),
            int(week),
            tuple(str(x) for x in slots),
            tuple(sorted(str(x) for x in active_roster_players)),
            tuple(sorted(str(x) for x in previous_alt_starters)),
            str(actual_row.get("roster_id") or ""),
            tuple(str(x) for x in (actual_row.get("starters") or [])),
            tuple(str(x) for x in (actual_row.get("players") or [])),
        )
        cached = lineup_cache.get(key)
        if cached is not None:
            _STATS["lineup_hits"] += 1
            return copy.deepcopy(cached)
        _STATS["lineup_misses"] += 1
        value = orig_choose(
            actual_row,
            active_roster_players,
            week=week,
            slots=slots,
            positions=positions,
            weekly_points=weekly_points,
            previous_alt_starters=previous_alt_starters,
        )
        lineup_cache[key] = copy.deepcopy(value)
        return value

    def cached_best_lineup_points(players, slots, positions, realized):
        key = (
            id(realized),
            id(positions),
            tuple(str(x) for x in slots),
            tuple(sorted(str(x) for x in players)),
        )
        cached = maxpf_cache.get(key)
        if cached is not None:
            _STATS["maxpf_hits"] += 1
            return copy.deepcopy(cached)
        _STATS["maxpf_misses"] += 1
        value = orig_maxpf(players, slots, positions, realized)
        maxpf_cache[key] = copy.deepcopy(value)
        return value

    def cached_trailing(self, season, week, pid):
        cache = getattr(self, "_alternate_history_trailing_cache", None)
        if cache is None:
            cache = {}
            setattr(self, "_alternate_history_trailing_cache", cache)
        key = (str(season), None if week is None else int(week), str(pid))
        cached = cache.get(key)
        if cached is not None:
            _STATS["trailing_hits"] += 1
            return copy.deepcopy(cached)
        _STATS["trailing_misses"] += 1
        value = orig_trailing(self, season, week, pid)
        cache[key] = copy.deepcopy(value)
        return value

    season_v3.choose_branch_lineup = cached_choose_branch_lineup
    season_v3.best_lineup_points = cached_best_lineup_points
    HistoricalPoints.trailing = cached_trailing
