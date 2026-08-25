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
import run_fsffl_alternate_rookie_draft_particles as draft_runner

_SOURCE_OBJECTS: Dict[int, Any] = {}
_SEASON_OBSERVED: Dict[int, frozenset[str]] = {}
_LINEUP_CACHE: Dict[Tuple[Any, ...], Tuple[Tuple[str, ...], Any]] = {}
_MAXPF_CACHE: Dict[Tuple[Any, ...], Tuple[float, Tuple[str, ...]]] = {}
_SIM_LINEUP_CACHE: Dict[Tuple[Any, ...], Any] = {}
_SIM_BACKUP_CACHE: Dict[Tuple[Any, ...], Any] = {}
_LEDGER_HASH_CACHE: Dict[int, str] = {}

_ORIGINAL_CHOOSE = season_v3.choose_branch_lineup
_ORIGINAL_BEST_LINEUP = season_v3.best_lineup_points
_ORIGINAL_SIM_OPTIMIZE = simulator.optimize_fsffl_fast
_ORIGINAL_SIM_BACKUPS = simulator.build_backup_chains
_ORIGINAL_DRAFT_PICK = draft_runner.apply_draft_pick


def apply_preserving_ledger_cow(
    state_payload: Dict[str, Any],
    event: Dict[str, Any],
    outcome: Dict[str, Any],
) -> Dict[str, Any]:
    """Carry the immutable ledger by reference across transaction transitions."""
    ledger = state_payload.get(season_v3.LEDGER_KEY)
    new_state = branch_v1.apply_outcome(state_payload, event, outcome)
    new_state[season_v3.LEDGER_KEY] = ledger if ledger is not None else {}
    return new_state


def apply_draft_pick_cow(
    state: Dict[str, Any],
    *,
    draft_season: str,
    round_no: int,
    slot: int,
    original_roster_id: str,
    controller_roster_id: str,
    controller_user_id: str,
    player: Dict[str, Any],
) -> Dict[str, Any]:
    """Exact draft transition that copies only structures changed by the pick."""
    pid = str(player.get("player_id") or "")
    if not pid:
        raise ah.AlternateHistoryError("0.8a attempted to draft empty player id")

    # The reference implementation deep-copies the entire particle state. A
    # draft pick mutates only roster ownership, one pick-owner key, and draft
    # history, so all other state components can be safely shared by reference.
    out = dict(state)

    source_rosters = state.get("roster_players") or {}
    rosters = dict(source_rosters)
    for rid, players in source_rosters.items():
        rid = str(rid)
        if isinstance(players, set):
            if pid in players or rid == str(controller_roster_id):
                copied = set(players)
                copied.discard(pid)
                if rid == str(controller_roster_id):
                    copied.add(pid)
                rosters[rid] = copied
        else:
            values = list(players or [])
            if pid in values or rid == str(controller_roster_id):
                values = [str(x) for x in values if str(x) != pid]
                if rid == str(controller_roster_id) and pid not in values:
                    values.append(pid)
                    values.sort()
                rosters[rid] = values
    if str(controller_roster_id) not in rosters:
        rosters[str(controller_roster_id)] = [pid]
    out["roster_players"] = rosters

    pick_owners = dict(state.get("pick_owners") or {})
    pick_key = f"pick:{draft_season}:R{int(round_no)}:orig{original_roster_id}"
    pick_owners.pop(pick_key, None)
    out["pick_owners"] = pick_owners

    source_draft = state.get(draft_runner.DRAFT_KEY) or {}
    draft_node = dict(source_draft)
    selected = [str(x) for x in (source_draft.get("selected_player_ids") or [])]
    if pid not in selected:
        selected.append(pid)
    draft_node["selected_player_ids"] = selected
    picks = [dict(row) for row in (source_draft.get("picks") or [])]
    picks.append({
        "draft_season": str(draft_season),
        "round": int(round_no),
        "slot": int(slot),
        "pick_no": (int(round_no) - 1) * 12 + int(slot),
        "original_roster_id": str(original_roster_id),
        "controller_roster_id": str(controller_roster_id),
        "controller_user_id": str(controller_user_id),
        "player_id": pid,
        "player_name": player.get("player_name"),
        "position": player.get("position"),
        "actual_market_pick_no": int(player.get("pick_no") or 0),
    })
    draft_node["picks"] = picks
    out[draft_runner.DRAFT_KEY] = draft_node
    return out


def _canonical_roster_subset(state: Dict[str, Any], key: str) -> Tuple[Tuple[str, Tuple[str, ...]], ...]:
    return tuple(
        (str(k), tuple(sorted(str(x) for x in (v or []))))
        for k, v in sorted((state.get(key) or {}).items(), key=lambda row: str(row[0]))
    )


def _freeze_json_exact(value: Any) -> Any:
    """Convert JSON-shaped data to an exact hashable structure without serialization."""
    if isinstance(value, dict):
        return ("dict", tuple(
            (str(k), _freeze_json_exact(v))
            for k, v in sorted(value.items(), key=lambda row: str(row[0]))
        ))
    if isinstance(value, list):
        return ("list", tuple(_freeze_json_exact(x) for x in value))
    if isinstance(value, tuple):
        return ("tuple", tuple(_freeze_json_exact(x) for x in value))
    if isinstance(value, set):
        frozen = [_freeze_json_exact(x) for x in value]
        frozen.sort(key=repr)
        return ("set", tuple(frozen))
    return value


