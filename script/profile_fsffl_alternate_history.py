#!/usr/bin/env python3
"""Profile FSFFL Alternate History scaling without changing model logic.

Each benchmark case runs in a fresh subprocess. The audit can install the
validated accuracy-neutral runtime optimizations and records wall time for each
generic orchestration phase in addition to state growth and peak RSS.
"""

from __future__ import annotations

import argparse
import functools
import json
import resource
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterable, List

import alternate_history_engine as ah
import alternate_history_performance_runtime as perf
import run_fsffl_generic_alternate_history as generic
import validate_fsffl_generic_alternate_history as regression

DEFAULT_PARTICLE_SCALES = (2, 4, 8)
DEFAULT_SEED = 20260824
SUPERLINEAR_TOLERANCE = 1.25


def _peak_rss_mb() -> float:
    """Return peak resident memory for this process on Linux CI."""
    value = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    return round(value / 1024.0, 3)


def _phase_metrics(report: Dict[str, Any], particles: int) -> List[Dict[str, Any]]:
    rows = []
    previous_unique = None
    for phase in report.get("phase_audit") or []:
        unique = int(phase.get("unique_states") or 0)
        growth = round(unique / previous_unique, 6) if previous_unique not in (None, 0) else None
        rows.append({
            "phase": phase.get("phase"),
            "season": phase.get("season"),
            "unique_states": unique,
            "state_to_particle_ratio": round(unique / particles, 6) if particles else None,
            "particle_to_state_compression": round(particles / unique, 6) if unique else None,
            "unique_state_growth_from_prior_phase": growth,
            "events_processed": phase.get("events_processed"),
            "picks_simulated": phase.get("picks_simulated"),
        })
        previous_unique = unique
    return rows


def _season_from_call(label: str, args: tuple[Any, ...], kwargs: Dict[str, Any]) -> str | None:
    if label == "fork_season_boundary":
        return None
    season = kwargs.get("season") or kwargs.get("draft_season")
    if season is not None:
        return str(season)
    return None


@contextmanager
def _phase_timer():
    """Temporarily time generic orchestration calls without altering outputs."""
    timings: List[Dict[str, Any]] = []
    targets = [
        (generic.predraft, "anchored_boundary_simulate", "fork_season_boundary"),
        (generic.cycle, "replay_predraft_offseason", "predraft_offseason"),
        (generic, "replay_rookie_draft_groups", "rookie_draft"),
        (generic.cycle, "propagate_completed_season", "completed_season"),
        (generic.cycle, "replay_active_season_to_now", "active_season_to_now"),
    ]
    originals = []
    for owner, attr, label in targets:
        original = getattr(owner, attr)
        originals.append((owner, attr, original))

        @functools.wraps(original)
        def wrapped(*args, __original=original, __label=label, **kwargs):
            started = time.perf_counter()
            try:
                return __original(*args, **kwargs)
            finally:
                timings.append({
                    "phase": __label,
                    "season": _season_from_call(__label, args, kwargs),
                    "elapsed_seconds": round(time.perf_counter() - started, 4),
                })

        setattr(owner, attr, wrapped)
    try:
        yield timings
    finally:
        for owner, attr, original in originals:
            setattr(owner, attr, original)


def _run_one(
    scenario_path: Path,
    *,
    expected_fork: int,
    particles: int,
    seed: int,
    optimized: bool,
) -> Dict[str, Any]:
    if optimized:
        perf.install()
    started = time.perf_counter()
    with _phase_timer() as phase_timing:
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
    phase_metrics = _phase_metrics(report, particles)
    peak_phase = max(phase_metrics, key=lambda row: int(row.get("unique_states") or 0), default=None)
    timed_seconds = sum(float(row["elapsed_seconds"]) for row in phase_timing)
    return {
        "fork_season": expected_fork,
        "particles": particles,
        "seed": seed,
        "optimized_runtime": optimized,
        "elapsed_seconds": round(elapsed, 4),
        "particles_per_second": round(particles / elapsed, 4) if elapsed else None,
        "peak_rss_mb": _peak_rss_mb(),
        "final_unique_states": final_unique,
        "final_state_to_particle_ratio": round(final_unique / particles, 6) if particles else None,
        "peak_unique_state_phase": peak_phase,
        "phase_metrics": phase_metrics,
        "phase_timing": phase_timing,
        "phase_timing_total_seconds": round(timed_seconds, 4),
        "non_phase_overhead_seconds": round(max(0.0, elapsed - timed_seconds), 4),
        "validated_information_firewall": True,
        "validated_probability_mass": True,
    }


def _case_definition(label: str) -> tuple[Path, int]:
    if label == "generated-2022":
        return regression.build_2022_scenario(), 2022
    if label == "puka-2023":
        return regression.PREFERRED, 2023
    raise ah.AlternateHistoryError(f"unknown performance-audit case: {label}")


def _run_isolated(label: str, particles: int, seed: int, *, optimized: bool) -> Dict[str, Any]:
    """Run one benchmark in a fresh process so RSS is independently measurable."""
    with tempfile.TemporaryDirectory(prefix="alternate-history-profile-") as tmp:
        result_path = Path(tmp) / "result.json"
        command = [
            sys.executable,
            str(Path(__file__).resolve()),
            "--single-case", label,
            "--single-particles", str(particles),
            "--seed", str(seed),
            "--result-path", str(result_path),
        ]
        if optimized:
            command.append("--optimized")
        subprocess.run(command, check=True)
        if not result_path.exists():
            raise ah.AlternateHistoryError(
                f"isolated performance audit did not emit result for {label}/{particles}"
            )
        return json.loads(result_path.read_text())


