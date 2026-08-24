#!/usr/bin/env python3
"""Profile FSFFL Alternate History generic-cycle scaling without changing model logic.

Runs deterministic small-particle regressions across the real generated 2022 fork
and the established 2023 Puka fork, recording wall-clock time, peak RSS, state
counts, and state-compression ratios. This is an audit surface only: it calls the
production generic orchestrator and the same invariant validator used by CI.
"""

from __future__ import annotations

import argparse
import json
import resource
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List

import alternate_history_engine as ah
import run_fsffl_generic_alternate_history as generic
import validate_fsffl_generic_alternate_history as regression

DEFAULT_PARTICLE_SCALES = (2, 4, 8)
DEFAULT_SEED = 20260824


def _peak_rss_mb() -> float:
    # Linux reports ru_maxrss in KiB; macOS reports bytes. CI is Linux.
    value = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    return round(value / 1024.0, 3)


def _phase_metrics(report: Dict[str, Any], particles: int) -> List[Dict[str, Any]]:
    rows = []
    for phase in report.get("phase_audit") or []:
        unique = int(phase.get("unique_states") or 0)
        rows.append({
            "phase": phase.get("phase"),
            "season": phase.get("season"),
            "unique_states": unique,
            "state_to_particle_ratio": round(unique / particles, 6) if particles else None,
            "particle_to_state_compression": round(particles / unique, 6) if unique else None,
            "events_processed": phase.get("events_processed"),
            "picks_simulated": phase.get("picks_simulated"),
        })
    return rows


def _run_one(
    scenario_path: Path,
    *,
    expected_fork: int,
    particles: int,
    seed: int,
) -> Dict[str, Any]:
    rss_before = _peak_rss_mb()
    started = time.perf_counter()
    _, _, report = generic.run_generic(
        scenario_path,
        particles=particles,
        seed=seed,
        return_groups=True,
    )
    elapsed = time.perf_counter() - started
    regression.validate_report(report, expected_fork=expected_fork, particles=particles)
    summary = report.get("summary") or {}
    final_unique = int(summary.get("final_unique_states") or 0)
    peak_rss = _peak_rss_mb()
    return {
        "fork_season": expected_fork,
        "particles": particles,
        "seed": seed,
        "elapsed_seconds": round(elapsed, 4),
        "particles_per_second": round(particles / elapsed, 4) if elapsed else None,
        "peak_rss_mb": peak_rss,
        "peak_rss_delta_mb_lower_bound": round(max(0.0, peak_rss - rss_before), 3),
        "final_unique_states": final_unique,
        "final_state_to_particle_ratio": round(final_unique / particles, 6) if particles else None,
        "phase_metrics": _phase_metrics(report, particles),
        "validated_information_firewall": True,
        "validated_probability_mass": True,
    }


def profile(particle_scales: Iterable[int], *, seed: int = DEFAULT_SEED) -> Path:
    scales = sorted({int(value) for value in particle_scales})
    if not scales or any(value <= 0 for value in scales):
        raise ah.AlternateHistoryError("performance particle scales must be positive")

    scenario_2022 = regression.build_2022_scenario()
    cases = [
        ("generated-2022", scenario_2022, 2022),
        ("puka-2023", regression.PREFERRED, 2023),
    ]
    rows: List[Dict[str, Any]] = []
    for label, scenario, fork in cases:
        for particles in scales:
            result = _run_one(
                scenario,
                expected_fork=fork,
                particles=particles,
                seed=seed,
            )
            result["case"] = label
            rows.append(result)

    by_case: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        by_case.setdefault(str(row["case"]), []).append(row)

    scaling = {}
    for label, case_rows in by_case.items():
        case_rows = sorted(case_rows, key=lambda row: int(row["particles"]))
        baseline = case_rows[0]
        base_particles = float(baseline["particles"])
        base_elapsed = float(baseline["elapsed_seconds"])
        scaling[label] = [
            {
                "particles": row["particles"],
                "elapsed_seconds": row["elapsed_seconds"],
                "particle_multiple": round(float(row["particles"]) / base_particles, 3),
                "runtime_multiple": round(float(row["elapsed_seconds"]) / base_elapsed, 3)
                if base_elapsed else None,
            }
            for row in case_rows
        ]

    payload = {
        "model_version": "Fantasy-Alternate-History-1.0-performance-audit",
        "purpose": "Measure scaling and state growth without changing model decisions or pruning probability mass.",
        "configuration": {"particle_scales": scales, "seed": seed},
        "design_invariants": {
            "production_orchestrator_used_unchanged": True,
            "historical_information_firewall_revalidated_each_run": True,
            "completed_nfl_history_remains_immutable": True,
            "probability_mass_pruned_for_performance": False,
            "quality_reduced_for_benchmark": False,
        },
        "runs": rows,
        "scaling_summary": scaling,
    }
    out = ah.write_isolated_json("performance/generic_cycle_scaling_1_0.json", payload)
    print(out)
    print(json.dumps(scaling, indent=2, sort_keys=True))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Profile generic Alternate History particle scaling")
    parser.add_argument(
        "--particles",
        type=int,
        nargs="+",
        default=list(DEFAULT_PARTICLE_SCALES),
        help="Particle counts to benchmark (default: 2 4 8)",
    )
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args()
    profile(args.particles, seed=args.seed)


if __name__ == "__main__":
    main()
