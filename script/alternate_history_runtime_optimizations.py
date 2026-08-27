#!/usr/bin/env python3
"""Exact runtime optimizations for Alternate History publication.

These optimizations memoize deterministic calculations and avoid repeatedly
copying/hashing the completed-season ledger while it is unchanged between
scoring boundaries. They do not alter random number consumption, branch
probabilities, model policy, historical evidence, or publication semantics.
"""
from __future__ import annotations

import copy
from typing import Any, Dict, Tuple

import alternate_history_engine as ah
import run_fsffl_multiseason_branch_replay as branch_v1
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
    "ledger_digest_hits": 0,
    "ledger_digest_misses": 0,
    "ledger_copy_avoided": 0,
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
    # Keep the object reference alongside its digest so Python id reuse cannot
    # cause a stale hit. Between scoring boundaries the ledger is treated as
    # immutable; scoring already deep-copies it before modification.
    ledger_digest_cache: Dict[int, Tuple[Dict[str, Any], str]] = {}

    def ledger_digest(ledger: Dict[str, Any]) -> str:
        ident = id(ledger)
        cached = ledger_digest_cache.get(ident)
        if cached is not None and cached[0] is ledger:
            _STATS["ledger_digest_hits"] += 1
            return cached[1]
        _STATS["ledger_digest_misses"] += 1
        digest = ah.stable_hash(ledger)
        ledger_digest_cache[ident] = (ledger, digest)
        return digest

    def fast_apply_preserving_ledger(state_payload, event, outcome):
        # branch_v1.apply_outcome serializes only transaction-state fields and
        # never mutates the Alternate History ledger. Sharing the current ledger
        # reference is therefore safe. Every scoring/postseason writer makes a
        # deep copy before changing it, preserving branch isolation exactly.
        ledger = state_payload.get(season_v3.LEDGER_KEY) or {}
        new_state = branch_v1.apply_outcome(state_payload, event, outcome)
        new_state[season_v3.LEDGER_KEY] = ledger
        _STATS["ledger_copy_avoided"] += 1
        return new_state

    def fast_season_state_key(state):
        canonical = {
            "roster_players": season_v3._canonical_roster_subsets(state, "roster_players"),
            "roster_taxi": season_v3._canonical_roster_subsets(state, "roster_taxi"),
            "roster_reserve": season_v3._canonical_roster_subsets(state, "roster_reserve"),
            "pick_owners": dict(sorted((state.get("pick_owners") or {}).items())),
            "faab": {
                str(k): float(v or 0.0)
                for k, v in sorted((state.get("faab") or {}).items())
            },
            "rookie_draft_history": state.get(season_v3.DRAFT_KEY) or {},
            # The digest is an exact content identity for merge purposes while
            # avoiding re-serialization of the growing ledger at every event.
            "season_ledger_digest": ledger_digest(state.get(season_v3.LEDGER_KEY) or {}),
        }
        return ah.stable_hash(canonical)

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

    season_v3.apply_preserving_ledger = fast_apply_preserving_ledger
    season_v3.season_state_key = fast_season_state_key
    season_v3.choose_branch_lineup = cached_choose_branch_lineup
    season_v3.best_lineup_points = cached_best_lineup_points
    HistoricalPoints.trailing = cached_trailing
