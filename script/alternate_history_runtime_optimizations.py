#!/usr/bin/env python3
"""Exact runtime optimizations for Alternate History publication.

These optimizations remove redundant computation only. They do not alter random
number consumption, branch probabilities, model policy, historical evidence,
Simulator draws, lineup eligibility, lineup tie-breaking, or publication
semantics.

The completed-season path reuses immutable ledger objects/digests between
scoring boundaries. The Simulator path reuses deterministic candidate pools and
runs an algebraically identical FSFFL lineup search that pre-sorts each
positional pool once rather than rebuilding/sorting it inside every FLEX/SF
combination.
"""
from __future__ import annotations

import copy
from collections import Counter
from typing import Any, Dict, List, Tuple

import alternate_history_engine as ah
import run_fsffl_multiseason_branch_replay as branch_v1
import run_fsffl_multiseason_particle_replay_v3 as season_v3
import run_fsffl_season_simulator_preproduction as simulator
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
    "sim_candidate_hits": 0,
    "sim_candidate_misses": 0,
    "sim_lineup_hits": 0,
    "sim_lineup_misses": 0,
    "sim_backup_hits": 0,
    "sim_backup_misses": 0,
    "sim_exact_fast_lineups": 0,
}


def stats() -> Dict[str, int]:
    return dict(_STATS)


