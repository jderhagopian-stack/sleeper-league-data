#!/usr/bin/env python3
"""
FSFFL Season Simulator - pre-production vectorized engine.

Adds:
- fast exact lineup construction for FSFFL's roster format
- simulation-time bench substitution for random unavailability
- same-NFL-team offensive correlation
- optional opponent adjustment hook (neutral if source absent)
- season/config-derived deterministic RNG seed
- vectorized 3k/50k Monte Carlo
- runtime and probability-sum validation
- same output files expected by Simulator 1.0

This intentionally preserves the existing core validation and output schema.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

import build_fsffl_season_simulator as core

DATA = Path("data")
SIM_ROOT = DATA / "simulator"
MODEL_VERSION = core.MODEL_VERSION + "-preproduction"

# Mild shared offensive environment correlation. This is deliberately
# conservative until we calibrate richer QB/receiver/opponent relationships.
TEAM_SHOCK_RHO = {
    "QB": 0.16,
    "WR": 0.13,
    "TE": 0.13,
    "RB": 0.08,
}

SLOT_SCARCITY = {
    "QB": 0,
    "TE": 1,
    "RB": 2,
    "WR": 2,
    "SUPER_FLEX": 3,
    "FLEX": 4,
}

# Compatibility facade for Shared Core consumers such as Decision Lab.
# These aliases expose canonical helper contracts without restoring legacy
# simulation authority to build_fsffl_season_simulator.py.
player_meta = core.player_meta
projection_for = core.projection_for
lineup_slots = core.lineup_slots
eligible = core.eligible
regular_season_weeks = core.regular_season_weeks
roster_directory = core.roster_directory
build_schedule = core.build_schedule
validate_inputs = core.validate_inputs


def optimize_weekly_lineup(roster, week, league, players, projections):
    return optimize_fsffl_fast(roster, week, league, players, projections)



def as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def deterministic_seed(league: Dict[str, Any], season: str) -> int:
    material = f"{league.get('league_id','')}|{season}|FSFFL-Season-Simulator-1.0"
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
    return int(digest[:8], 16)


def player_team(players: Dict[str, Any], pid: str) -> Optional[str]:
    p = (players or {}).get(str(pid)) or {}
    team = p.get("team") or p.get("team_abbr")
    if not team:
        return None
    return str(team).upper()


def active_roster_players(roster: Dict[str, Any]) -> List[str]:
    taxi = {str(x) for x in (roster.get("taxi") or [])}
    reserve = {str(x) for x in (roster.get("reserve") or [])}
    # IR/reserve should not be candidates for normal lineup selection.
    return [
        str(pid)
        for pid in (roster.get("players") or [])
        if str(pid) not in taxi and str(pid) not in reserve
    ]


def candidate_rows(
    roster: Dict[str, Any],
    week: int,
    players: Dict[str, Any],
    projections: Dict[str, Any],
) -> List[Dict[str, Any]]:
    rows = []
    for pid in active_roster_players(roster):
        meta = core.player_meta(players, projections, pid)
        pos = meta.get("position")
        pr = core.projection_for(projections, pid, week)
        if not pos or pr is None:
            continue
        rows.append(
            {
                **meta,
                **pr,
                "nfl_team": player_team(players, pid),
                "value": float(pr["mean"]) * float(pr["active_probability"]),
            }
        )
    return rows


def standard_fsffl_slot_counts(slots: List[str]) -> bool:
    return Counter(slots) == Counter(
        ["QB", "RB", "RB", "WR", "WR", "WR", "TE", "FLEX", "SUPER_FLEX"]
    )


def best_fixed_fill(
    candidates: List[Dict[str, Any]],
    excluded: set,
    pos: str,
    count: int,
) -> Optional[List[Dict[str, Any]]]:
    pool = [
        c for c in candidates
        if c["player_id"] not in excluded and c["position"] == pos
    ]
    pool.sort(key=lambda c: c["value"], reverse=True)
    if len(pool) < count:
        return None
    return pool[:count]


def optimize_fsffl_fast(
    roster: Dict[str, Any],
    week: int,
    league: Dict[str, Any],
    players: Dict[str, Any],
    projections: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """
    Exact optimizer for the current FSFFL slot structure.

    Enumerates only SUPER_FLEX and FLEX assignments, then fills QB/RB/WR/TE
    with the best remaining positional players. This is exact for:
    QB, 2 RB, 3 WR, TE, FLEX, SUPER_FLEX.
    """
    slots = core.lineup_slots(league)
    if not standard_fsffl_slot_counts(slots):
        return core.optimize_weekly_lineup(roster, week, league, players, projections)

    candidates = candidate_rows(roster, week, players, projections)
    if not candidates:
        return core.optimize_weekly_lineup(roster, week, league, players, projections)

    sf_pool = [c for c in candidates if c["position"] in {"QB", "RB", "WR", "TE"}]
    flex_pool = [c for c in candidates if c["position"] in {"RB", "WR", "TE"}]

    best_total = -1e18
    best = None

    # None is allowed for pathological roster shortages.
    sf_options = [None] + sf_pool
    flex_options = [None] + flex_pool

    for sf in sf_options:
        sf_id = sf["player_id"] if sf else None
        for fl in flex_options:
            fl_id = fl["player_id"] if fl else None
            if sf_id is not None and sf_id == fl_id:
                continue

            used = {x for x in (sf_id, fl_id) if x is not None}

            qb = best_fixed_fill(candidates, used, "QB", 1)
            if qb is None:
                continue
            used_qb = used | {x["player_id"] for x in qb}

            rb = best_fixed_fill(candidates, used_qb, "RB", 2)
            if rb is None:
                continue
            used_rb = used_qb | {x["player_id"] for x in rb}

            wr = best_fixed_fill(candidates, used_rb, "WR", 3)
            if wr is None:
                continue
            used_wr = used_rb | {x["player_id"] for x in wr}

            te = best_fixed_fill(candidates, used_wr, "TE", 1)
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
        return core.optimize_weekly_lineup(roster, week, league, players, projections)

    # Rebuild in league slot order.
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


def build_backup_chains(
    roster: Dict[str, Any],
    week: int,
    lineup: List[Dict[str, Any]],
    players: Dict[str, Any],
    projections: Dict[str, Any],
) -> Dict[int, List[Dict[str, Any]]]:
    """
    For each lineup slot, create a projected-value-ranked fallback chain.
    The simulator will use these only when the projected starter is unavailable.
    """
    candidates = candidate_rows(roster, week, players, projections)
    starter_ids = {
        row["player_id"] for row in lineup if row.get("player_id") is not None
    }

    chains = {}
    for slot_index, starter in enumerate(lineup):
        slot = starter["slot"]
        chain = [
            c for c in candidates
            if c["player_id"] not in starter_ids
            and core.eligible(c["position"], slot)
        ]
        chain.sort(key=lambda c: c["value"], reverse=True)
        chains[slot_index] = chain
    return chains


def load_opponent_adjustments(season: str) -> Tuple[Dict[str, Any], str]:
    """
    Optional hook. Expected shape:
    {
      "weeks": {
        "1": {
          "BUF": {"QB": 1.03, "RB": 0.97, "WR": 1.02, "TE": 1.00}
        }
      }
    }

    Until a dedicated NFL matchup source is connected, absence is neutral 1.0.
    """
    path = SIM_ROOT / season / "sources" / "opponent_adjustments.json"
    if not path.exists():
        return {}, "neutral_no_source"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload or {}, "file"
    except Exception:
        return {}, "neutral_invalid_source"


def matchup_multiplier(
    adjustments: Dict[str, Any],
    week: int,
    nfl_team: Optional[str],
    position: Optional[str],
) -> float:
    if not nfl_team or not position:
        return 1.0
    row = (
        ((adjustments.get("weeks") or {}).get(str(week)) or {})
        .get(str(nfl_team).upper(), {})
    )
    return max(0.80, min(1.20, as_float(row.get(position), 1.0)))


def generate_player_draws(
    row: Dict[str, Any],
    week: int,
    n_sims: int,
    rng: np.random.Generator,
    team_shocks: Dict[Tuple[int, str], np.ndarray],
    adjustments: Dict[str, Any],
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Returns (points, available).
    """
    if row.get("player_id") is None:
        return (
            np.zeros(n_sims, dtype=np.float32),
            np.zeros(n_sims, dtype=bool),
        )

    pos = row.get("position")
    team = row.get("nfl_team")
    mean = float(row["mean"]) * matchup_multiplier(adjustments, week, team, pos)
    sd = max(0.1, float(row["sd"]))
    active_p = max(0.0, min(1.0, float(row["active_probability"])))

    available = rng.random(n_sims) <= active_p

    rho = TEAM_SHOCK_RHO.get(str(pos), 0.0)
    if team and rho > 0:
        key = (week, team)
        if key not in team_shocks:
            team_shocks[key] = rng.standard_normal(n_sims).astype(np.float32)
        shared = team_shocks[key]
        independent = rng.standard_normal(n_sims).astype(np.float32)
        z = rho * shared + np.sqrt(1.0 - rho * rho) * independent
    else:
        z = rng.standard_normal(n_sims).astype(np.float32)

    points = (mean + sd * z).astype(np.float32, copy=False)
    np.maximum(points, 0.0, out=points)
    points *= available
    return points, available


