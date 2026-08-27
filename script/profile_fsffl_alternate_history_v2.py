#!/usr/bin/env python3
"""Phase-level runtime profiler for the V2 Alternate History publication.

This wrapper does not alter model behavior. It times the existing validated
functions in-process, then runs the normal V6 reader publication. The emitted
JSON is intended to identify optimization targets before any fidelity change.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any, Callable, Dict, List

import run_fsffl_alternate_history_magazine as publication
import run_fsffl_alternate_history_magazine_v6 as v6
import run_fsffl_generic_alternate_history as generic

TIMINGS: List[Dict[str, Any]] = []


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


def install() -> None:
    _wrap(generic.predraft, "anchored_boundary_simulate", "fork_boundary")
    _wrap(generic.cycle, "replay_predraft_offseason", "predraft_offseason")
    # generic imported this function directly, so patch the module binding.
    _wrap(generic, "replay_rookie_draft_groups", "rookie_draft")
    _wrap(generic.cycle, "propagate_completed_season", "completed_season")
    _wrap(generic.cycle, "replay_active_season_to_now", "active_season_to_now")
    _wrap(generic.roster_compliance, "enforce_current_season_roster_rules", "roster_compliance")
    _wrap(publication, "_league_simulator", "simulator")


def main() -> None:
    parser = argparse.ArgumentParser(description="Profile FSFFL Alternate History V2 without changing fidelity")
    parser.add_argument("scenario", type=Path)
    parser.add_argument("--particles", type=int, default=20)
    parser.add_argument("--sims", type=int, default=20)
    parser.add_argument("--seed", type=int, default=20260824)
    args = parser.parse_args()

    install()
    started = time.perf_counter()
    v6.run(args.scenario, particles=args.particles, n_sims=args.sims, seed=args.seed)
    total = time.perf_counter() - started
    ranked = sorted(TIMINGS, key=lambda row: -float(row["seconds"]))
    summary = {
        "particles": args.particles,
        "simulator_sims": args.sims,
        "total_seconds": round(total, 4),
        "timings": TIMINGS,
        "ranked_hotspots": ranked,
    }
    print("AH_PROFILE_SUMMARY=" + json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
