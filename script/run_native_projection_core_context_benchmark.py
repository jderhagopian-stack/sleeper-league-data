#!/usr/bin/env python3
"""Leakage-safe ablation of remaining core native-projection feature families.

Tests three deliberately small, position-specific families on top of the accepted
lag-1/lag-2 model: nuanced age/career stage, durability/availability shape, and
lagged team context. Each family is evaluated alone and in a combined challenger
on rolling temporal holdouts. No target-season realized context is used.
"""
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
    STATS,
    TARGETS,
    fetch_csv,
    make_lagged_rows,
    normalize_season,
)

PLAYERS_URL = "https://github.com/nflverse/nflverse-data/releases/download/players/players.csv"


def fval(v) -> float:
    try:
        return float(v or 0.0)
    except (TypeError, ValueError):
        return 0.0


def fetch_players() -> dict:
    req = urllib.request.Request(PLAYERS_URL, headers={"User-Agent": "FSFFL-core-context-benchmark/1.0"})
    with urllib.request.urlopen(req, timeout=60) as response:
        rows = list(csv.DictReader(io.StringIO(response.read().decode("utf-8-sig"))))
    out = {}
    for r in rows:
        pid = str(r.get("gsis_id") or "").strip()
        if pid:
            out[pid] = {
                "birth_date": str(r.get("birth_date") or "").strip(),
                "entry_year": str(r.get("entry_year") or r.get("rookie_year") or "").strip(),
            }
    return out


def team_context(season_rows: list[dict]) -> dict:
    """Build only lagged team totals from the feature season."""
    out = {}
    for r in season_rows:
        key = (int(r["season"]), str(r.get("team") or ""))
        if not key[1]:
            continue
        t = out.setdefault(key, {"games": 0.0, **{s: 0.0 for s in STATS}})
        t["games"] = max(t["games"], fval(r.get("games")))
        for s in STATS:
            t[s] += fval(r.get(s))
    return out


def enrich(rows: list[dict], season_rows: list[dict], players: dict) -> list[dict]:
    teams = team_context(season_rows)
    feature_index = {(int(r["season"]), str(r["player_id"])): r for r in season_rows}
    enriched = []
    for raw in rows:
        row = dict(raw)
        target_season = int(row["season"])
        feature_season = int(row["feature_season"])
        pid = str(row["player_id"])
        cur = feature_index.get((feature_season, pid), {})
        team = teams.get((feature_season, str(cur.get("team") or "")), {})

        meta = players.get(pid, {})
        age = 0.0
        age_ok = 0
        birth = meta.get("birth_date")
        if birth:
            try:
                born = date.fromisoformat(birth[:10])
                age = (date(target_season, 9, 1) - born).days / 365.2425
                age_ok = int(17.0 <= age <= 50.0)
                if not age_ok:
                    age = 0.0
            except ValueError:
                pass
        entry = int(fval(meta.get("entry_year")))
        career_year = max(0, target_season - entry + 1) if entry else 0
        row.update({
            "age_available": age_ok,
            "target_age": age,
            "target_age_sq": age * age if age_ok else 0.0,
            "career_year": career_year,
            "recent_games_avg": (fval(row.get("lag1_games")) + fval(row.get("lag2_games"))) / (2.0 if row.get("lag2_available") else 1.0),
            "lag1_low_availability": int(fval(row.get("lag1_games")) < 12.0),
            "two_year_low_availability": int(bool(row.get("lag2_available")) and fval(row.get("lag1_games")) < 12.0 and fval(row.get("lag2_games")) < 12.0),
            "team_pass_attempts": fval(team.get("attempts")),
            "team_passing_yards": fval(team.get("passing_yards")),
            "team_carries": fval(team.get("carries")),
            "team_rushing_tds": fval(team.get("rushing_tds")),
            "team_targets": fval(team.get("targets")),
            "team_receiving_tds": fval(team.get("receiving_tds")),
        })
        # Position-specific nonlinear/interaction terms. These are derived only
        # from information known before the target season.
        if row["position"] == "RB":
            recent_touches = fval(row.get("lag1_carries")) + fval(row.get("lag1_receptions"))
            row["age_x_recent_touches"] = age * recent_touches if age_ok else 0.0
        elif row["position"] == "WR":
            row["age_x_lag1_targets"] = age * fval(row.get("lag1_targets")) if age_ok else 0.0
        elif row["position"] == "QB":
            attempts = max(1.0, fval(row.get("lag1_attempts")))
            rush_dependence = fval(row.get("lag1_carries")) / attempts
            row["rush_dependence"] = rush_dependence
            row["age_x_rush_dependence"] = age * rush_dependence if age_ok else 0.0
        enriched.append(row)
    return enriched