def simulate_team_week(
    roster: Dict[str, Any],
    week: int,
    lineup: List[Dict[str, Any]],
    backups: Dict[int, List[Dict[str, Any]]],
    n_sims: int,
    rng: np.random.Generator,
    team_shocks: Dict[Tuple[int, str], np.ndarray],
    adjustments: Dict[str, Any],
) -> np.ndarray:
    """
    Vectorized simulation-time substitution:
    - use projected starter when available
    - if unavailable, use highest-ranked eligible bench player who is available
      and not already used elsewhere in that simulation.
    """
    all_rows = {}
    for row in lineup:
        if row.get("player_id") is not None:
            all_rows[row["player_id"]] = row
    for chain in backups.values():
        for row in chain:
            all_rows[row["player_id"]] = row

    points = {}
    available = {}
    for pid, row in all_rows.items():
        p, a = generate_player_draws(
            row, week, n_sims, rng, team_shocks, adjustments
        )
        points[pid] = p
        available[pid] = a

    used = {pid: np.zeros(n_sims, dtype=bool) for pid in all_rows}
    total = np.zeros(n_sims, dtype=np.float32)

    # Scarce slots first reduces bad fallback collisions.
    slot_order = sorted(
        range(len(lineup)),
        key=lambda i: SLOT_SCARCITY.get(lineup[i]["slot"], 5),
    )

    for i in slot_order:
        starter = lineup[i]
        chain = []
        if starter.get("player_id") is not None:
            chain.append(starter)
        chain.extend(backups.get(i, []))

        filled = np.zeros(n_sims, dtype=bool)

        for cand in chain:
            pid = cand["player_id"]
            can_use = (~filled) & available[pid] & (~used[pid])
            if not np.any(can_use):
                continue
            total[can_use] += points[pid][can_use]
            used[pid][can_use] = True
            filled[can_use] = True
            if np.all(filled):
                break

    return total


