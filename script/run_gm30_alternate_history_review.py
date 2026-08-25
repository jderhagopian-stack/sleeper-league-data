#!/usr/bin/env python3
"""Explicit GM 3.0 -> Alternate History review command surface.

This file is intentionally NOT imported by normal GM 3.0 execution.
It exists only for user-requested historical reviews / what-if questions.

Normal GM 3.0 remains unchanged. Alternate History writes only to its isolated
`data/alternate_history/results/...` namespace and may consume Simulator 1.0 at
the present-day boundary through the already-validated final-report layer.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import alternate_history_performance_runtime as perf
import alternate_history_weekly_cow_runtime as weekly_cow
import alternate_history_ledger_key_runtime as ledger_key
import alternate_history_simulator_dp_runtime as simulator_dp
import alternate_history_trade_persistence_runtime as trade_persistence

DEFAULT_PARTICLES = 100
DEFAULT_SIMS = 500
DEFAULT_SEED = 20260824


def run_review(
    scenario: Path,
    *,
    particles: int = DEFAULT_PARTICLES,
    sims: int = DEFAULT_SIMS,
    seed: int = DEFAULT_SEED,
):
    if particles <= 0:
        raise SystemExit("historical particles must be positive")
    if sims <= 0:
        raise SystemExit("Simulator draws must be positive")
    if not scenario.exists():
        raise SystemExit(f"Alternate History scenario not found: {scenario}")

    # Performance patches are accuracy-neutral and already equivalence-tested.
    # Trade persistence is a separately regression-gated counterfactual behavior
    # improvement: it only repairs historical trades whose exact terms became
    # illegal while comparable timestamp-safe branch-owned capital still exists.
    perf.install()
    weekly_cow.install()
    ledger_key.install()
    simulator_dp.install()
    trade_persistence.install()

    # Import after the opt-in runtimes are installed. The narrative report
    # consumes the same branch groups and Simulator boundary as the final model.
    import run_fsffl_alternate_history_report_v2 as final_report

    return final_report.run(
        scenario,
        particles=int(particles),
        n_sims=int(sims),
        seed=int(seed),
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Explicitly run an Alternate History retrospective/what-if review. "
            "This command is separate from normal GM 3.0 execution."
        )
    )
    parser.add_argument("scenario", type=Path)
    parser.add_argument("--particles", type=int, default=DEFAULT_PARTICLES)
    parser.add_argument("--sims", type=int, default=DEFAULT_SIMS)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args()
    run_review(
        args.scenario,
        particles=args.particles,
        sims=args.sims,
        seed=args.seed,
    )


if __name__ == "__main__":
    main()