AGE = {
    "QB": ["age_available", "target_age", "target_age_sq", "career_year", "rush_dependence", "age_x_rush_dependence"],
    "RB": ["age_available", "target_age", "target_age_sq", "career_year", "age_x_recent_touches"],
    "WR": ["age_available", "target_age", "target_age_sq", "career_year", "age_x_lag1_targets"],
    "TE": ["age_available", "target_age", "target_age_sq", "career_year"],
}
DURABILITY = {p: ["recent_games_avg", "lag1_low_availability", "two_year_low_availability"] for p in POSITIONS}
TEAM = {
    "QB": ["team_pass_attempts", "team_passing_yards", "team_targets"],
    "RB": ["team_carries", "team_rushing_tds", "team_pass_attempts"],
    "WR": ["team_pass_attempts", "team_passing_yards", "team_targets", "team_receiving_tds"],
    "TE": ["team_pass_attempts", "team_targets", "team_receiving_tds"],
}


def evaluate_variant(rows: list[dict], extra: dict, first_holdout: int, end_season: int) -> dict:
    holdouts = [s for s in sorted({int(r["season"]) for r in rows}) if first_holdout <= s <= end_season]
    by_season = {}
    aggregate = {}
    for holdout in holdouts:
        eligible = [r for r in rows if int(r["season"]) <= holdout]
        by_season[str(holdout)] = {}
        for p in sorted(POSITIONS):
            features = list(BASE_FEATURES[p]) + list(extra[p])
            report = temporal_holdout(eligible, p, features, TARGETS[p])
            vals = list(report["targets"].values())
            by_season[str(holdout)][p] = {
                "targets": report["targets"],
                "mean_improvement_vs_persistence_pct": sum(float(v.get("improvement_vs_persistence_pct", 0.0)) for v in vals) / len(vals),
                "targets_beating_persistence": sum(bool(v.get("beats_persistence")) for v in vals),
            }
    for p in sorted(POSITIONS):
        per = [by_season[str(s)][p] for s in holdouts]
        aggregate[p] = {
            "mean_improvement_vs_persistence_pct": sum(x["mean_improvement_vs_persistence_pct"] for x in per) / len(per),
            "mean_targets_beating_persistence": sum(x["targets_beating_persistence"] for x in per) / len(per),
            "target_stability": {
                t: {
                    "seasons_beating_persistence": sum(bool(by_season[str(s)][p]["targets"][t].get("beats_persistence")) for s in holdouts),
                    "mean_improvement_vs_persistence_pct": sum(float(by_season[str(s)][p]["targets"][t].get("improvement_vs_persistence_pct", 0.0)) for s in holdouts) / len(holdouts),
                }
                for t in TARGETS[p]
            },
        }
    return {"aggregate": aggregate, "by_season": by_season}


def evaluate(start_season: int, end_season: int, first_holdout: int) -> dict:
    season_rows = []
    for season in range(start_season, end_season + 1):
        season_rows.extend(normalize_season(fetch_csv(season), season))
    rows = enrich(make_lagged_rows(season_rows), season_rows, fetch_players())
    variants = {
        "age_position_specific": AGE,
        "durability_position_specific": DURABILITY,
        "team_context_position_specific": TEAM,
        "combined_core_context": {p: AGE[p] + DURABILITY[p] + TEAM[p] for p in POSITIONS},
    }
    results = {name: evaluate_variant(rows, extra, first_holdout, end_season) for name, extra in variants.items()}
    return {
        "schema_version": "1.0",
        "status": "PASS",
        "experiment": "remaining_core_feature_family_ablation",
        "variants": results,
        "governance": {
            "future_seasons_used_for_training": False,
            "target_season_realized_team_context_used": False,
            "fantasy_points_used_as_target": False,
            "age_reference_date": "September 1 of target season",
            "durability_scope": "recent games-played shape only; no medical diagnosis or injury-type inference",
            "team_context_scope": "feature-season team totals with position-specific subsets; no blanket coefficient or target-season context",
            "promotion_rule": "retain only position/family combinations with material and reasonably stable rolling holdout improvement over the accepted model",
            "production_promoted": False,
        },
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--start-season", type=int, default=2016)
    p.add_argument("--end-season", type=int, default=2025)
    p.add_argument("--first-holdout", type=int, default=2021)
    p.add_argument("--output", type=Path, default=Path("data/model_validation/native_projection_core_context_benchmark.json"))
    args = p.parse_args()
    result = evaluate(args.start_season, args.end_season, args.first_holdout)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": "PASS", "variants": {k: v["aggregate"] for k, v in result["variants"].items()}}, indent=2))


if __name__ == "__main__":
    main()