def fast_seed_orders(
    wins: np.ndarray,
    pf: np.ndarray,
    roster_ids: List[int],
) -> np.ndarray:
    n_sims, n_teams = wins.shape
    rid_array = np.asarray(roster_ids, dtype=np.int32)
    orders = np.empty((n_sims, n_teams), dtype=np.int16)
    # Only 12 teams per row; this loop is tiny compared with player simulation.
    for s in range(n_sims):
        orders[s] = np.lexsort((rid_array, -pf[s], -wins[s]))
    return orders


def run_preproduction_simulation(
    league,
    rosters,
    users,
    players,
    raw_schedule,
    projections,
    n_sims=50000,
    seed=None,
    lineups_override=None,
):
    started = time.perf_counter()
    season = str(league["season"])
    if seed is None:
        seed = deterministic_seed(league, season)

    roster_dir = core.roster_directory(rosters, users)
    reg_weeks = core.regular_season_weeks(league)
    by_week, _ = core.build_schedule(raw_schedule, reg_weeks)

    playoff_start = int((league.get("settings") or {}).get("playoff_week_start") or 15)
    playoff_weeks = [playoff_start, playoff_start + 1, playoff_start + 2]
    all_weeks = sorted(set(reg_weeks + playoff_weeks))

    roster_ids = sorted(roster_dir)
    rid_to_idx = {rid: i for i, rid in enumerate(roster_ids)}
    n_teams = len(roster_ids)

    adjustments, adjustment_source = load_opponent_adjustments(season)

    t0 = time.perf_counter()
    lineups = defaultdict(dict)
    backups = defaultdict(dict)
    supplied = lineups_override or {}
    for rid, roster in roster_dir.items():
        for week in all_weeks:
            lineup = (
                (supplied.get(rid) or {}).get(week)
                or (supplied.get(str(rid)) or {}).get(str(week))
                or optimize_fsffl_fast(roster, week, league, players, projections)
            )
            lineups[rid][week] = lineup
            backups[rid][week] = build_backup_chains(
                roster, week, lineup, players, projections
            )
    lineup_seconds = time.perf_counter() - t0

    rng = np.random.default_rng(seed)
    week_to_idx = {w: i for i, w in enumerate(all_weeks)}
    scores = np.zeros((n_sims, len(all_weeks), n_teams), dtype=np.float32)

    t1 = time.perf_counter()
    team_shocks = {}
    for week in all_weeks:
        wi = week_to_idx[week]
        for rid in roster_ids:
            scores[:, wi, rid_to_idx[rid]] = simulate_team_week(
                roster_dir[rid],
                week,
                lineups[rid][week],
                backups[rid][week],
                n_sims,
                rng,
                team_shocks,
                adjustments,
            )
    scoring_seconds = time.perf_counter() - t1

    reg_idx = [week_to_idx[w] for w in reg_weeks]
    pf = scores[:, reg_idx, :].sum(axis=1, dtype=np.float64)
    wins = np.zeros((n_sims, n_teams), dtype=np.int16)

    for week in reg_weeks:
        wi = week_to_idx[week]
        for a, b in by_week.get(week, []):
            ai, bi = rid_to_idx[a], rid_to_idx[b]
            aw = scores[:, wi, ai] >= scores[:, wi, bi]
            wins[:, ai] += aw
            wins[:, bi] += ~aw

    orders = fast_seed_orders(wins, pf, roster_ids)

    playoff_teams = int((league.get("settings") or {}).get("playoff_teams") or 6)
    seed_counts = np.zeros((n_teams, n_teams), dtype=np.int64)
    playoff_counts = np.zeros(n_teams, dtype=np.int64)
    bye_counts = np.zeros(n_teams, dtype=np.int64)
    division_counts = np.zeros(n_teams, dtype=np.int64)
    title_counts = np.zeros(n_teams, dtype=np.int64)

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

    rid_array = np.asarray(roster_ids, dtype=np.int32)
    for members in division_members.values():
        m = np.asarray(members, dtype=np.int16)
        for s in range(n_sims):
            local = np.lexsort((rid_array[m], -pf[s, m], -wins[s, m]))
            division_counts[m[local[0]]] += 1

    # Six-team playoff bracket, vectorized.
    if playoff_teams >= 6 and len(playoff_weeks) >= 3:
        sim = np.arange(n_sims)
        top6 = orders[:, :6]
        w1 = week_to_idx[playoff_weeks[0]]
        w2 = week_to_idx[playoff_weeks[1]]
        w3 = week_to_idx[playoff_weeks[2]]

        s1, s2, s3, s4, s5, s6 = [top6[:, j] for j in range(6)]

        g1 = np.where(scores[sim, w1, s3] >= scores[sim, w1, s6], s3, s6)
        g2 = np.where(scores[sim, w1, s4] >= scores[sim, w1, s5], s4, s5)

        seed_num = np.full((n_sims, n_teams), 99, dtype=np.int16)
        for j in range(6):
            seed_num[sim, top6[:, j]] = j + 1

        lower_seed = np.where(
            seed_num[sim, g1] > seed_num[sim, g2], g1, g2
        )
        higher_seed = np.where(lower_seed == g1, g2, g1)

        semi1 = np.where(
            scores[sim, w2, s1] >= scores[sim, w2, lower_seed],
            s1,
            lower_seed,
        )
        semi2 = np.where(
            scores[sim, w2, s2] >= scores[sim, w2, higher_seed],
            s2,
            higher_seed,
        )

        champ = np.where(
            scores[sim, w3, semi1] >= scores[sim, w3, semi2],
            semi1,
            semi2,
        )
        np.add.at(title_counts, champ, 1)

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
                len(reg_weeks) - float(win_totals[i]) / n_sims, 3
            ),
            "expected_points_for": round(float(pf_totals[i]) / n_sims, 2),
            "playoff_probability": round(float(playoff_counts[i]) / n_sims, 5),
            "bye_probability": round(float(bye_counts[i]) / n_sims, 5),
            "division_probability": (
                round(float(division_counts[i]) / n_sims, 5)
                if info.get("division") is not None else None
            ),
            "championship_probability": round(
                float(title_counts[i]) / n_sims, 5
            ),
            "seed_probabilities": {
                str(seed_no): round(
                    float(seed_counts[i, seed_no - 1]) / n_sims, 5
                )
                for seed_no in range(1, n_teams + 1)
            },
        })

    teams.sort(key=lambda x: (-x["expected_wins"], -x["expected_points_for"]))

    total_seconds = time.perf_counter() - started

    probability_checks = {
        "expected_wins_sum": round(
            sum(t["expected_wins"] for t in teams), 4
        ),
        "expected_wins_target": len(reg_weeks) * n_teams / 2,
        "playoff_probability_sum": round(
            sum(t["playoff_probability"] for t in teams), 5
        ),
        "playoff_probability_target": playoff_teams,
        "bye_probability_sum": round(
            sum(t["bye_probability"] for t in teams), 5
        ),
        "bye_probability_target": 2,
        "championship_probability_sum": round(
            sum(t["championship_probability"] for t in teams), 5
        ),
        "championship_probability_target": 1,
    }

    result = {
        "generated_at_utc": core.now_utc(),
        "model_version": core.MODEL_VERSION + "-preproduction",
        "season": season,
        "simulations": n_sims,
        "rng_seed": seed,
        "regular_season_weeks": reg_weeks,
        "playoff_weeks": playoff_weeks,
        "features": {
            "fast_exact_fsffl_lineup_optimizer": True,
            "simulation_time_bench_substitution": True,
            "same_nfl_team_correlation": True,
            "opponent_adjustment_source": adjustment_source,
            "deterministic_season_config_seed": True,
            "external_lineup_override_supported": True,
            "external_lineup_override_used": bool(lineups_override),
        },
        "runtime": {
            "lineup_build_seconds": round(lineup_seconds, 3),
            "score_generation_seconds": round(scoring_seconds, 3),
            "total_seconds": round(total_seconds, 3),
        },
        "probability_checks": probability_checks,
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
                        "active_probability": round(
                            row["active_probability"], 4
                        ),
                    }
                    for row in lineups[rid][w]
                ]
                for w in all_weeks
            }
            for rid in roster_ids
        },
    }
    return result


