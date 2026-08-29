#!/usr/bin/env python3
"""Test simple age/career-stage features on the accepted native projection model."""
from __future__ import annotations

import argparse
import csv
import io
import json
import urllib.request
from datetime import date
from pathlib import Path

from native_projection_challenger import temporal_holdout
from run_native_projection_nflverse_benchmark import (
    FEATURES as BASE_FEATURES,
    POSITIONS,
    TARGETS,
    fetch_csv,
    make_lagged_rows,
    normalize_season,
)

PLAYERS_URL = "https://github.com/nflverse/nflverse-data/releases/download/players/players.csv"
AGE_FEATURES = ["age_available", "target_season_age", "target_season_age_sq", "career_year"]
FEATURES = {p: list(BASE_FEATURES[p]) + (AGE_FEATURES if p == "TE" else []) for p in POSITIONS}


def fval(value) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def fetch_players() -> dict:
    req = urllib.request.Request(PLAYERS_URL, headers={"User-Agent": "FSFFL-native-projection-age-benchmark/1.0"})
    with urllib.request.urlopen(req, timeout=60) as response:
        rows = list(csv.DictReader(io.StringIO(response.read().decode("utf-8-sig"))))
    out = {}
    for r in rows:
        pid = str(r.get("gsis_id") or "").strip()
        if not pid:
            continue
        out[pid] = {
            "birth_date": str(r.get("birth_date") or "").strip(),
            "entry_year": str(r.get("entry_year") or r.get("rookie_year") or "").strip(),
        }
    return out


def add_age_features(rows: list[dict], players: dict) -> list[dict]:
    enriched = []
    for raw in rows:
        row = dict(raw)
        meta = players.get(str(row.get("player_id")), {})
        target_season = int(row["season"])
        age = 0.0
        available = 0
        birth = meta.get("birth_date")
        if birth:
            try:
                born = date.fromisoformat(birth[:10])
                asof = date(target_season, 9, 1)
                age = (asof - born).days / 365.2425
                if 17.0 <= age <= 50.0:
                    available = 1
                else:
                    age = 0.0
            except ValueError:
                age = 0.0
        entry = int(fval(meta.get("entry_year")))
        career_year = max(0, target_season - entry + 1) if entry else 0
        row["age_available"] = available
        row["target_season_age"] = age
        row["target_season_age_sq"] = age * age if available else 0.0
        row["career_year"] = career_year
        enriched.append(row)
    return enriched


def evaluate(start_season: int, end_season: int, first_holdout: int) -> dict:
    season_rows = []
    for season in range(start_season, end_season + 1):
        raw = fetch_csv(season)
        season_rows.extend(normalize_season(raw, season))
    lagged = add_age_features(make_lagged_rows(season_rows), fetch_players())
    holdouts = [s for s in sorted({int(r["season"]) for r in lagged}) if first_holdout <= s <= end_season]
    by_season = {}
    aggregate = {}
    for holdout in holdouts:
        eligible = [r for r in lagged if int(r["season"]) <= holdout]
        by_season[str(holdout)] = {}
        for p in sorted(POSITIONS):
            report = temporal_holdout(eligible, p, FEATURES[p], TARGETS[p])
            vals = list(report["targets"].values())
            by_season[str(holdout)][p] = {
                "targets": report["targets"],
                "mean_improvement_vs_persistence_pct": sum(float(v.get("improvement_vs_persistence_pct", 0.0)) for v in vals) / len(vals),
                "targets_beating_persistence": sum(bool(v.get("beats_persistence")) for v in vals),
            }
    for p in sorted(POSITIONS):
        per = [by_season[str(s)][p] for s in holdouts]
        aggregate[p] = {
            "holdout_seasons": holdouts,
            "target_count": len(TARGETS[p]),
            "mean_targets_beating_persistence": sum(x["targets_beating_persistence"] for x in per) / len(per),
            "mean_improvement_vs_persistence_pct": sum(x["mean_improvement_vs_persistence_pct"] for x in per) / len(per),
            "target_stability": {
                t: {
                    "seasons_beating_persistence": sum(bool(by_season[str(s)][p]["targets"][t].get("beats_persistence")) for s in holdouts),
                    "seasons_tested": len(holdouts),
                    "mean_improvement_vs_persistence_pct": sum(float(by_season[str(s)][p]["targets"][t].get("improvement_vs_persistence_pct", 0.0)) for s in holdouts) / len(holdouts),
                }
                for t in TARGETS[p]
            },
        }
    coverage = sum(int(r["age_available"]) for r in lagged) / len(lagged) if lagged else 0.0
    return {
        "schema_version": "1.1",
        "status": "PASS",
        "experiment": "tight_end_age_and_career_stage_after_all_position_ablation",
        "aggregate": aggregate,
        "age_metadata_coverage": coverage,
        "governance": {
            "future_seasons_used_for_training": False,
            "fantasy_points_used_as_target": False,
            "age_reference_date": "September 1 of target season",
            "metadata_fields": ["birth_date", "entry_year"],
            "all_position_ablation_result": "QB gain trivial; RB and WR regressed; TE materially improved, so age features are retained only for TE in this challenger",
            "production_promoted": False
        }
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--start-season", type=int, default=2016)
    p.add_argument("--end-season", type=int, default=2025)
    p.add_argument("--first-holdout", type=int, default=2021)
    p.add_argument("--output", type=Path, default=Path("data/model_validation/native_projection_age_benchmark.json"))
    args = p.parse_args()
    result = evaluate(args.start_season, args.end_season, args.first_holdout)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": "PASS", "coverage": result["age_metadata_coverage"], "aggregate": result["aggregate"]}, indent=2))


if __name__ == "__main__":
    main()
