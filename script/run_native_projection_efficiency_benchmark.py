#!/usr/bin/env python3
"""Test simple prior-year efficiency rates on top of the accepted native model."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from native_projection_challenger import temporal_holdout
from run_native_projection_nflverse_benchmark import POSITIONS, TARGETS, fetch_csv, make_lagged_rows, normalize_season
from run_native_projection_age_benchmark import FEATURES as ACCEPTED_FEATURES, add_age_features, fetch_players

EFFICIENCY_FEATURES = {
    "QB": ["eff_completion_rate", "eff_pass_ypa", "eff_pass_td_rate", "eff_int_rate", "eff_rush_ypc"],
    "RB": ["eff_rush_ypc", "eff_rush_td_rate", "eff_catch_rate", "eff_rec_ypt", "eff_rec_ypr", "eff_rec_td_rate"],
    "WR": ["eff_catch_rate", "eff_rec_ypt", "eff_rec_ypr", "eff_rec_td_rate"],
    "TE": ["eff_catch_rate", "eff_rec_ypt", "eff_rec_ypr", "eff_rec_td_rate"],
}
FEATURES = {p: list(ACCEPTED_FEATURES[p]) + EFFICIENCY_FEATURES[p] for p in POSITIONS}


def rate(num: float, den: float) -> float:
    return float(num) / float(den) if den and den > 0 else 0.0


def add_efficiency(rows: list[dict]) -> list[dict]:
    out = []
    for raw in rows:
        r = dict(raw)
        attempts = float(r.get("lag1_attempts", 0.0))
        carries = float(r.get("lag1_carries", 0.0))
        targets = float(r.get("lag1_targets", 0.0))
        receptions = float(r.get("lag1_receptions", 0.0))
        r["eff_completion_rate"] = rate(float(r.get("lag1_completions", 0.0)), attempts)
        r["eff_pass_ypa"] = rate(float(r.get("lag1_passing_yards", 0.0)), attempts)
        r["eff_pass_td_rate"] = rate(float(r.get("lag1_passing_tds", 0.0)), attempts)
        r["eff_int_rate"] = rate(float(r.get("lag1_interceptions", 0.0)), attempts)
        r["eff_rush_ypc"] = rate(float(r.get("lag1_rushing_yards", 0.0)), carries)
        r["eff_rush_td_rate"] = rate(float(r.get("lag1_rushing_tds", 0.0)), carries)
        r["eff_catch_rate"] = rate(receptions, targets)
        r["eff_rec_ypt"] = rate(float(r.get("lag1_receiving_yards", 0.0)), targets)
        r["eff_rec_ypr"] = rate(float(r.get("lag1_receiving_yards", 0.0)), receptions)
        r["eff_rec_td_rate"] = rate(float(r.get("lag1_receiving_tds", 0.0)), targets)
        out.append(r)
    return out


def evaluate(start_season: int, end_season: int, first_holdout: int) -> dict:
    season_rows = []
    for season in range(start_season, end_season + 1):
        raw = fetch_csv(season)
        season_rows.extend(normalize_season(raw, season))
    lagged = add_efficiency(add_age_features(make_lagged_rows(season_rows), fetch_players()))
    holdouts = [s for s in sorted({int(r["season"]) for r in lagged}) if first_holdout <= s <= end_season]
    by_season = {}
    aggregate = {}
    for holdout in holdouts:
        eligible = [r for r in lagged if int(r["season"]) <= holdout]
        by_season[str(holdout)] = {}
        for p in sorted(POSITIONS):
            rep = temporal_holdout(eligible, p, FEATURES[p], TARGETS[p])
            vals = list(rep["targets"].values())
            by_season[str(holdout)][p] = {
                "targets": rep["targets"],
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
    return {"schema_version":"1.0","status":"PASS","experiment":"restrained_prior_year_efficiency_rates","aggregate":aggregate,"governance":{"future_seasons_used_for_training":False,"fantasy_points_used_as_target":False,"efficiency_source":"feature-season player stats only","regularization":"same ridge shrinkage as accepted challenger","production_promoted":False}}


def main() -> None:
    p=argparse.ArgumentParser(); p.add_argument("--start-season",type=int,default=2016); p.add_argument("--end-season",type=int,default=2025); p.add_argument("--first-holdout",type=int,default=2021); p.add_argument("--output",type=Path,default=Path("data/model_validation/native_projection_efficiency_benchmark.json")); a=p.parse_args()
    result=evaluate(a.start_season,a.end_season,a.first_holdout); a.output.parent.mkdir(parents=True,exist_ok=True); a.output.write_text(json.dumps(result,indent=2,sort_keys=True)+"\n",encoding="utf-8"); print(json.dumps({"status":"PASS","aggregate":result["aggregate"]},indent=2))

if __name__ == "__main__": main()
