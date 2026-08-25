#!/usr/bin/env python3
"""Finer exact copy-on-write for Alternate History weekly season scoring.

This patch changes only data movement. Historical inputs, lineup selection,
realized scoring, MaxPF, records, particle counts, and branch behavior remain
owned by the validated reference functions. Prior completed weeks are treated
as immutable and shared; only maps mutated by the current week are detached.
"""

from __future__ import annotations

from typing import Any, Dict

import run_fsffl_multiseason_particle_replay_v3 as season_v3


def _copy_nested_week_map(source: Any) -> Dict[str, Dict[str, Any]]:
    return {
        str(rid): dict(weeks or {})
        for rid, weeks in (source or {}).items()
    }


def _copy_records(source: Any) -> Dict[str, Dict[str, Any]]:
    return {
        str(rid): dict(row or {})
        for rid, row in (source or {}).items()
    }


def score_regular_week_fine_cow(
    groups,
    *,
    season: str,
    week: int,
    matchup_rows,
    slots,
    positions,
    weekly_points,
):
    """Score one historical week while copying only structures that mutate."""
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
        source_row = source_ledger.get(str(season)) or {}
        season_row = dict(source_row)

        weekly_lineups = _copy_nested_week_map(source_row.get("weekly_lineups"))
        weekly_scores = _copy_nested_week_map(source_row.get("weekly_scores"))
        weekly_max = _copy_nested_week_map(source_row.get("weekly_max_pf"))
        season_max = dict(source_row.get("season_max_pf") or {})
        previous = dict(source_row.get("previous_alt_starters") or {})
        records = _copy_records(source_row.get("records"))
        data_gaps = list(source_row.get("data_gaps") or [])

        season_row["weekly_lineups"] = weekly_lineups
        season_row["weekly_scores"] = weekly_scores
        season_row["weekly_max_pf"] = weekly_max
        season_row["season_max_pf"] = season_max
        season_row["previous_alt_starters"] = previous
        season_row["records"] = records
        season_row["data_gaps"] = data_gaps
        ledger[str(season)] = season_row

        scores: Dict[str, float] = {}
        roster_players_map = group.state.get("roster_players") or {}
        roster_taxi_map = group.state.get("roster_taxi") or {}
        roster_reserve_map = group.state.get("roster_reserve") or {}

        for rid, actual_row in rows_by_roster.items():
            owned_players = {str(x) for x in (roster_players_map.get(rid, []) or [])}
            taxi = {str(x) for x in (roster_taxi_map.get(rid, []) or [])}
            reserve = {str(x) for x in (roster_reserve_map.get(rid, []) or [])}
            active_roster_players = sorted(owned_players - taxi - reserve)
            prev = {str(x) for x in (previous.get(rid) or [])}

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

            scores[rid] = score
            weekly_lineups.setdefault(rid, {})[str(week)] = {
                "starters": lineup,
                "changes": changes,
            }
            weekly_scores.setdefault(rid, {})[str(week)] = score
            weekly_max.setdefault(rid, {})[str(week)] = {
                "max_pf": max_pf,
                "lineup": max_lineup,
            }
            season_max[rid] = round(float(season_max.get(rid) or 0.0) + float(max_pf), 2)
            previous[rid] = [pid for pid in lineup if pid not in {"0", "None", ""}]

            if changes:
                lineup_change_particles += group.count
            if missing:
                missing_point_particles += group.count
                data_gaps.append({
                    "week": week,
                    "roster_id": rid,
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


def install() -> None:
    season_v3.score_regular_week = score_regular_week_fine_cow
