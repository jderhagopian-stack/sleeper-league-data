#!/usr/bin/env python3
"""FSFFL Alternate History 0.7d performance wrapper.

Preserves the exact v3 season-feedback model while removing a major copying
hotspot. Historical transaction transitions never mutate the season ledger, so
branch children may share the existing ledger object until a scoring/finalize
operation performs the existing deep copy immediately before mutation.

This is copy-on-write, not approximation: fantasy state, probabilities, lineup
selection, exact Max PF, standings, playoff fields, and draft-order outcomes are
unchanged.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Dict

import run_fsffl_multiseason_branch_replay as branch_v1
import run_fsffl_multiseason_particle_replay_v3 as v3


LEDGER_KEY = v3.LEDGER_KEY
DEFAULT_PARTICLES = v3.DEFAULT_PARTICLES
DEFAULT_SEED = v3.DEFAULT_SEED


def apply_preserving_ledger_copy_on_write(
    state_payload: Dict[str, Any],
    event: Dict[str, Any],
    outcome: Dict[str, Any],
) -> Dict[str, Any]:
    """Apply a transaction without eagerly copying immutable season history.

    ``branch_v1.apply_outcome`` reconstructs and serializes only core fantasy
    state and does not mutate ``state_payload`` or its Alternate History ledger.
    v3's scoring and regular-season finalization paths deep-copy the ledger
    before every mutation. Therefore sibling transaction branches can safely
    share this reference between scoring boundaries.
    """
    ledger = state_payload.get(LEDGER_KEY) or {}
    new_state = branch_v1.apply_outcome(state_payload, event, outcome)
    new_state[LEDGER_KEY] = ledger
    return new_state


def run(
    scenario_path: Path,
    *,
    particles: int = DEFAULT_PARTICLES,
    seed: int = DEFAULT_SEED,
) -> Path:
    original = v3.apply_preserving_ledger
    v3.apply_preserving_ledger = apply_preserving_ledger_copy_on_write
    try:
        return v3.run(scenario_path, particles=particles, seed=seed)
    finally:
        v3.apply_preserving_ledger = original


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run optimized exact 0.7d particle season-feedback replay"
    )
    parser.add_argument("scenario", type=Path)
    parser.add_argument("--particles", type=int, default=DEFAULT_PARTICLES)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args()
    run(args.scenario, particles=args.particles, seed=args.seed)


if __name__ == "__main__":
    main()
