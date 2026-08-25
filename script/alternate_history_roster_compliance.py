#!/usr/bin/env python3
"""Historical-safe roster compliance for Fantasy Alternate History.

Rookie drafts temporarily expand branch rosters. Completed seasons contract to
that franchise's observed Week 1 roster-membership count before Week 1 is
scored. The active season contracts to the current canonical Sleeper roster
count after completed transactions are replayed.

Capacity is contemporaneous league evidence only. Player retention never uses
points from the season about to be replayed. Selection priority is:
1. players the real manager retained in the contemporaneous roster snapshot;
2. rookies the branch manager selected in that season's alternate rookie draft;
3. completed prior-season fantasy production;
4. stable player-id tie break.
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

import alternate_history_engine as ah
import run_fsffl_multiseason_particle_replay_v3 as season_v3
from run_fsffl_downstream_dependencies import load
from run_fsffl_historical_usage_policy import HistoricalPoints

DRAFT_KEY = "_alternate_history_rookie_draft"
SeasonParticleGroup = season_v3.SeasonParticleGroup


def week1_rosters(season: str) -> Dict[str, set[str]]:
    payload = load(ah.DATA / "stats" / "fsffl" / str(season) / "league_matchups_raw.json") or {}
    rows = payload.get("1") or []
    out: Dict[str, set[str]] = {}
    for row in rows:
        rid = str(row.get("roster_id") or "")
        if rid:
            out[rid] = {str(pid) for pid in (row.get("players") or [])}
    if not out:
        raise ah.AlternateHistoryError(f"Week 1 roster envelope unavailable for {season}")
    return out


def current_rosters() -> Dict[str, set[str]]:
    rows = load(ah.DATA / "rosters.json") or []
    out = {
        str(row.get("roster_id")): {str(pid) for pid in (row.get("players") or [])}
        for row in rows
        if row.get("roster_id") is not None
    }
    if not out:
        raise ah.AlternateHistoryError("Current roster envelope unavailable")
    return out


def drafted_by_roster(state: Dict[str, Any], season: str) -> Dict[str, Dict[str, Tuple[int, int]]]:
    out: Dict[str, Dict[str, Tuple[int, int]]] = {}
    for row in ((state.get(DRAFT_KEY) or {}).get("picks") or []):
        if str(row.get("draft_season") or "") != str(season):
            continue
        rid = str(row.get("controller_roster_id") or "")
        pid = str(row.get("player_id") or "")
        if rid and pid:
            out.setdefault(rid, {})[pid] = (
                int(row.get("round") or 99),
                int(row.get("pick_no") or 999),
            )
    return out


def prior_season_totals(points: HistoricalPoints, season: str) -> Dict[str, float]:
    weekly = points.season(str(int(season) - 1))
    totals: Dict[str, float] = {}
    for rows in weekly.values():
        for pid, value in rows.items():
            totals[str(pid)] = totals.get(str(pid), 0.0) + float(value or 0.0)
    return totals


def retention_key(pid: str, *, actual_kept: set[str], drafted: Dict[str, Tuple[int, int]], prior_totals: Dict[str, float]) -> tuple:
    draft_meta = drafted.get(str(pid))
    return (
        0 if str(pid) in actual_kept else 1,
        0 if draft_meta is not None else 1,
        draft_meta[0] if draft_meta is not None else 99,
        draft_meta[1] if draft_meta is not None else 999,
        -float(prior_totals.get(str(pid), 0.0)),
        str(pid),
    )


def _enforce(groups: List[SeasonParticleGroup], *, season: str, particles: int, actual: Dict[str, set[str]], capacity_source: str) -> Tuple[List[SeasonParticleGroup], Dict[str, Any]]:
    prior_totals = prior_season_totals(HistoricalPoints(), str(season))
    total_removed = 0
    particle_rosters_trimmed = 0
    max_before = 0
    max_after = 0
    audits = []

    for group in groups:
        drafted = drafted_by_roster(group.state, str(season))
        roster_map = group.state.setdefault("roster_players", {})
        taxi_map = group.state.setdefault("roster_taxi", {})
        reserve_map = group.state.setdefault("roster_reserve", {})
        for rid, actual_players in actual.items():
            current = sorted({str(pid) for pid in (roster_map.get(rid) or [])})
            capacity = len(actual_players)
            max_before = max(max_before, len(current))
            if len(current) <= capacity:
                max_after = max(max_after, len(current))
                continue
            ranked = sorted(current, key=lambda pid: retention_key(
                pid,
                actual_kept=actual_players,
                drafted=drafted.get(rid, {}),
                prior_totals=prior_totals,
            ))
            keep = set(ranked[:capacity])
            removed = sorted(set(current) - keep)
            roster_map[rid] = sorted(keep)
            taxi_map[rid] = sorted({str(pid) for pid in (taxi_map.get(rid) or [])} & keep)
            reserve_map[rid] = sorted({str(pid) for pid in (reserve_map.get(rid) or [])} & keep)
            total_removed += len(removed) * group.count
            particle_rosters_trimmed += group.count
            max_after = max(max_after, len(keep))
            audits.append({
                "roster_id": rid,
                "particles": group.count,
                "capacity": capacity,
                "before": len(current),
                "after": len(keep),
                "removed_player_ids": removed,
            })

    groups, merged = season_v3.merge_groups(groups)
    if sum(group.count for group in groups) != particles:
        raise ah.AlternateHistoryError(f"Roster compliance lost particle mass for {season}")
    for group in groups:
        for rid, actual_players in actual.items():
            size = len((group.state.get("roster_players") or {}).get(rid) or [])
            if size > len(actual_players):
                raise ah.AlternateHistoryError(
                    f"Roster compliance failed for {season} roster {rid}: {size}>{len(actual_players)}"
                )

    return groups, {
        "season": str(season),
        "capacity_source": capacity_source,
        "future_season_points_used": False,
        "retention_signals": [
            "contemporaneous_revealed_retention",
            "branch_rookie_draft_investment",
            "completed_prior_season_fantasy_points",
        ],
        "particle_roster_instances_trimmed": particle_rosters_trimmed,
        "weighted_players_removed": total_removed,
        "max_roster_size_before": max_before,
        "max_roster_size_after": max_after,
        "particles_merged_after_compliance": merged,
        "audit": audits,
    }


def enforce_week1_roster_envelope(groups: List[SeasonParticleGroup], *, season: str, particles: int):
    return _enforce(
        groups,
        season=str(season),
        particles=particles,
        actual=week1_rosters(str(season)),
        capacity_source="actual_week1_roster_membership_count",
    )


def enforce_current_roster_envelope(groups: List[SeasonParticleGroup], *, season: str, particles: int):
    return _enforce(
        groups,
        season=str(season),
        particles=particles,
        actual=current_rosters(),
        capacity_source="canonical_current_roster_membership_count",
    )