def write_preproduction_validation(result: Dict[str, Any], season: str):
    out = SIM_ROOT / season / "outputs"
    checks = result["probability_checks"]
    runtime = result["runtime"]

    validations = {
        "generated_at_utc": result["generated_at_utc"],
        "model_version": result["model_version"],
        "season": season,
        "checks": [
            {
                "code": "WIN_SUM",
                "passed": abs(
                    checks["expected_wins_sum"] - checks["expected_wins_target"]
                ) <= 0.05,
                "value": checks["expected_wins_sum"],
                "target": checks["expected_wins_target"],
            },
            {
                "code": "PLAYOFF_PROB_SUM",
                "passed": abs(
                    checks["playoff_probability_sum"]
                    - checks["playoff_probability_target"]
                ) <= 0.01,
                "value": checks["playoff_probability_sum"],
                "target": checks["playoff_probability_target"],
            },
            {
                "code": "BYE_PROB_SUM",
                "passed": abs(
                    checks["bye_probability_sum"] - checks["bye_probability_target"]
                ) <= 0.01,
                "value": checks["bye_probability_sum"],
                "target": checks["bye_probability_target"],
            },
            {
                "code": "TITLE_PROB_SUM",
                "passed": abs(
                    checks["championship_probability_sum"]
                    - checks["championship_probability_target"]
                ) <= 0.01,
                "value": checks["championship_probability_sum"],
                "target": checks["championship_probability_target"],
            },
            {
                "code": "RUNTIME_RECORDED",
                "passed": runtime["total_seconds"] >= 0,
                "value": runtime["total_seconds"],
            },
        ],
    }
    validations["passed"] = all(x["passed"] for x in validations["checks"])

    path = out / "preproduction_validation.json"
    path.write_text(json.dumps(validations, indent=2, sort_keys=True), encoding="utf-8")


def main():
    count = int(os.getenv("FSFFL_SIMULATIONS", "3000"))
    if count < 100:
        raise SystemExit("FSFFL_SIMULATIONS must be at least 100.")

    # Keep existing hard input validation and file-writing logic.
    core.DEFAULT_SIMS = count
    core.run_simulation = run_preproduction_simulation
    core.main()

    league = core.load_json(DATA / "league.json")
    season = str(league["season"])
    result = core.load_json(
        SIM_ROOT / season / "outputs" / "season_simulation.json"
    )
    write_preproduction_validation(result, season)

    print(
        "Pre-production validation: "
        f"{result['simulations']:,} sims in "
        f"{result['runtime']['total_seconds']:.3f}s "
        f"(lineups {result['runtime']['lineup_build_seconds']:.3f}s; "
        f"score generation {result['runtime']['score_generation_seconds']:.3f}s)."
    )


if __name__ == "__main__":
    main()
