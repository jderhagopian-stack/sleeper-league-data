#!/usr/bin/env python3
"""Regression tests for exact simulation-time backup assignment."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
SIM = ROOT / "script" / "run_fsffl_season_simulator_preproduction.py"


def load():
    spec = importlib.util.spec_from_file_location("exact_backup_sim", SIM)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load Simulator")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sim = load()


def row(pid, slot, pos, mean, active_probability):
    return {
        "slot": slot,
        "player_id": pid,
        "name": pid,
        "position": pos,
        "mean": float(mean),
        "median": float(mean),
        "sd": 0.01,
        "active_probability": float(active_probability),
        "nfl_team": None,
        "value": float(mean) * float(active_probability),
    }


def bench(pid, pos, mean):
    return {
        "player_id": pid,
        "name": pid,
        "position": pos,
        "mean": float(mean),
        "median": float(mean),
        "sd": 0.01,
        "active_probability": 1.0,
        "nfl_team": None,
        "value": float(mean),
    }


def simulate(lineup, backups, seed=12345):
    rng = np.random.default_rng(seed)
    return sim.simulate_team_week(
        roster={},
        week=1,
        lineup=lineup,
        backups=backups,
        n_sims=4000,
        rng=rng,
        team_shocks={},
        adjustments={},
    )


def test_fsffl_flex_before_superflex_is_exact():
    lineup = [
        row("SF_START", "SUPER_FLEX", "QB", 1.0, 0.0),
        row("F_START", "FLEX", "RB", 1.0, 0.0),
    ]
    qb = bench("QB_BACKUP", "QB", 19.0)
    rb = bench("RB_BACKUP", "RB", 20.0)
    # Each chain is independently value-ranked, reproducing the production
    # collision that previously let SUPER_FLEX consume the RB first.
    backups = {
        0: [rb, qb],
        1: [rb],
    }
    scores = simulate(lineup, backups)
    mean = float(np.mean(scores))
    if abs(mean - 39.0) > 0.08:
        raise AssertionError(
            f"FSFFL exact backup assignment expected ~39 points, got {mean}"
        )


def test_nonlaminar_flex_family_uses_exact_fallback():
    lineup = [
        row("A_START", "WRRB_FLEX", "RB", 1.0, 0.0),
        row("B_START", "REC_FLEX", "TE", 1.0, 0.0),
    ]
    wr = bench("WR_BACKUP", "WR", 20.0)
    rb = bench("RB_BACKUP", "RB", 19.0)
    te = bench("TE_BACKUP", "TE", 1.0)
    backups = {
        0: [wr, rb],
        1: [wr, te],
    }
    if sim.slot_family_is_laminar([x["slot"] for x in lineup]):
        raise AssertionError("overlapping WR/RB and WR/TE flex family should be non-laminar")
    scores = simulate(lineup, backups, seed=54321)
    mean = float(np.mean(scores))
    if abs(mean - 39.0) > 0.08:
        raise AssertionError(
            f"non-laminar exact backup assignment expected ~39 points, got {mean}"
        )


def test_current_fsffl_slot_family_is_laminar():
    slots = ["QB", "RB", "RB", "WR", "WR", "WR", "TE", "FLEX", "SUPER_FLEX"]
    if not sim.slot_family_is_laminar(slots):
        raise AssertionError("current FSFFL slot family should be laminar")
    order = sim.constrained_slot_order(
        [{"slot": slot} for slot in slots]
    )
    ordered = [slots[i] for i in order]
    if ordered.index("FLEX") > ordered.index("SUPER_FLEX"):
        raise AssertionError("FLEX must be assigned before SUPER_FLEX")


def test_arbitrary_slot_scarcity_map_removed():
    if hasattr(sim, "SLOT_SCARCITY"):
        raise AssertionError("hand-set SLOT_SCARCITY map still has runtime authority")


def main():
    tests = [
        test_fsffl_flex_before_superflex_is_exact,
        test_nonlaminar_flex_family_uses_exact_fallback,
        test_current_fsffl_slot_family_is_laminar,
        test_arbitrary_slot_scarcity_map_removed,
    ]
    for test in tests:
        test()
    print(
        {
            "passed": True,
            "test_count": len(tests),
            "production_behavior_change": "exact_backup_assignment_only",
            "new_economic_coefficient_introduced": False,
        }
    )


if __name__ == "__main__":
    main()
