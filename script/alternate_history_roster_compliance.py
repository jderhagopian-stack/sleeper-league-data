#!/usr/bin/env python3
"""Sleeper-rule roster compliance for Fantasy Alternate History.

Rookie drafts may temporarily expand branch rosters. Before a completed season
is scored, and again at the active-season Simulator handoff, each branch is
contracted against the roster structure defined by that season's Sleeper league
profile.

Authoritative capacity inputs:
- roster_positions -> normal active roster capacity;
- settings.reserve_slots -> reserve/IR capacity;
- settings.taxi_slots -> taxi capacity;
- settings.taxi_allow_vets / taxi_years -> supported taxi eligibility rules.

Historical Week 1/current roster snapshots are behavioral evidence only. They
never define capacity. Historical IR placement is not invented when archived
eligibility state is unavailable; that limitation is reported explicitly.
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

import alternate_history_engine as ah
from run_fsffl_downstream_dependencies import load
from run_fsffl_historical_usage_policy import HistoricalPoints

DRAFT_KEY = "_alternate_history_rookie_draft"
NON_ACTIVE_POSITION_MARKERS = {"IR", "RESERVE", "TAXI"}


def season_league_profile(season: str) -> Tuple[Dict[str, Any], str]:
    season = str(season)
    current = ah.load_json(ah.DATA / "league.json", {}) or {}
    if str(current.get("season") or "") == season:
        return current, "canonical_current_sleeper_league_profile"

    manifest = ah.load_json(
        ah.AH_ROOT / "source_history" / "sleeper_history.json", {}
    ) or {}
    for row in manifest.get("history") or []:
        league = row.get("league") or {}
        if str(league.get("season") or "") == season:
            return league, "archived_sleeper_league_profile"
    raise ah.AlternateHistoryError(f"Sleeper league profile unavailable for {season}")


def roster_rules(season: str) -> Dict[str, Any]:
    league, source = season_league_profile(str(season))
    settings = league.get("settings") or {}
    roster_positions = [
        str(slot)
        for slot in (league.get("roster_positions") or [])
        if str(slot).upper() not in NON_ACTIVE_POSITION_MARKERS
    ]
    active_slots = len(roster_positions)
    reserve_slots = int(settings.get("reserve_slots") or 0)
    taxi_slots = int(settings.get("taxi_slots") or 0)
    if active_slots <= 0:
        raise ah.AlternateHistoryError(
            f"Sleeper active roster capacity unavailable for {season}"
        )
    return {
        "season": str(season),
        "source": source,
        "league_id": str(league.get("league_id") or ""),
        "active_slots": active_slots,
        "reserve_slots": reserve_slots,
        "taxi_slots": taxi_slots,
        "total_owned_capacity": active_slots + reserve_slots + taxi_slots,
        "taxi_allow_vets": int(settings.get("taxi_allow_vets") or 0),
        "taxi_years": int(settings.get("taxi_years") or 0),
        "taxi_deadline": settings.get("taxi_deadline"),
        "reserve_eligibility": {
            key: int(settings.get(key) or 0)
            for key in (
                "reserve_allow_cov",
                "reserve_allow_dnr",
                "reserve_allow_doubtful",
                "reserve_allow_na",
                "reserve_allow_out",
                "reserve_allow_sus",
            )
        },
        "roster_positions": roster_positions,
    }


def behavioral_rosters(season: str, *, active: bool) -> Dict[str, set[str]]:
    """Contemporaneous revealed retention evidence; never a capacity source."""
    if active:
        rows = load(ah.DATA / "rosters.json") or []
        return {
            str(row.get("roster_id")): {str(pid) for pid in (row.get("players") or [])}
            for row in rows
            if row.get("roster_id") is not None
        }
    payload = load(
        ah.DATA / "stats" / "fsffl" / str(season) / "league_matchups_raw.json"
    ) or {}
    out: Dict[str, set[str]] = {}
    for row in payload.get("1") or []:
        rid = str(row.get("roster_id") or "")
        if rid:
            out[rid] = {str(pid) for pid in (row.get("players") or [])}
    return out


def current_slot_placements() -> Tuple[Dict[str, set[str]], Dict[str, set[str]]]:
    rows = load(ah.DATA / "rosters.json") or []
    reserve = {}
    taxi = {}
    for row in rows:
        if row.get("roster_id") is None:
            continue
        rid = str(row.get("roster_id"))
        reserve[rid] = {str(pid) for pid in (row.get("reserve") or [])}
        taxi[rid] = {str(pid) for pid in (row.get("taxi") or [])}
    return reserve, taxi


def drafted_history_by_roster(
    state: Dict[str, Any], season: str
) -> Dict[str, Dict[str, Tuple[int, int, int]]]:
    """pid -> (draft season, round, pick) for branch-specific rookie selections."""
    out: Dict[str, Dict[str, Tuple[int, int, int]]] = {}
    for row in ((state.get(DRAFT_KEY) or {}).get("picks") or []):
        draft_season = int(row.get("draft_season") or 0)
        if draft_season <= 0 or draft_season > int(season):
            continue
        rid = str(row.get("controller_roster_id") or "")
        pid = str(row.get("player_id") or "")
        if rid and pid:
            out.setdefault(rid, {})[pid] = (
                draft_season,
                int(row.get("round") or 99),
                int(row.get("pick_no") or 999),
            )
    return out


def prior_season_totals(points: HistoricalPoints, season: str) -> Dict[str, float]:
    prior = str(int(season) - 1)
    matchup_path = ah.DATA / "stats" / "fsffl" / prior / "league_matchups_raw.json"
    weekly_path = ah.DATA / "stats" / "fsffl" / prior / "player_weekly_fsffl.json"
    if not matchup_path.exists() and not weekly_path.exists():
        return {}
    weekly = points.season(prior)
    totals: Dict[str, float] = {}
    for rows in weekly.values():
        for pid, value in rows.items():
            totals[str(pid)] = totals.get(str(pid), 0.0) + float(value or 0.0)
    return totals


def retention_score(
    pid: str,
    *,
    revealed_kept: set[str],
    drafted: Dict[str, Tuple[int, int, int]],
    prior_totals: Dict[str, float],
) -> float:
    """Historical-safe retention score; higher means stronger keep preference."""
    score = float(prior_totals.get(str(pid), 0.0))
    if str(pid) in revealed_kept:
        score += 75.0
    meta = drafted.get(str(pid))
    if meta is not None:
        _, round_no, _ = meta
        score += {1: 125.0, 2: 85.0, 3: 55.0}.get(round_no, 35.0)
    return score


def taxi_eligible_from_branch_history(
    pid: str,
    *,
    season: str,
    drafted: Dict[str, Tuple[int, int, int]],
    rules: Dict[str, Any],
) -> bool:
    if int(rules.get("taxi_slots") or 0) <= 0:
        return False
    if int(rules.get("taxi_allow_vets") or 0) == 1:
        return True
    meta = drafted.get(str(pid))
    if meta is None:
        return False
    draft_season = int(meta[0])
    taxi_years = int(rules.get("taxi_years") or 0)
    if taxi_years <= 0:
        return False
    player_year = int(season) - draft_season + 1
    return 1 <= player_year <= taxi_years


def _enforce(
    groups: List[Any],
    *,
    season: str,
    active: bool,
) -> Dict[str, Any]:
    rules = roster_rules(str(season))
    revealed = behavioral_rosters(str(season), active=active)
    prior_totals = prior_season_totals(HistoricalPoints(), str(season))
    current_reserve, current_taxi = current_slot_placements() if active else ({}, {})

    weighted_removed = 0
    weighted_to_taxi = 0
    particle_rosters_changed = 0
    max_owned_before = 0
    max_owned_after = 0
    max_active_after = 0
    audits = []

    for group in groups:
        drafted_by_roster = drafted_history_by_roster(group.state, str(season))
        roster_map = group.state.setdefault("roster_players", {})
        taxi_map = group.state.setdefault("roster_taxi", {})
        reserve_map = group.state.setdefault("roster_reserve", {})

        for rid in sorted(roster_map, key=str):
            owned = {str(pid) for pid in (roster_map.get(rid) or [])}
            before = len(owned)
            max_owned_before = max(max_owned_before, before)
            revealed_kept = revealed.get(rid, set())
            drafted = drafted_by_roster.get(rid, {})

            # Active-season placement can safely preserve the live Sleeper IR/taxi
            # subsets for players still owned by that same franchise. Historical
            # placement is not inferred from current/future player status.
            reserve = (
                set(current_reserve.get(rid, set())) & owned
                if active
                else set()
            )
            taxi = (
                set(current_taxi.get(rid, set())) & owned
                if active
                else set()
            )
            reserve -= taxi

            reserve_cap = int(rules["reserve_slots"])
            taxi_cap = int(rules["taxi_slots"])
            active_cap = int(rules["active_slots"])

            if len(reserve) > reserve_cap:
                ranked = sorted(
                    reserve,
                    key=lambda pid: (
                        -retention_score(
                            pid,
                            revealed_kept=revealed_kept,
                            drafted=drafted,
                            prior_totals=prior_totals,
                        ),
                        pid,
                    ),
                )
                reserve = set(ranked[:reserve_cap])
            if len(taxi) > taxi_cap:
                ranked = sorted(
                    taxi,
                    key=lambda pid: (
                        -retention_score(
                            pid,
                            revealed_kept=revealed_kept,
                            drafted=drafted,
                            prior_totals=prior_totals,
                        ),
                        pid,
                    ),
                )
                taxi = set(ranked[:taxi_cap])

            active_players = owned - reserve - taxi

            # If the active roster is oversized, use available legal taxi slots
            # before cutting. We place the weakest taxi-eligible branch-drafted
            # players there, preserving stronger assets on the active roster.
            taxi_room = max(0, taxi_cap - len(taxi))
            if len(active_players) > active_cap and taxi_room > 0:
                candidates = [
                    pid
                    for pid in active_players
                    if taxi_eligible_from_branch_history(
                        pid,
                        season=str(season),
                        drafted=drafted,
                        rules=rules,
                    )
                ]
                candidates.sort(
                    key=lambda pid: (
                        retention_score(
                            pid,
                            revealed_kept=revealed_kept,
                            drafted=drafted,
                            prior_totals=prior_totals,
                        ),
                        pid,
                    )
                )
                need = min(taxi_room, max(0, len(active_players) - active_cap))
                moved = candidates[:need]
                taxi.update(moved)
                active_players.difference_update(moved)
                weighted_to_taxi += len(moved) * group.count

            removed: List[str] = []
            if len(active_players) > active_cap:
                ranked_weakest = sorted(
                    active_players,
                    key=lambda pid: (
                        retention_score(
                            pid,
                            revealed_kept=revealed_kept,
                            drafted=drafted,
                            prior_totals=prior_totals,
                        ),
                        pid,
                    )
                )
                cut_count = len(active_players) - active_cap
                removed = ranked_weakest[:cut_count]
                active_players.difference_update(removed)
                owned.difference_update(removed)
                reserve.difference_update(removed)
                taxi.difference_update(removed)
                weighted_removed += len(removed) * group.count

            # A final hard total-cap assertion protects against malformed subsets.
            total_cap = int(rules["total_owned_capacity"])
            if len(owned) > total_cap:
                raise ah.AlternateHistoryError(
                    f"Sleeper roster compliance total cap failed for {season} roster {rid}: "
                    f"{len(owned)}>{total_cap}"
                )
            if len(active_players) > active_cap or len(reserve) > reserve_cap or len(taxi) > taxi_cap:
                raise ah.AlternateHistoryError(
                    f"Sleeper slot compliance failed for {season} roster {rid}"
                )

            roster_map[rid] = sorted(owned)
            reserve_map[rid] = sorted(reserve)
            taxi_map[rid] = sorted(taxi)
            max_owned_after = max(max_owned_after, len(owned))
            max_active_after = max(max_active_after, len(active_players))

            if removed or before != len(owned) or taxi != set(current_taxi.get(rid, set()) if active else []):
                particle_rosters_changed += group.count
                audits.append({
                    "roster_id": rid,
                    "particles": group.count,
                    "owned_before": before,
                    "owned_after": len(owned),
                    "active_after": len(active_players),
                    "reserve_after": len(reserve),
                    "taxi_after": len(taxi),
                    "removed_player_ids": sorted(removed),
                    "taxi_player_ids": sorted(taxi),
                    "reserve_player_ids": sorted(reserve),
                })

    return {
        "season": str(season),
        "capacity_source": rules["source"],
        "sleeper_roster_rules": rules,
        "future_season_points_used": False,
        "prior_season_points_available": bool(prior_totals),
        "historical_week1_snapshot_used_for_capacity": False,
        "historical_week1_snapshot_used_as_behavioral_signal": not active,
        "historical_reserve_eligibility_invented": False,
        "historical_reserve_slot_placement_known_limitation": (
            None if active else "Archived timestamp-specific IR placement/status is unavailable; no new historical reserve placement is inferred."
        ),
        "retention_signals": [
            "contemporaneous_revealed_retention",
            "branch_rookie_draft_investment",
            "completed_prior_season_fantasy_points_when_available",
        ],
        "particle_roster_instances_changed": particle_rosters_changed,
        "weighted_players_removed": weighted_removed,
        "weighted_players_moved_to_taxi": weighted_to_taxi,
        "max_owned_roster_size_before": max_owned_before,
        "max_owned_roster_size_after": max_owned_after,
        "max_active_roster_size_after": max_active_after,
        "audit": audits,
    }


def enforce_completed_season_roster_rules(groups: List[Any], *, season: str) -> Dict[str, Any]:
    return _enforce(groups, season=str(season), active=False)


def enforce_current_season_roster_rules(groups: List[Any], *, season: str) -> Dict[str, Any]:
    return _enforce(groups, season=str(season), active=True)


# Backward-compatible aliases while older validation/reporting entrypoints are
# migrated. Their behavior now uses Sleeper rules, not observed roster counts.
def enforce_week1_roster_envelope(groups: List[Any], *, season: str) -> Dict[str, Any]:
    return enforce_completed_season_roster_rules(groups, season=str(season))


def enforce_current_roster_envelope(groups: List[Any], *, season: str) -> Dict[str, Any]:
    return enforce_current_season_roster_rules(groups, season=str(season))
