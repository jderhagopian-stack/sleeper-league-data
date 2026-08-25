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
_ORIGINAL_SCORE_REGULAR_WEEK = season_v3.score_regular_week


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
            if pid in {str(x) for x in values} or rid == str(controller_roster_id):
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
        _SOURCE_OBJECTS[identity] = ledger
        cached = ah.stable_hash(ledger)
        _LEDGER_HASH_CACHE[identity] = cached
    return cached


def _state_key_with_memo(state: Dict[str, Any]) -> Tuple[Any, ...]:
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


def score_regular_week_cow(
    groups,
    *,
    season: str,
    week: int,
    matchup_rows,
    slots,
    positions,
    weekly_points,
):
    """Exact weekly scoring with copy-on-write only for the active season row.

    Completed prior-season ledger rows are immutable after their season closes.
    The reference implementation deep-copies every completed season on every
    subsequent scoring week. This keeps those rows shared and detaches only the
    season row that is about to be mutated.
    """
    season_league, _ = season_v3.roster_compliance.season_league_profile(str(season))
    slots = season_v3.starter_slots(season_league)

    compliance_audit = None
    if int(week) == 1:
        compliance_audit = season_v3.roster_compliance.enforce_completed_season_roster_rules(
            groups,
            season=str(season),
        )

    missing_point_particles = 0
    lineup_change_particles = 0
    rows_by_roster = {str(row.get("roster_id")): row for row in matchup_rows}
    realized = weekly_points.get(int(week), {})

    for group in groups:
        source_ledger = group.state.get(season_v3.LEDGER_KEY) or {}
        ledger = dict(source_ledger)
        source_season_row = source_ledger.get(str(season))
        if source_season_row is None:
            season_row = {
                "weekly_lineups": {},
                "weekly_scores": {},
                "weekly_max_pf": {},
                "season_max_pf": {},
                "records": {},
                "previous_alt_starters": {},
                "data_gaps": [],
            }
        else:
            season_row = copy.deepcopy(source_season_row)
        ledger[str(season)] = season_row

        weekly_lineups = season_row.setdefault("weekly_lineups", {})
        weekly_scores = season_row.setdefault("weekly_scores", {})
        weekly_max = season_row.setdefault("weekly_max_pf", {})
        season_max = season_row.setdefault("season_max_pf", {})
        previous = season_row.setdefault("previous_alt_starters", {})
        records = season_row.setdefault("records", {})
        scores: Dict[str, float] = {}

        for rid, actual_row in rows_by_roster.items():
            owned_players = {
                str(x)
                for x in ((group.state.get("roster_players") or {}).get(str(rid), []) or [])
            }
            taxi = {
                str(x)
                for x in ((group.state.get("roster_taxi") or {}).get(str(rid), []) or [])
            }
            reserve = {
                str(x)
                for x in ((group.state.get("roster_reserve") or {}).get(str(rid), []) or [])
            }
            active_roster_players = sorted(owned_players - taxi - reserve)
            prev = {str(x) for x in (previous.get(str(rid)) or [])}
            lineup, changes = season_v3.choose_branch_lineup(
                actual_row,
                active_roster_players,
                week=week,
                slots=slots,
                positions=positions,
                weekly_points=weekly_points,
                previous_alt_starters=prev,
            )
            score, missing = season_v3.realized_lineup_points(
                lineup,
                week=week,
                weekly_points=weekly_points,
            )
            max_pf, max_lineup = season_v3.best_lineup_points(
                sorted(owned_players),
                slots,
                positions,
                realized,
            )
            scores[str(rid)] = score
            weekly_lineups.setdefault(str(rid), {})[str(week)] = {
                "starters": lineup,
                "changes": changes,
            }
            weekly_scores.setdefault(str(rid), {})[str(week)] = score
            weekly_max.setdefault(str(rid), {})[str(week)] = {
                "max_pf": max_pf,
                "lineup": max_lineup,
            }
            season_max[str(rid)] = round(float(season_max.get(str(rid)) or 0.0) + float(max_pf), 2)
            previous[str(rid)] = [pid for pid in lineup if pid not in {"0", "None", ""}]
            if changes:
                lineup_change_particles += group.count
            if missing:
                missing_point_particles += group.count
                season_row.setdefault("data_gaps", []).append({
                    "week": week,
                    "roster_id": str(rid),
                    "missing_player_ids": missing,
                })

        season_v3.update_records_from_week(records, matchup_rows, scores)
        group.state[season_v3.LEDGER_KEY] = ledger

    result = {
        "missing_point_particle_roster_instances": missing_point_particles,
        "lineup_change_particle_roster_instances": lineup_change_particles,
    }
    if compliance_audit is not None:
        result["roster_compliance"] = compliance_audit
    return result


def _sim_roster_signature(roster: Dict[str, Any]) -> Tuple[Tuple[str, ...], ...]:
    return (
        tuple(sorted(str(x) for x in (roster.get("players") or []))),
        tuple(sorted(str(x) for x in (roster.get("taxi") or []))),
        tuple(sorted(str(x) for x in (roster.get("reserve") or []))),
    )


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
        cached = _ORIGINAL_SIM_OPTIMIZE(roster, week, league, players, projections)
        _SIM_LINEUP_CACHE[key] = cached
    return cached


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
        cached = _ORIGINAL_SIM_BACKUPS(roster, week, lineup, players, projections)
        _SIM_BACKUP_CACHE[key] = cached
    return cached


def cache_stats() -> Dict[str, int]:
    return {
        "source_objects": len(_SOURCE_OBJECTS),
        "season_observed": len(_SEASON_OBSERVED),
        "lineup_cache": len(_LINEUP_CACHE),
        "maxpf_cache": len(_MAXPF_CACHE),
        "sim_lineup_cache": len(_SIM_LINEUP_CACHE),
        "sim_backup_cache": len(_SIM_BACKUP_CACHE),
        "ledger_hash_cache": len(_LEDGER_HASH_CACHE),
    }


def install() -> None:
    season_v3.apply_preserving_ledger = apply_preserving_ledger_cow
    season_v3.merge_groups = merge_groups_memoized
    season_v3.realized_lineup_points = realized_lineup_points_cached
    season_v3.choose_branch_lineup = choose_branch_lineup_cached
    season_v3.best_lineup_points = best_lineup_points_cached
    season_v3.score_regular_week = score_regular_week_cow
    boundary_core.realized_lineup_points = realized_lineup_points_cached
    boundary_core.choose_branch_lineup = choose_branch_lineup_cached
    draft_runner.apply_draft_pick = apply_draft_pick_cow
    simulator.optimize_fsffl_fast = simulator_lineup_cached
    simulator.build_backup_chains = simulator_backups_cached