def _sim_roster_key(roster: Dict[str, Any]) -> Tuple[Any, ...]:
    """Canonical identity for fields that affect Simulator lineup eligibility."""
    players = tuple(sorted(str(x) for x in (roster.get("players") or [])))
    taxi = tuple(sorted(str(x) for x in (roster.get("taxi") or [])))
    reserve = tuple(sorted(str(x) for x in (roster.get("reserve") or [])))
    return players, taxi, reserve


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    orig_choose = season_v3.choose_branch_lineup
    orig_maxpf = season_v3.best_lineup_points
    orig_trailing = HistoricalPoints.trailing
    orig_candidate_rows = simulator.candidate_rows
    orig_sim_lineup = simulator.optimize_fsffl_fast
    orig_sim_backups = simulator.build_backup_chains

    lineup_cache: Dict[Tuple[Any, ...], Any] = {}
    maxpf_cache: Dict[Tuple[Any, ...], Any] = {}
    sim_candidate_cache: Dict[Tuple[Any, ...], Any] = {}
    sim_lineup_cache: Dict[Tuple[Any, ...], Any] = {}
    sim_backup_cache: Dict[Tuple[Any, ...], Any] = {}
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

    def cached_candidate_rows(roster, week, players, projections):
        key = (
            _sim_roster_key(roster),
            int(week),
            id(players),
            id(projections),
        )
        cached = sim_candidate_cache.get(key)
        if cached is not None:
            _STATS["sim_candidate_hits"] += 1
            return copy.deepcopy(cached)
        _STATS["sim_candidate_misses"] += 1
        value = orig_candidate_rows(roster, week, players, projections)
        sim_candidate_cache[key] = copy.deepcopy(value)
        return value

    def exact_fast_sim_lineup(roster, week, league, players, projections):
        """Same FSFFL optimizer result with positional pools sorted only once.

        The canonical optimizer enumerates the same SUPER_FLEX and FLEX options,
        then repeatedly filters and stable-sorts positional pools for every
        pair. Here each positional pool is stable-sorted once. Selecting the
        first non-excluded rows from that sorted pool is exactly equivalent to
        filtering then stable-sorting on every pair, including value ties.
        """
        slots = simulator.core.lineup_slots(league)
        if not simulator.standard_fsffl_slot_counts(slots):
            return orig_sim_lineup(roster, week, league, players, projections)

        candidates = cached_candidate_rows(roster, week, players, projections)
        if not candidates:
            return orig_sim_lineup(roster, week, league, players, projections)

        sf_pool = [c for c in candidates if c["position"] in {"QB", "RB", "WR", "TE"}]
        flex_pool = [c for c in candidates if c["position"] in {"RB", "WR", "TE"}]
        by_pos: Dict[str, List[Dict[str, Any]]] = {}
        for pos in ("QB", "RB", "WR", "TE"):
            pool = [c for c in candidates if c["position"] == pos]
            pool.sort(key=lambda c: c["value"], reverse=True)
            by_pos[pos] = pool

        def fixed(pos: str, count: int, excluded: set):
            chosen = []
            for row in by_pos[pos]:
                if row["player_id"] in excluded:
                    continue
                chosen.append(row)
                if len(chosen) == count:
                    return chosen
            return None

        best_total = -1e18
        best = None
        sf_options = [None] + sf_pool
        flex_options = [None] + flex_pool

        for sf in sf_options:
            sf_id = sf["player_id"] if sf else None
            for fl in flex_options:
                fl_id = fl["player_id"] if fl else None
                if sf_id is not None and sf_id == fl_id:
                    continue

                used = {x for x in (sf_id, fl_id) if x is not None}
                qb = fixed("QB", 1, used)
                if qb is None:
                    continue
                used_qb = used | {x["player_id"] for x in qb}
                rb = fixed("RB", 2, used_qb)
                if rb is None:
                    continue
                used_rb = used_qb | {x["player_id"] for x in rb}
                wr = fixed("WR", 3, used_rb)
                if wr is None:
                    continue
                used_wr = used_rb | {x["player_id"] for x in wr}
                te = fixed("TE", 1, used_wr)
                if te is None:
                    continue

                selected = qb + rb + wr + te
                if fl:
                    selected.append(fl)
                if sf:
                    selected.append(sf)
                total = sum(x["value"] for x in selected)
                if total > best_total:
                    best_total = total
                    best = {
                        "QB": qb,
                        "RB": rb,
                        "WR": wr,
                        "TE": te,
                        "FLEX": [fl] if fl else [],
                        "SUPER_FLEX": [sf] if sf else [],
                    }

        if best is None:
            return orig_sim_lineup(roster, week, league, players, projections)

        buckets = {k: list(v) for k, v in best.items()}
        lineup = []
        for slot in slots:
            row = buckets.get(slot, []).pop(0) if buckets.get(slot) else None
            if row is None:
                lineup.append({
                    "slot": slot,
                    "player_id": None,
                    "name": "EMPTY",
                    "position": None,
                    "mean": 0.0,
                    "median": 0.0,
                    "sd": 0.1,
                    "active_probability": 0.0,
                    "nfl_team": None,
                })
            else:
                lineup.append({"slot": slot, **row})
        _STATS["sim_exact_fast_lineups"] += 1
        return lineup

    def cached_sim_lineup(roster, week, league, players, projections):
        key = (
            _sim_roster_key(roster),
            int(week),
            tuple(simulator.core.lineup_slots(league)),
            id(players),
            id(projections),
        )
        cached = sim_lineup_cache.get(key)
        if cached is not None:
            _STATS["sim_lineup_hits"] += 1
            return copy.deepcopy(cached)
        _STATS["sim_lineup_misses"] += 1
        value = exact_fast_sim_lineup(roster, week, league, players, projections)
        sim_lineup_cache[key] = copy.deepcopy(value)
        return value

    def cached_sim_backups(roster, week, lineup, players, projections):
        lineup_key = tuple(
            (
                str(row.get("slot") or ""),
                str(row.get("player_id") or ""),
                str(row.get("position") or ""),
                float(row.get("mean") or 0.0),
                float(row.get("sd") or 0.0),
                float(row.get("active_probability") or 0.0),
            )
            for row in lineup
        )
        key = (
            _sim_roster_key(roster),
            int(week),
            lineup_key,
            id(players),
            id(projections),
        )
        cached = sim_backup_cache.get(key)
        if cached is not None:
            _STATS["sim_backup_hits"] += 1
            return copy.deepcopy(cached)
        _STATS["sim_backup_misses"] += 1
        # orig_sim_backups resolves simulator.candidate_rows dynamically, which
        # is patched below, so the backup pass reuses the exact candidate pool
        # constructed by the optimizer rather than rebuilding it.
        value = orig_sim_backups(roster, week, lineup, players, projections)
        sim_backup_cache[key] = copy.deepcopy(value)
        return value

    season_v3.apply_preserving_ledger = fast_apply_preserving_ledger
    season_v3.season_state_key = fast_season_state_key
    season_v3.choose_branch_lineup = cached_choose_branch_lineup
    season_v3.best_lineup_points = cached_best_lineup_points
    HistoricalPoints.trailing = cached_trailing
    simulator.candidate_rows = cached_candidate_rows
    simulator.optimize_fsffl_fast = cached_sim_lineup
    simulator.build_backup_chains = cached_sim_backups
