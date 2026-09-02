#!/usr/bin/env python3
"""Shadow comparison of exact versus legacy greedy Simulator backup assignment."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SCRIPT = ROOT / "script"
OUT = DATA / "audit" / "simulator_backup_assignment_shadow.json"
SIMULATIONS = 3000

LEGACY_SLOT_SCARCITY = {
    "QB": 0,
    "TE": 1,
    "RB": 2,
    "WR": 2,
    "SUPER_FLEX": 3,
    "FLEX": 4,
}


def loadmod(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sim = loadmod(
    SCRIPT / "run_fsffl_season_simulator_preproduction.py",
    "backup_shadow_sim",
)


def legacy_simulate_team_week(
    roster,
    week,
    lineup,
    backups,
    n_sims,
    rng,
    team_shocks,
    adjustments,
):
    """Exact copy of the superseded greedy simulation-time allocation."""
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
        p, a = sim.generate_player_draws(
            row, week, n_sims, rng, team_shocks, adjustments
        )
        points[pid] = p
        available[pid] = a

    used = {pid: np.zeros(n_sims, dtype=bool) for pid in all_rows}
    total = np.zeros(n_sims, dtype=np.float32)
    slot_order = sorted(
        range(len(lineup)),
        key=lambda i: LEGACY_SLOT_SCARCITY.get(lineup[i]["slot"], 5),
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


def load_inputs():
    league = sim.core.load_json(DATA / "league.json")
    rosters = sim.core.load_json(DATA / "rosters.json", [])
    users = sim.core.load_json(DATA / "users.json", [])
    players = sim.core.load_json(DATA / "players.json", {})
    season = str(league.get("season"))
    schedule = sim.core.load_json(
        DATA / "stats" / "fsffl" / season / "league_matchups_raw.json",
        {},
    )
    projections = sim.core.load_json(
        DATA / "simulator" / season / "inputs" / "player_weekly_projections.json",
        {},
    )
    return league, rosters, users, players, schedule, projections


def team_index(result):
    return {
        str(x.get("user_id")): x
        for x in (result.get("teams") or [])
    }


def main():
    league, rosters, users, players, schedule, projections = load_inputs()
    seed = sim.deterministic_seed(league, str(league.get("season")))

    exact = sim.run_preproduction_simulation(
        league,
        rosters,
        users,
        players,
        schedule,
        projections,
        n_sims=SIMULATIONS,
        seed=seed,
    )

    current_impl = sim.simulate_team_week
    try:
        sim.simulate_team_week = legacy_simulate_team_week
        legacy = sim.run_preproduction_simulation(
            league,
            rosters,
            users,
            players,
            schedule,
            projections,
            n_sims=SIMULATIONS,
            seed=seed,
        )
    finally:
        sim.simulate_team_week = current_impl

    eidx = team_index(exact)
    lidx = team_index(legacy)
    metrics = [
        "expected_points_for",
        "expected_wins",
        "playoff_probability",
        "bye_probability",
        "championship_probability",
    ]
    rows = []
    for uid in sorted(set(eidx) | set(lidx)):
        e = eidx.get(uid) or {}
        l = lidx.get(uid) or {}
        delta = {
            key: round(float(e.get(key) or 0.0) - float(l.get(key) or 0.0), 6)
            for key in metrics
        }
        rows.append(
            {
                "user_id": uid,
                "team_name": e.get("team_name") or l.get("team_name"),
                "exact": {key: e.get(key) for key in metrics},
                "legacy": {key: l.get(key) for key in metrics},
                "exact_minus_legacy": delta,
            }
        )

    max_abs = {
        key: max(
            (abs(float(x["exact_minus_legacy"][key])) for x in rows),
            default=0.0,
        )
        for key in metrics
    }
    exact_runtime = float((exact.get("runtime") or {}).get("total_seconds") or 0.0)
    legacy_runtime = float((legacy.get("runtime") or {}).get("total_seconds") or 0.0)
    runtime_ratio = (
        exact_runtime / legacy_runtime
        if legacy_runtime > 0
        else None
    )
    changed = [
        x
        for x in rows
        if any(abs(float(v)) > 1e-9 for v in x["exact_minus_legacy"].values())
    ]

    report = {
        "model_version": "FSFFL-Simulator-Backup-Assignment-Shadow-1.0",
        "authority": "SHADOW_VALIDATION_NON_AUTHORITATIVE",
        "production_outputs_mutated": False,
        "simulations": SIMULATIONS,
        "seed": seed,
        "common_random_numbers": True,
        "comparison": "exact rule-derived/cached assignment minus legacy greedy SLOT_SCARCITY assignment",
        "summary": {
            "team_count": len(rows),
            "teams_with_any_output_change": len(changed),
            "max_absolute_delta": max_abs,
            "structural_counterexample_exists_independently_of_current_league_impact": True,
            "new_economic_coefficient_introduced": False,
            "exact_runtime_seconds": round(exact_runtime, 4),
            "legacy_runtime_seconds": round(legacy_runtime, 4),
            "exact_to_legacy_runtime_ratio": (
                round(runtime_ratio, 4) if runtime_ratio is not None else None
            ),
        },
        "teams": rows,
        "interpretation": {
            "zero_current_league_delta_would_not_make_legacy_algorithm_correct": True,
            "nonzero_delta_is_expected_consequence_of_correct_legal_assignment": True,
            "points_to_wins_chain_changes_only_through_corrected_team_scores": True,
            "no_downstream_compensating_coefficient_should_be_added": True,
        },
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], indent=2))


if __name__ == "__main__":
    main()