def _scaling_summary(case_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    case_rows = sorted(case_rows, key=lambda row: int(row["particles"]))
    baseline = case_rows[0]
    base_particles = float(baseline["particles"])
    base_elapsed = float(baseline["elapsed_seconds"])
    result = []
    previous = None
    for row in case_rows:
        particle_multiple = float(row["particles"]) / base_particles
        runtime_multiple = float(row["elapsed_seconds"]) / base_elapsed if base_elapsed else None
        adjacent_particle_multiple = None
        adjacent_runtime_multiple = None
        scaling_efficiency = None
        superlinear = False
        if previous is not None:
            adjacent_particle_multiple = float(row["particles"]) / float(previous["particles"])
            previous_elapsed = float(previous["elapsed_seconds"])
            adjacent_runtime_multiple = float(row["elapsed_seconds"]) / previous_elapsed if previous_elapsed else None
            if adjacent_runtime_multiple is not None and adjacent_particle_multiple:
                scaling_efficiency = adjacent_particle_multiple / adjacent_runtime_multiple
                superlinear = adjacent_runtime_multiple > adjacent_particle_multiple * SUPERLINEAR_TOLERANCE
        result.append({
            "particles": row["particles"],
            "elapsed_seconds": row["elapsed_seconds"],
            "peak_rss_mb": row["peak_rss_mb"],
            "particle_multiple": round(particle_multiple, 3),
            "runtime_multiple": round(runtime_multiple, 3) if runtime_multiple is not None else None,
            "adjacent_particle_multiple": round(adjacent_particle_multiple, 3) if adjacent_particle_multiple is not None else None,
            "adjacent_runtime_multiple": round(adjacent_runtime_multiple, 3) if adjacent_runtime_multiple is not None else None,
            "adjacent_scaling_efficiency": round(scaling_efficiency, 4) if scaling_efficiency is not None else None,
            "superlinear_runtime_flag": superlinear,
        })
        previous = row
    return result


def profile(
    particle_scales: Iterable[int],
    *,
    seed: int = DEFAULT_SEED,
    optimized: bool = False,
) -> Path:
    scales = sorted({int(value) for value in particle_scales})
    if not scales or any(value <= 0 for value in scales):
        raise ah.AlternateHistoryError("performance particle scales must be positive")

    cases = ("generated-2022", "puka-2023")
    rows: List[Dict[str, Any]] = []
    for label in cases:
        for particles in scales:
            result = _run_isolated(label, particles, seed, optimized=optimized)
            result["case"] = label
            rows.append(result)

    by_case: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        by_case.setdefault(str(row["case"]), []).append(row)
    scaling = {label: _scaling_summary(case_rows) for label, case_rows in by_case.items()}
    alerts = [
        {
            "case": label,
            "particles": row["particles"],
            "adjacent_particle_multiple": row["adjacent_particle_multiple"],
            "adjacent_runtime_multiple": row["adjacent_runtime_multiple"],
        }
        for label, rows_for_case in scaling.items()
        for row in rows_for_case
        if row["superlinear_runtime_flag"]
    ]

    payload = {
        "model_version": "Fantasy-Alternate-History-1.1-performance-audit",
        "purpose": "Measure optimized scaling, phase wall time, isolated peak memory, and state growth without changing decisions or pruning probability mass.",
        "configuration": {
            "particle_scales": scales,
            "seed": seed,
            "optimized_runtime": optimized,
            "superlinear_tolerance": SUPERLINEAR_TOLERANCE,
            "fresh_process_per_case_scale": True,
        },
        "design_invariants": {
            "generic_orchestrator_logic_unchanged": True,
            "historical_information_firewall_revalidated_each_run": True,
            "completed_nfl_history_remains_immutable": True,
            "probability_mass_pruned_for_performance": False,
            "quality_reduced_for_benchmark": False,
            "phase_timing_is_observational_only": True,
        },
        "runs": rows,
        "scaling_summary": scaling,
        "performance_alerts": alerts,
    }
    out = ah.write_isolated_json("performance/generic_cycle_scaling_1_0.json", payload)
    print(out)
    print(json.dumps({"scaling_summary": scaling, "performance_alerts": alerts}, indent=2, sort_keys=True))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Profile generic Alternate History particle scaling")
    parser.add_argument("--particles", type=int, nargs="+", default=list(DEFAULT_PARTICLE_SCALES))
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--optimized", action="store_true", help="Install validated accuracy-neutral runtime optimizations")
    parser.add_argument("--single-case", choices=("generated-2022", "puka-2023"))
    parser.add_argument("--single-particles", type=int)
    parser.add_argument("--result-path", type=Path)
    args = parser.parse_args()

    if args.single_case:
        if args.single_particles is None or args.single_particles <= 0 or args.result_path is None:
            raise ah.AlternateHistoryError("single-case audit requires positive --single-particles and --result-path")
        scenario, fork = _case_definition(args.single_case)
        result = _run_one(
            scenario,
            expected_fork=fork,
            particles=args.single_particles,
            seed=args.seed,
            optimized=args.optimized,
        )
        args.result_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
        return

    profile(args.particles, seed=args.seed, optimized=args.optimized)


if __name__ == "__main__":
    main()
