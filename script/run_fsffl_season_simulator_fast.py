#!/usr/bin/env python3
"""
Vectorized FSFFL Season Simulator runner.

Uses the existing Simulator 1.0 validation, lineup optimization, output schema,
and schedule logic, but replaces the slow per-season/per-player Monte Carlo
loop with NumPy bulk simulation.
"""

from __future__ import annotations

import os
from collections import defaultdict

import numpy as np

import build_fsffl_season_simulator as core


def vectorized_team_week_scores(lineup, n_sims, rng):
    total = np.zeros(n_sims, dtype=np.float32)
    for row in lineup:
        if row["player_id"] is None:
            continue

        mean = float(row["mean"])
        sd = max(0.1, float(row["sd"]))
        active_p = max(0.0, min(1.0, float(row["active_probability"])))

        draws = rng.normal(mean, sd, n_sims).astype(np.float32, copy=False)
        np.maximum(draws, 0.0, out=draws)

        if active_p <= 0.0:
            continue
        if active_p < 1.0:
            draws *= (rng.random(n_sims) <= active_p)

        total += draws
    return total


def fast_run_simulation(
    league,
    rosters,
    users,
    players,
    raw_schedule,
    projections,
    n_sims=50000,
    seed=20260821,
):
    season = str(league["season"])
    roster_dir = core.roster_directory(rosters, users)
    reg_weeks = core.regular_season_weeks(league)
    by_week, _ = core.build_schedule(raw_schedule, reg_weeks)

    playoff_start = int((league.get("settings") or {}).get("playoff_week_start") or 15)
    playoff_weeks = [playoff_start, playoff_start + 1, playoff_start + 2]
    all_needed_weeks = sorted(set(reg_weeks + playoff_weeks))

    roster_ids = sorted(roster_dir)
    rid_to_idx = {rid: i for i, rid in enumerate(roster_ids)}
    n_teams = len(roster_ids)

    # Exact lineup optimization still happens once per team/week, never once
    # per simulation.
    lineups = defaultdict(dict)
    for rid, roster in roster_dir.items():
        for week in all_needed_weeks:
            lineups[rid][week] = core.optimize_weekly_lineup(
                roster, week, league, players, projections
            )

    rng = np.random.default_rng(seed)

    # [simulation, week, team]
    scores = np.zeros((n_sims, len(all_needed_weeks), n_teams), dtype=np.float32)
    week_to_idx = {w: i for i, w in enumerate(all_needed_weeks)}

    for w in all_needed_weeks:
        wi = week_to_idx[w]
        for rid in roster_ids:
            scores[:, wi, rid_to_idx[rid]] = vectorized_team_week_scores(
                lineups[rid][w], n_sims, rng
            )

    reg_idx = [week_to_idx[w] for w in reg_weeks]
    pf = scores[:, reg_idx, :].sum(axis=1, dtype=np.float64)
    wins = np.zeros((n_sims, n_teams), dtype=np.int16)

    for w in reg_weeks:
        wi = week_to_idx[w]
        for a, b in by_week.get(w, []):
            ai, bi = rid_to_idx[a], rid_to_idx[b]
            a_wins = scores[:, wi, ai] >= scores[:, wi, bi]
            wins[:, ai] += a_wins
            wins[:, bi] += ~a_wins

    # Seed ordering: wins desc, PF desc, roster ID asc.
    # Only 12 teams, so sorting each row is cheap relative to score generation.
    orders = np.empty((n_sims, n_teams), dtype=np.int16)
    rid_array = np.asarray(roster_ids, dtype=np.int32)
    for s in range(n_sims):
        orders[s] = np.lexsort((rid_array, -pf[s], -wins[s]))

    playoff_teams = int((league.get("settings") or {}).get("playoff_teams") or 6)

    seed_counts = np.zeros((n_teams, n_teams), dtype=np.int64)
    playoff_counts = np.zeros(n_teams, dtype=np.int64)
    bye_counts = np.zeros(n_teams, dtype=np.int64)
    title_counts = np.zeros(n_teams, dtype=np.int64)
    division_counts = np.zeros(n_teams, dtype=np.int64)

    for seed_idx in range(n_teams):
        idxs = orders[:, seed_idx]
        np.add.at(seed_counts[:, seed_idx], idxs, 1)
        if seed_idx < playoff_teams:
            np.add.at(playoff_counts, idxs, 1)
        if seed_idx < 2:
            np.add.at(bye_counts, idxs, 1)

    division_members = defaultdict(list)
    for rid, info in roster_dir.items():
        if info.get("division") is not None:
            division_members[info["division"]].append(rid_to_idx[rid])

    # Only four small divisions; loop by simulation is negligible.
    for members in division_members.values():
        member_arr = np.asarray(members, dtype=np.int16)
        for s in range(n_sims):
            best = member_arr[
                np.lexsort((
                    rid_array[member_arr],
                    -pf[s, member_arr],
                    -wins[s, member_arr],
                ))[0]
            ]
            division_counts[best] += 1

    # Vectorized playoff bracket for six-team format.
    if playoff_teams >= 6 and len(playoff_weeks) >= 3:
        seed6 = orders[:, :6]
        sim_idx = np.arange(n_sims)

        w1 = week_to_idx[playoff_weeks[0]]
        w2 = week_to_idx[playoff_weeks[1]]
        w3 = week_to_idx[playoff_weeks[2]]

        s3, s4, s5, s6 = seed6[:, 2], seed6[:, 3], seed6[:, 4], seed6[:, 5]

        g1winner = np.where(
            scores[sim_idx, w1, s3] >= scores[sim_idx, w1, s6], s3, s6
        )
        g2winner = np.where(
            scores[sim_idx, w1, s4] >= scores[sim_idx, w1, s5], s4, s5
        )

        # Determine original seed numbers among each simulation's top six.
        seed_num = np.empty((n_sims, n_teams), dtype=np.int16)
        seed_num.fill(99)
        for j in range(6):
            seed_num[sim_idx, seed6[:, j]] = j + 1

        low = np.where(
            seed_num[sim_idx, g1winner] > seed_num[sim_idx, g2winner],
            g1winner,
            g2winner,
        )
        high = np.where(low == g1winner, g2winner, g1winner)

        seed1, seed2 = seed6[:, 0], seed6[:, 1]

        semi1 = np.where(
            scores[sim_idx, w2, seed1] >= scores[sim_idx, w2, low],
            seed1,
            low,
        )
        semi2 = np.where(
            scores[sim_idx, w2, seed2] >= scores[sim_idx, w2, high],
            seed2,
            high,
        )

        champion = np.where(
            scores[sim_idx, w3, semi1] >= scores[sim_idx, w3, semi2],
            semi1,
            semi2,
        )
        np.add.at(title_counts, champion, 1)

    win_totals = wins.sum(axis=0)
    pf_totals = pf.sum(axis=0)

    teams = []
    for rid in roster_ids:
        i = rid_to_idx[rid]
        info = roster_dir[rid]
        teams.append({
            "roster_id": rid,
            "user_id": info["user_id"],
            "manager": info["manager"],
            "team_name": info["team_name"],
            "division": info.get("division"),
            "expected_wins": round(float(win_totals[i]) / n_sims, 3),
            "expected_losses": round(
                len(reg_weeks) - (float(win_totals[i]) / n_sims), 3
            ),
            "expected_points_for": round(float(pf_totals[i]) / n_sims, 2),
            "playoff_probability": round(float(playoff_counts[i]) / n_sims, 5),
            "bye_probability": round(float(bye_counts[i]) / n_sims, 5),
            "division_probability": (
                round(float(division_counts[i]) / n_sims, 5)
                if info.get("division") is not None
                else None
            ),
            "championship_probability": round(float(title_counts[i]) / n_sims, 5),
            "seed_probabilities": {
                str(seed): round(
                    float(seed_counts[i, seed - 1]) / n_sims, 5
                )
                for seed in range(1, n_teams + 1)
            },
        })

    teams.sort(key=lambda x: (-x["expected_wins"], -x["expected_points_for"]))

    return {
        "generated_at_utc": core.now_utc(),
        "model_version": core.MODEL_VERSION + "-vectorized",
        "season": season,
        "simulations": n_sims,
        "regular_season_weeks": reg_weeks,
        "playoff_weeks": playoff_weeks,
        "teams": teams,
        "schedule": {
            str(w): [{"a": a, "b": b} for a, b in by_week.get(w, [])]
            for w in reg_weeks
        },
        "lineups": {
            str(rid): {
                str(w): [
                    {
                        "slot": row["slot"],
                        "player_id": row["player_id"],
                        "name": row["name"],
                        "position": row["position"],
                        "mean": round(row["mean"], 3),
                        "sd": round(row["sd"], 3),
                        "active_probability": round(row["active_probability"], 4),
                    }
                    for row in lineups[rid][w]
                ]
                for w in all_needed_weeks
            }
            for rid in roster_ids
        },
    }


if __name__ == "__main__":
    count = int(os.getenv("FSFFL_SIMULATIONS", "3000"))
    if count < 100:
        raise SystemExit("FSFFL_SIMULATIONS must be at least 100.")

    core.DEFAULT_SIMS = count
    core.run_simulation = fast_run_simulation
    core.main()
