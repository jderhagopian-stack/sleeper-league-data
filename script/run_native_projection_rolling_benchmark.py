#!/usr/bin/env python3
"""Rolling-origin validation for the FSFFL-native projection challenger.

This deliberately reuses the exact nflverse normalization and leakage controls
from the single-holdout benchmark. Each requested test season is evaluated only
with rows from earlier seasons, so later NFL outcomes cannot influence fitting
or ridge-alpha selection.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from run_native_projection_nflverse_benchmark import (
    FEATURES,
    POSITIONS,
    TARGETS,
    fetch_csv,
    make_lagged_rows,
    normalize_season,
)
from native_projection_challenger import temporal_holdout


def evaluate_rolling(start_season: int, end_season: int, first_holdout: int) -> dict:
    all_rows = []
    source_counts = {}
    for season in range(start_season, end_season + 1):
        raw = fetch_csv(season)
        normalized = normalize_season(raw, season)
        if not normalized:
            raise ValueError(f"{season}: no eligible QB/RB/WR/TE rows")
        all_rows.extend(normalized)
        source_counts[str(season)] = len(normalized)

    lagged = make_lagged_rows(all_rows)
    available = sorted({int(r["season"]) for r in lagged})
    holdouts = [s for s in available if first_holdout <= s <= end_season]
    if not holdouts:
        raise ValueError("no rolling holdout seasons available")

    by_season = {}
    aggregate = {}
    for holdout in holdouts:
        eligible = [r for r in lagged if int(r["season"]) <= holdout]
        season_report = {}
        for position in sorted(POSITIONS):
            report = temporal_holdout(eligible, position, FEATURES[position], TARGETS[position])
            if int(report["holdout_season"]) != holdout:
                raise AssertionError(f"{position}: expected holdout {holdout}, got {report['holdout_season']}")
            target_results = list(report["targets"].values())
            season_report[position] = {
                "train_n": report["train_n"],
                "holdout_n": report["holdout_n"],
                "targets_beating_persistence": sum(bool(r.get("beats_persistence")) for r in target_results),
                "target_count": len(target_results),
                "mean_improvement_vs_persistence_pct": sum(
                    float(r.get("improvement_vs_persistence_pct", 0.0)) for r in target_results
                ) / len(target_results),
                "targets": report["targets"],
            }
        by_season[str(holdout)] = season_report

    for position in sorted(POSITIONS):
        per_season = [by_season[str(s)][position] for s in holdouts]
        target_names = TARGETS[position]
        target_stability = {}
        for target in target_names:
            vals = [season[position]["targets"][target] for season in [by_season[str(s)] for s in holdouts]]
            target_stability[target] = {
                "seasons_beating_persistence": sum(bool(v.get("beats_persistence")) for v in vals),
                "seasons_tested": len(vals),
                "mean_improvement_vs_persistence_pct": sum(float(v.get("improvement_vs_persistence_pct", 0.0)) for v in vals) / len(vals),
            }
        aggregate[position] = {
            "holdout_seasons": holdouts,
            "mean_targets_beating_persistence": sum(r["targets_beating_persistence"] for r in per_season) / len(per_season),
            "target_count": len(target_names),
            "mean_improvement_vs_persistence_pct": sum(r["mean_improvement_vs_persistence_pct"] for r in per_season) / len(per_season),
            "target_stability": target_stability,
        }

    return {
        "schema_version": "1.0",
        "status": "PASS",
        "evaluation": "rolling_origin_temporal_holdout",
        "source_seasons": [start_season, end_season],
        "first_holdout": first_holdout,
        "holdout_seasons": holdouts,
        "source_counts": source_counts,
        "aggregate": aggregate,
        "by_season": by_season,
        "governance": {
            "future_seasons_used_for_training": False,
            "fantasy_points_used_as_target": False,
            "baseline": "prior-year same-stat persistence",
            "production_promoted": False,
        },
    }


def self_test() -> dict:
    # Structural test only; network-backed real-data execution happens in CI.
    assert sorted(POSITIONS) == ["QB", "RB", "TE", "WR"]
    for position in POSITIONS:
        assert FEATURES[position]
        assert TARGETS[position]
        assert all(t.startswith("next_") for t in TARGETS[position])
    return {"status": "PASS", "positions": sorted(POSITIONS)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-season", type=int, default=2016)
    parser.add_argument("--end-season", type=int, default=2025)
    parser.add_argument("--first-holdout", type=int, default=2021)
    parser.add_argument("--output", type=Path, default=Path("data/model_validation/native_projection_rolling_benchmark.json"))
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        print(json.dumps(self_test(), indent=2, sort_keys=True))
        return
    result = evaluate_rolling(args.start_season, args.end_season, args.first_holdout)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": "PASS", "aggregate": result["aggregate"], "output": str(args.output)}, indent=2))


if __name__ == "__main__":
    main()
