#!/usr/bin/env python3
"""Accuracy-neutral runtime optimizations for Alternate History.

This module changes only redundant data movement/hash/scoring work. It does not
alter particle counts, branch probabilities, decision policies, historical
inputs, lineup logic, draft logic, MaxPF logic, or Simulator behavior.

The optimizations are installed explicitly by production/benchmark wrappers so
we can A/B them against the validated engine before folding them into core.
"""

from __future__ import annotations

import copy
from typing import Any, Dict, Iterable, Tuple

import alternate_history_engine as ah
import run_fsffl_multiseason_branch_replay as branch_v1
import run_fsffl_multiseason_particle_replay_v3 as season_v3
import run_fsffl_season_boundary_particles as boundary_core
import run_fsffl_season_simulator_preproduction as simulator

# Keep referenced immutable source objects alive while their identity is used in
# cache keys. That prevents Python id reuse from ever aliasing two source maps.
_SOURCE_OBJECTS: Dict[int, Any] = {}
_SEASON_OBSERVED: Dict[int, frozenset[str]] = {}
_LINEUP_CACHE: Dict[Tuple[Any, ...], Tuple[Tuple[str, ...], Any]] = {}
_MAXPF_CACHE: Dict[Tuple[Any, ...], Tuple[float, Tuple[str, ...]]] = {}
_SIM_LINEUP_CACHE: Dict[Tuple[Any, ...], Any] = {}
_SIM_BACKUP_CACHE: Dict[Tuple[Any, ...], Any] = {}

_ORIGINAL_CHOOSE = season_v3.choose_branch_lineup
_ORIGINAL_BEST_LINEUP = season_v3.best_lineup_points
_ORIGINAL_SIM_OPTIMIZE = simulator.optimize_fsffl_fast
_ORIGINAL_SIM_BACKUPS = simulator.build_backup_chains


def apply_preserving_ledger_cow(
    state_payload: Dict[str, Any],
    event: Dict[str, Any],
    outcome: Dict[str, Any],
) -> Dict[str, Any]:
    """Carry the immutable ledger by reference across transaction transitions.

    branch_v1.apply_outcome serializes only transaction state and never reads or
    mutates the season ledger. All ledger-writing paths in the season engine
    already copy the ledger before mutation, so deep-copying it for every
    transaction outcome is redundant.
    """
    ledger = state_payload.get(season_v3.LEDGER_KEY)
    new_state = branch_v1.apply_outcome(state_payload, event, outcome)
    new_state[season_v3.LEDGER_KEY] = ledger if ledger is not None else {}
    return new_state


def _state_key_with_memo(
    state: Dict[str, Any],
    ledger_hash_by_identity: Dict[int, str],
) -> str:
    ledger = state.get(season_v3.LEDGER_KEY) or {}
    identity = id(ledger)
    ledger_hash = ledger_hash_by_identity.get(identity)
    if ledger_hash is None:
        ledger_hash = ah.stable_hash(ledger)
        ledger_hash_by_identity[identity] = ledger_hash

    core = {
        "roster_players": {
            str(k): sorted(str(x) for x in (v or []))
            for k, v in sorted((state.get("roster_players") or {}).items())
        },
        "pick_owners": dict(sorted((state.get("pick_owners") or {}).items())),
        "faab": {
            str(k): float(v or 0.0)
            for k, v in sorted((state.get("faab") or {}).items())
        },
    }
    return f"{ah.stable_hash(core)}:{ledger_hash}"


def merge_groups_memoized(
    groups: Iterable[season_v3.SeasonParticleGroup],
) -> Tuple[list[season_v3.SeasonParticleGroup], int]:
    """Merge exact-equivalent states while hashing shared ledgers only once."""
    by_key: Dict[str, season_v3.SeasonParticleGroup] = {}
    ledger_hash_by_identity: Dict[int, str] = {}
    merged_particles = 0

    for group in groups:
        if group.count <= 0:
            continue
        key = _state_key_with_memo(group.state, ledger_hash_by_identity)
        existing = by_key.get(key)
        if existing is None:
            by_key[key] = season_v3.SeasonParticleGroup(
                group.count,
                group.state,
                [list(t) for t in group.traces[: season_v3.MAX_TRACES_PER_GROUP]],
            )
            continue

        merged_particles += group.count
        existing.count += group.count
        for trace in group.traces:
            if len(existing.traces) >= season_v3.MAX_TRACES_PER_GROUP:
                break
            if trace not in existing.traces:
                existing.traces.append(list(trace))

    return list(by_key.values()), merged_particles


def realized_lineup_points_cached(
    lineup,
    *,
    week: int,
    weekly_points: Dict[int, Dict[str, float]],
):
    """Exact realized scoring with a once-per-season observed-player index."""
    source_id = id(weekly_points)
    _SOURCE_OBJECTS[source_id] = weekly_points
    observed = _SEASON_OBSERVED.get(source_id)
    if observed is None:
        observed = frozenset(
            str(pid)
            for week_rows in weekly_points.values()
            for pid in week_rows.keys()
        )
        _SEASON_OBSERVED[source_id] = observed

    missing = []
    total = 0.0
    realized = weekly_points.get(int(week), {})
    for pid in lineup:
        if pid in {"0", "None", ""}:
            continue
        pid = str(pid)
        if pid in realized:
            total += float(realized[pid])
        elif pid not in observed:
            missing.append(pid)
    return round(total, 2), missing


