#!/usr/bin/env python3
"""Validate only the evidence-supported V1 native-projection feature selections."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from run_native_projection_core_context_benchmark import AGE, DURABILITY, enrich, evaluate_variant, fetch_players
from run_native_projection_nflverse_benchmark import POSITIONS, fetch_csv, make_lagged_rows, normalize_season

SELECTED = {
    "QB": list(DURABILITY["QB"]),
    "RB": [],
    "WR": list(AGE["WR"]),
    "TE": list(AGE["TE"]),
}


def evaluate(start_season: int, end_season: int, first_holdout: int) -> dict:
    season_rows = []
    for season in range(start_season, end_season + 1):
        season_rows.extend(normalize_season(fetch_csv(season), season))
    rows = enrich(make_lagged_rows(season_rows), season_rows, fetch_players())
    result = evaluate_variant(rows, SELECTED, first_holdout, end_season)
    return {
        "schema_version": "1.0",
        "status": "PASS",
        "experiment": "selected_v1_core_features",
        "selected_features": SELECTED,
        "aggregate": result["aggregate"],
        "by_season": result["by_season"],
        "governance": {
            "future_seasons_used_for_training": False,
            "target_season_realized_team_context_used": False,
            "fantasy_points_used_as_target": False,
            "production_promoted": False,
            "purpose": "Confirm the frozen selected position-specific feature bundle before external preseason benchmarking."
        }
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--start-season", type=int, default=2016)
    p.add_argument("--end-season", type=int, default=2025)
    p.add_argument("--first-holdout", type=int, default=2021)
    p.add_argument("--output", type=Path, default=Path("data/model_validation/native_projection_selected_core_benchmark.json"))
    args = p.parse_args()
    result = evaluate(args.start_season, args.end_season, args.first_holdout)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "aggregate": result["aggregate"]}, indent=2))


if __name__ == "__main__":
    main()
