#!/usr/bin/env python3
"""Phase-level runtime profiler for the optimized V2 publication."""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any, Callable, Dict, List

import alternate_history_runtime_optimizations as runtime_opt

runtime_opt.install()

import run_fsffl_alternate_history_magazine as publication
import run_fsffl_alternate_history_magazine_v7 as v7
import run_fsffl_generic_alternate_history as generic
import run_fsffl_gm30_counterfactual as cf

TIMINGS: List[Dict[str, Any]] = []
SIM_INTERNAL: List[Dict[str, Any]] = []


def _wrap(owner: Any, name: str, label: str) -> None:
    original: Callable[..., Any] = getattr(owner, name)

    def timed(*args, **kwargs):
        started = time.perf_counter()
        try:
            return original(*args, **kwargs)
        finally:
            elapsed = time.perf_counter() - started
            season = kwargs.get("season") or kwargs.get("draft_season") or kwargs.get("completed_season")
            TIMINGS.append({
                "phase": label,
                "season": str(season) if season is not None else None,
                "seconds": round(elapsed, 4),
            })
            print(f"AH_PROFILE phase={label} season={season or '-'} seconds={elapsed:.4f}")

    setattr(owner, name, timed)


def _wrap_simulator_run() -> None:
    original = cf.CounterfactualEngine._run

    def timed_run(self, rosters, n_sims):
        started = time.perf_counter()
        result = original(self, rosters, n_sims)
        elapsed = time.perf_counter() - started
        runtime = dict(result.get("runtime") or {})
        row = {
            "n_sims": int(n_sims),
            "wall_seconds": round(elapsed, 4),
            "lineup_build_seconds": float(runtime.get("lineup_build_seconds") or 0.0),
            "score_generation_seconds": float(runtime.get("score_generation_seconds") or 0.0),
            "simulator_total_seconds": float(runtime.get("total_seconds") or 0.0),
        }
        SIM_INTERNAL.append(row)
        print("AH_SIM_INTERNAL=" + json.dumps(row, sort_keys=True))
        return result

    cf.CounterfactualEngine._run = timed_run


def install() -> None:
    _wrap(generic.predraft, "anchored_boundary_simulate", "fork_boundary")
    _wrap(generic.cycle, "replay_predraft_offseason", "predraft_offseason")
    _wrap(generic, "replay_rookie_draft_groups", "rookie_draft")
    _wrap(generic.cycle, "propagate_completed_season", "completed_season")
    _wrap(generic.cycle, "replay_active_season_to_now", "active_season_to_now")
    _wrap(generic.roster_compliance, "enforce_current_season_roster_rules", "roster_compliance")
    _wrap(publication, "_league_simulator", "simulator")
    _wrap_simulator_run()


def main() -> None:
    parser = argparse.ArgumentParser(description="Profile optimized FSFFL Alternate History V2")
    parser.add_argument("scenario", type=Path)
    parser.add_argument("--particles", type=int, default=20)
    parser.add_argument("--sims", type=int, default=20)
    parser.add_argument("--seed", type=int, default=20260824)
    args = parser.parse_args()

    install()
    started = time.perf_counter()
    v7.run(args.scenario, particles=args.particles, n_sims=args.sims, seed=args.seed)
    total = time.perf_counter() - started
    ranked = sorted(TIMINGS, key=lambda row: -float(row["seconds"]))
    summary = {
        "particles": args.particles,
        "simulator_sims": args.sims,
        "total_seconds": round(total, 4),
        "timings": TIMINGS,
        "ranked_hotspots": ranked,
        "cache_stats": runtime_opt.stats(),
        "simulator_internal_runs": SIM_INTERNAL,
        "simulator_internal_totals": {
            "invocations": len(SIM_INTERNAL),
            "lineup_build_seconds": round(sum(x["lineup_build_seconds"] for x in SIM_INTERNAL), 4),
            "score_generation_seconds": round(sum(x["score_generation_seconds"] for x in SIM_INTERNAL), 4),
            "simulator_total_seconds": round(sum(x["simulator_total_seconds"] for x in SIM_INTERNAL), 4),
            "wall_seconds": round(sum(x["wall_seconds"] for x in SIM_INTERNAL), 4),
        },
    }
    print("AH_PROFILE_SUMMARY=" + json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