def choose_branch_lineup_cached(
    actual_row,
    roster_players,
    *,
    week,
    slots,
    positions,
    weekly_points,
    previous_alt_starters,
):
    """Memoize the exact no-hindsight lineup decision for identical inputs."""
    wp_id = id(weekly_points)
    pos_id = id(positions)
    _SOURCE_OBJECTS[wp_id] = weekly_points
    _SOURCE_OBJECTS[pos_id] = positions
    key = (
        wp_id,
        pos_id,
        int(week),
        tuple(str(x) for x in slots),
        tuple(sorted(str(x) for x in roster_players)),
        tuple(sorted(str(x) for x in previous_alt_starters)),
        tuple(str(x) for x in (actual_row.get("starters") or [])),
        tuple(sorted(str(x) for x in (actual_row.get("players") or []))),
    )
    cached = _LINEUP_CACHE.get(key)
    if cached is None:
        lineup, changes = _ORIGINAL_CHOOSE(
            actual_row,
            roster_players,
            week=week,
            slots=slots,
            positions=positions,
            weekly_points=weekly_points,
            previous_alt_starters=previous_alt_starters,
        )
        cached = (tuple(lineup), copy.deepcopy(changes))
        _LINEUP_CACHE[key] = cached
    return list(cached[0]), copy.deepcopy(cached[1])


def best_lineup_points_cached(roster_players, slots, positions, realized):
    """Memoize exact MaxPF for identical immutable roster/week inputs."""
    pos_id = id(positions)
    realized_id = id(realized)
    _SOURCE_OBJECTS[pos_id] = positions
    _SOURCE_OBJECTS[realized_id] = realized
    key = (
        pos_id,
        realized_id,
        tuple(str(x) for x in slots),
        tuple(sorted(str(x) for x in roster_players)),
    )
    cached = _MAXPF_CACHE.get(key)
    if cached is None:
        max_pf, lineup = _ORIGINAL_BEST_LINEUP(
            roster_players,
            slots,
            positions,
            realized,
        )
        cached = (float(max_pf), tuple(lineup))
        _MAXPF_CACHE[key] = cached
    return cached[0], list(cached[1])


def _sim_roster_signature(roster: Dict[str, Any]) -> Tuple[Tuple[str, ...], ...]:
    return (
        tuple(sorted(str(x) for x in (roster.get("players") or []))),
        tuple(sorted(str(x) for x in (roster.get("taxi") or []))),
        tuple(sorted(str(x) for x in (roster.get("reserve") or []))),
    )


def simulator_lineup_cached(roster, week, league, players, projections):
    """Reuse exact deterministic Simulator lineup preprocessing across states."""
    league_id = id(league)
    players_id = id(players)
    projections_id = id(projections)
    _SOURCE_OBJECTS[league_id] = league
    _SOURCE_OBJECTS[players_id] = players
    _SOURCE_OBJECTS[projections_id] = projections
    key = (
        league_id,
        players_id,
        projections_id,
        int(week),
        _sim_roster_signature(roster),
    )
    cached = _SIM_LINEUP_CACHE.get(key)
    if cached is None:
        cached = copy.deepcopy(
            _ORIGINAL_SIM_OPTIMIZE(roster, week, league, players, projections)
        )
        _SIM_LINEUP_CACHE[key] = cached
    return copy.deepcopy(cached)


def simulator_backups_cached(roster, week, lineup, players, projections):
    """Reuse exact deterministic Simulator backup chains across states."""
    players_id = id(players)
    projections_id = id(projections)
    _SOURCE_OBJECTS[players_id] = players
    _SOURCE_OBJECTS[projections_id] = projections
    lineup_sig = tuple(
        (str(row.get("slot") or ""), str(row.get("player_id") or ""))
        for row in lineup
    )
    key = (
        players_id,
        projections_id,
        int(week),
        _sim_roster_signature(roster),
        lineup_sig,
    )
    cached = _SIM_BACKUP_CACHE.get(key)
    if cached is None:
        cached = copy.deepcopy(
            _ORIGINAL_SIM_BACKUPS(roster, week, lineup, players, projections)
        )
        _SIM_BACKUP_CACHE[key] = cached
    return copy.deepcopy(cached)


def install() -> None:
    """Install the accuracy-neutral runtime replacements for this process."""
    season_v3.apply_preserving_ledger = apply_preserving_ledger_cow
    season_v3.merge_groups = merge_groups_memoized
    season_v3.realized_lineup_points = realized_lineup_points_cached
    season_v3.choose_branch_lineup = choose_branch_lineup_cached
    season_v3.best_lineup_points = best_lineup_points_cached
    # Postseason scoring imported these functions directly, so patch its module
    # globals as well to reuse the same exact caches.
    boundary_core.realized_lineup_points = realized_lineup_points_cached
    boundary_core.choose_branch_lineup = choose_branch_lineup_cached
    # Simulator 1.0 rebuilds deterministic lineup/backup structures on every
    # alternate-state call. Reuse those exact structures for identical rosters.
    simulator.optimize_fsffl_fast = simulator_lineup_cached
    simulator.build_backup_chains = simulator_backups_cached
