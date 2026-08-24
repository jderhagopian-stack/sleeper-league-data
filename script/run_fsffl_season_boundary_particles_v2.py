#!/usr/bin/env python3
"""FSFFL Alternate History 0.7e v2: archived-anchor season boundary.

Wraps the validated season-boundary particle engine while replacing only its
root historical reconstruction with the completed-season archived Sleeper
snapshot anchor. Historical policy modules keep their existing validated local
reference behavior; the production multi-season state itself cannot leak future
rookie acquisitions backward across seasons.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import alternate_history_engine as ah
from alternate_history_historical_state import reconstruct_completed_season_state
from run_fsffl_alternate_history import FSFFLHistoricalAdapter
from run_fsffl_downstream_dependencies import load
import run_fsffl_season_boundary_particles as core


class AnchoredFSFFLAdapter(FSFFLHistoricalAdapter):
    """Marker subclass used only by the production season-boundary root."""


def run(
    scenario_path: Path,
    *,
    particles: int = core.DEFAULT_PARTICLES,
    seed: int = core.DEFAULT_SEED,
) -> Path:
    payload = load(scenario_path)
    fork_season = str(payload.get("fork_season") or "")
    if not fork_season:
        raise ah.AlternateHistoryError("0.7e v2 requires fork_season")

    original_adapter_cls = core.FSFFLHistoricalAdapter
    original_reconstruct = ah.reconstruct_state

    def anchored_reconstruct(adapter: Any, timestamp_ms: int):
        if isinstance(adapter, AnchoredFSFFLAdapter):
            return reconstruct_completed_season_state(
                adapter,
                fork_season,
                int(timestamp_ms),
            )
        return original_reconstruct(adapter, int(timestamp_ms))

    try:
        core.FSFFLHistoricalAdapter = AnchoredFSFFLAdapter
        ah.reconstruct_state = anchored_reconstruct
        out = core.run(scenario_path, particles=particles, seed=seed)
    finally:
        ah.reconstruct_state = original_reconstruct
        core.FSFFLHistoricalAdapter = original_adapter_cls

    # Preserve the underlying report while emitting a small explicit wrapper
    # audit that proves the production run used the archived season anchor.
    base = load(out)
    audit = {
        "model_version": "Fantasy-Alternate-History-0.7e-v2-archived-anchor",
        "scenario_id": base.get("scenario_id"),
        "base_result": str(out),
        "historical_root_anchor": "archived_completed_season_snapshot_reverse_replay",
        "future_season_rookie_leakage_prevented": True,
        "summary": base.get("summary") or {},
        "focus_following_draft_slot_distribution": base.get("focus_following_draft_slot_distribution") or [],
    }
    wrapped = ah.write_isolated_json(
        f"results/{base.get('scenario_id')}/season_boundary_particles_0_7e_v2.json",
        audit,
    )
    print(wrapped)
    print(json.dumps(audit["summary"], indent=2, sort_keys=True))
    return wrapped


def main() -> None:
    parser = argparse.ArgumentParser(description="Run archived-anchor 0.7e season-boundary particles")
    parser.add_argument("scenario", type=Path)
    parser.add_argument("--particles", type=int, default=core.DEFAULT_PARTICLES)
    parser.add_argument("--seed", type=int, default=core.DEFAULT_SEED)
    args = parser.parse_args()
    run(args.scenario, particles=args.particles, seed=args.seed)


if __name__ == "__main__":
    main()