def _ledger_hash(ledger: Dict[str, Any]) -> str:
    identity = id(ledger)
    cached = _LEDGER_HASH_CACHE.get(identity)
    if cached is None:
        # Retain the source object so Python cannot recycle its id while cached.
        _SOURCE_OBJECTS[identity] = ledger
        cached = ah.stable_hash(ledger)
        _LEDGER_HASH_CACHE[identity] = cached
    return cached


def _state_key_with_memo(state: Dict[str, Any]) -> Tuple[Any, ...]:
    """Exact state identity without JSON-serializing the non-ledger state."""
    ledger = state.get(season_v3.LEDGER_KEY) or {}
    return (
        _canonical_roster_subset(state, "roster_players"),
        _canonical_roster_subset(state, "roster_taxi"),
        _canonical_roster_subset(state, "roster_reserve"),
        tuple(sorted((str(k), str(v)) for k, v in (state.get("pick_owners") or {}).items())),
        tuple(sorted((str(k), float(v or 0.0)) for k, v in (state.get("faab") or {}).items())),
        _freeze_json_exact(state.get(season_v3.DRAFT_KEY) or {}),
        _ledger_hash(ledger),
    )


def merge_groups_memoized(
    groups: Iterable[season_v3.SeasonParticleGroup],
) -> Tuple[list[season_v3.SeasonParticleGroup], int]:
    """Merge exact-equivalent states without repeated full-state JSON encoding."""
    by_key: Dict[Tuple[Any, ...], season_v3.SeasonParticleGroup] = {}
    merged_particles = 0

    for group in groups:
        if group.count <= 0:
            continue
        key = _state_key_with_memo(group.state)
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
    pos_id = id(positions)
    realized_id = id(realized)
    _SOURCE_OBJECTS[pos_id] = positions
    _SOURCE_OBJECTS[realized_id] = realized

    # The reference MaxPF optimizer permits players to remain unused and uses
    # EMPTY="0" for unfilled slots. Non-positive scorers can therefore never
    # improve the maximum or its lexicographic tie-break. Ignoring them makes
    # equivalent scoring rosters share the same exact cache entry.
    scoring_players = tuple(sorted(
        str(pid) for pid in roster_players
        if str(pid) not in {"0", "None", ""}
        and float(realized.get(str(pid)) or 0.0) > 0.0
    ))
    key = (
        pos_id,
        realized_id,
        tuple(str(x) for x in slots),
        scoring_players,
    )
    cached = _MAXPF_CACHE.get(key)
    if cached is None:
        max_pf, lineup = _ORIGINAL_BEST_LINEUP(
            scoring_players,
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


def _fixed_from_sorted(
    sorted_pool: list[Dict[str, Any]],
    excluded: set[str],
    count: int,
) -> list[Dict[str, Any]] | None:
    result = []
    for candidate in sorted_pool:
        if candidate["player_id"] in excluded:
            continue
        result.append(candidate)
        if len(result) == count:
            return result
    return None


def simulator_optimize_exact_presorted(roster, week, league, players, projections):
    """Same exact FSFFL optimizer, but positional pools are sorted only once."""
    slots = simulator.core.lineup_slots(league)
    if not simulator.standard_fsffl_slot_counts(slots):
        return _ORIGINAL_SIM_OPTIMIZE(roster, week, league, players, projections)

    candidates = simulator.candidate_rows(roster, week, players, projections)
    if not candidates:
        return _ORIGINAL_SIM_OPTIMIZE(roster, week, league, players, projections)

    pools = {
        pos: sorted(
            [c for c in candidates if c["position"] == pos],
            key=lambda c: c["value"],
            reverse=True,
        )
        for pos in ("QB", "RB", "WR", "TE")
    }
    sf_pool = [c for c in candidates if c["position"] in {"QB", "RB", "WR", "TE"}]
    flex_pool = [c for c in candidates if c["position"] in {"RB", "WR", "TE"}]

    best_total = -1e18
    best = None
    sf_options = [None] + sf_pool
    flex_options = [None] + flex_pool

    # Iteration order, candidate order, comparison, and first-best tie behavior
    # intentionally match optimize_fsffl_fast exactly.
    for sf in sf_options:
        sf_id = sf["player_id"] if sf else None
        for fl in flex_options:
            fl_id = fl["player_id"] if fl else None
            if sf_id is not None and sf_id == fl_id:
                continue
            used = {x for x in (sf_id, fl_id) if x is not None}

            qb = _fixed_from_sorted(pools["QB"], used, 1)
            if qb is None:
                continue
            used_qb = used | {x["player_id"] for x in qb}
            rb = _fixed_from_sorted(pools["RB"], used_qb, 2)
            if rb is None:
                continue
            used_rb = used_qb | {x["player_id"] for x in rb}
            wr = _fixed_from_sorted(pools["WR"], used_rb, 3)
            if wr is None:
                continue
            used_wr = used_rb | {x["player_id"] for x in wr}
            te = _fixed_from_sorted(pools["TE"], used_wr, 1)
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
        return _ORIGINAL_SIM_OPTIMIZE(roster, week, league, players, projections)

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
    return lineup


def simulator_lineup_cached(roster, week, league, players, projections):
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
            simulator_optimize_exact_presorted(roster, week, league, players, projections)
        )
        _SIM_LINEUP_CACHE[key] = cached
    return copy.deepcopy(cached)


def simulator_backups_cached(roster, week, lineup, players, projections):
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
    boundary_core.realized_lineup_points = realized_lineup_points_cached
    boundary_core.choose_branch_lineup = choose_branch_lineup_cached
    draft_runner.apply_draft_pick = apply_draft_pick_cow
    simulator.optimize_fsffl_fast = simulator_lineup_cached
    simulator.build_backup_chains = simulator_backups_cached
