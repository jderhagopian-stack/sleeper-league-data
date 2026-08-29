#!/usr/bin/env python3
"""Canonical exact lineup reoptimization utilities.

Provides the slot-mask dynamic-programming lineup solver originally introduced
in Counter Market Sweep v1.3. This module is version-neutral so current and
future FSFFL applications can reuse the exact same lineup mechanics without
depending on a historical trade-sweep wrapper.

This is a mechanical extraction only. The optimization objective and output
shape are intentionally unchanged.
"""
from __future__ import annotations

import copy

MODEL_VERSION = "FSFFL-Lineup-Optimizer-1.0"


def fast_optimize_weekly_lineup(simmod, roster, week, league, players, projections):
    """Exact max-weight legal lineup assignment via slot-mask DP."""
    candidates = []
    taxi = set(roster.get("taxi") or [])
    for pid in roster.get("players") or []:
        if pid in taxi:
            continue
        meta = simmod.player_meta(players, projections, pid)
        pos = meta.get("position")
        pr = simmod.projection_for(projections, pid, week)
        if not pos or pr is None or pr["active_probability"] <= 0:
            continue
        candidates.append({**meta, **pr})

    slots = simmod.lineup_slots(league)
    states = {0: (0.0, {})}
    for c in candidates:
        weight = float(c["mean"]) * float(c["active_probability"])
        prior = list(states.items())
        for mask, (value, assign) in prior:
            for idx, slot in enumerate(slots):
                bit = 1 << idx
                if mask & bit or not simmod.eligible(c["position"], slot):
                    continue
                new_mask = mask | bit
                new_value = value + weight
                old = states.get(new_mask)
                if old is None or new_value > old[0]:
                    new_assign = dict(assign)
                    new_assign[idx] = c
                    states[new_mask] = (new_value, new_assign)

    _, best_assign = max(states.values(), key=lambda x: x[0])
    lineup = []
    for idx, slot in enumerate(slots):
        c = best_assign.get(idx)
        if c is None:
            lineup.append({
                "slot": slot, "player_id": None, "name": "EMPTY", "position": None,
                "mean": 0.0, "sd": 0.1, "active_probability": 0.0,
            })
        else:
            lineup.append({"slot": slot, **c})
    return lineup


def fast_reoptimize_touched_lineups(dl, simmod, baseline_lineups, hypothetical_rosters,
                                     touched_uids, league, users, players, projections):
    """Re-optimize only franchises touched by a hypothetical transaction."""
    lineups = copy.deepcopy(baseline_lineups)
    by_uid, _ = dl.roster_maps(hypothetical_rosters)
    reg_weeks = simmod.regular_season_weeks(league)
    playoff_start = int((league.get("settings") or {}).get("playoff_week_start") or 15)
    all_weeks = sorted(set(reg_weeks + [playoff_start, playoff_start + 1, playoff_start + 2]))
    reoptimized = []
    for uid in touched_uids:
        roster = by_uid.get(str(uid))
        if not roster:
            continue
        rid = int(roster.get("roster_id"))
        lineups[rid] = {}
        for week in all_weeks:
            lineups[rid][week] = fast_optimize_weekly_lineup(
                simmod, roster, week, league, players, projections
            )
        reoptimized.append(rid)
    return lineups, reoptimized
