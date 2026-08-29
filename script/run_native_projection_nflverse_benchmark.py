#!/usr/bin/env python3
"""Leakage-safe real-data benchmark for the FSFFL-native projection challenger."""
from __future__ import annotations

import argparse
import csv
import io
import json
import sys
import traceback
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import List

sys.path.insert(0, str(Path(__file__).resolve().parent))
from native_projection_challenger import temporal_holdout  # noqa: E402

URL = "https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_reg_{season}.csv"
POSITIONS = {"QB", "RB", "WR", "TE"}
SOURCE_STATS = {
    "completions": "completions",
    "attempts": "attempts",
    "passing_yards": "passing_yards",
    "passing_tds": "passing_tds",
    "interceptions": "passing_interceptions",
    "carries": "carries",
    "rushing_yards": "rushing_yards",
    "rushing_tds": "rushing_tds",
    "targets": "targets",
    "receptions": "receptions",
    "receiving_yards": "receiving_yards",
    "receiving_tds": "receiving_tds",
}
STATS = list(SOURCE_STATS)
TEAM_FEATURES = [
    "team_lag1_pass_attempts",
    "team_lag1_rush_attempts",
    "team_lag1_targets",
    "team_lag1_offensive_opportunities",
    "team_lag1_pass_rate",
    "team_lag1_rushing_tds",
    "team_lag1_receiving_tds",
]
FEATURES = {
    "QB": [
        "lag1_games", "lag1_attempts", "lag1_passing_yards", "lag1_passing_tds", "lag1_interceptions", "lag1_carries", "lag1_rushing_yards", "lag1_rushing_tds",
        "lag2_available", "lag2_games", "lag2_attempts", "lag2_passing_yards", "lag2_passing_tds", "lag2_interceptions", "lag2_carries", "lag2_rushing_yards", "lag2_rushing_tds",
        *TEAM_FEATURES,
    ],
    "RB": [
        "lag1_games", "lag1_carries", "lag1_rushing_yards", "lag1_rushing_tds", "lag1_targets", "lag1_receptions", "lag1_receiving_yards", "lag1_receiving_tds",
        "lag2_available", "lag2_games", "lag2_carries", "lag2_rushing_yards", "lag2_rushing_tds", "lag2_targets", "lag2_receptions", "lag2_receiving_yards", "lag2_receiving_tds",
        *TEAM_FEATURES,
    ],
    "WR": [
        "lag1_games", "lag1_targets", "lag1_receptions", "lag1_receiving_yards", "lag1_receiving_tds", "lag1_carries", "lag1_rushing_yards", "lag1_rushing_tds",
        "lag2_available", "lag2_games", "lag2_targets", "lag2_receptions", "lag2_receiving_yards", "lag2_receiving_tds",
        *TEAM_FEATURES,
    ],
    "TE": [
        "lag1_games", "lag1_targets", "lag1_receptions", "lag1_receiving_yards", "lag1_receiving_tds",
        "lag2_available", "lag2_games", "lag2_targets", "lag2_receptions", "lag2_receiving_yards", "lag2_receiving_tds",
        *TEAM_FEATURES,
    ],
}
TARGETS = {
    "QB": ["next_attempts", "next_passing_yards", "next_passing_tds", "next_interceptions", "next_rushing_yards", "next_rushing_tds"],
    "RB": ["next_carries", "next_rushing_yards", "next_rushing_tds", "next_targets", "next_receptions", "next_receiving_yards", "next_receiving_tds"],
    "WR": ["next_targets", "next_receptions", "next_receiving_yards", "next_receiving_tds", "next_rushing_yards", "next_rushing_tds"],
    "TE": ["next_targets", "next_receptions", "next_receiving_yards", "next_receiving_tds"],
}


def fval(value) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def fetch_csv(season: int) -> List[dict]:
    req = urllib.request.Request(URL.format(season=season), headers={"User-Agent": "FSFFL-native-projection-benchmark/1.0"})
    with urllib.request.urlopen(req, timeout=60) as response:
        return list(csv.DictReader(io.StringIO(response.read().decode("utf-8-sig"))))


def normalize_season(rows: List[dict], season: int) -> List[dict]:
    out = []
    for raw in rows:
        if raw.get("season_type") and str(raw.get("season_type")).upper() != "REG":
            continue
        pid = str(raw.get("player_id") or raw.get("gsis_id") or "").strip()
        position = str(raw.get("position") or raw.get("position_group") or "").upper().strip()
        if not pid or position not in POSITIONS:
            continue
        row = {
            "season": int(raw.get("season") or season),
            "player_id": pid,
            "player_name": str(raw.get("player_display_name") or raw.get("player_name") or "").strip(),
            "position": position,
            "team": str(raw.get("recent_team") or raw.get("team") or "").strip(),
            "games": fval(raw.get("games")),
        }
        for canonical, source_column in SOURCE_STATS.items():
            row[canonical] = fval(raw.get(source_column))
        out.append(row)
    return out


def build_team_context(season_rows: List[dict]) -> dict:
    """Build broad lagged offensive-environment features from the same governed feed.

    These are deliberately simple team-volume/tendency summaries. Because the player
    seasonal file uses recent_team, traded-player totals can introduce some team-level
    noise; this challenger is retained only if rolling holdouts show material value.
    """
    grouped = defaultdict(lambda: defaultdict(float))
    for row in season_rows:
        season = int(row["season"])
        team = str(row.get("team") or "").strip()
        if not team:
            continue
        key = (season, team)
        grouped[key]["pass_attempts"] += fval(row.get("attempts"))
        grouped[key]["rush_attempts"] += fval(row.get("carries"))
        grouped[key]["targets"] += fval(row.get("targets"))
        grouped[key]["rushing_tds"] += fval(row.get("rushing_tds"))
        grouped[key]["receiving_tds"] += fval(row.get("receiving_tds"))
    out = {}
    for key, values in grouped.items():
        opportunities = values["pass_attempts"] + values["rush_attempts"]
        out[key] = {
            "team_lag1_pass_attempts": values["pass_attempts"],
            "team_lag1_rush_attempts": values["rush_attempts"],
            "team_lag1_targets": values["targets"],
            "team_lag1_offensive_opportunities": opportunities,
            "team_lag1_pass_rate": values["pass_attempts"] / opportunities if opportunities else 0.0,
            "team_lag1_rushing_tds": values["rushing_tds"],
            "team_lag1_receiving_tds": values["receiving_tds"],
        }
    return out


def make_lagged_rows(season_rows: List[dict]) -> List[dict]:
    """Create forecast rows using only seasons available before the target year."""
    index = {(int(r["season"]), str(r["player_id"])): r for r in season_rows}
    team_context = build_team_context(season_rows)
    max_season = max(season for season, _ in index)
    out = []
    for (season, pid), cur in sorted(index.items()):
        if season >= max_season:
            continue
        nxt = index.get((season + 1, pid))
        prev = index.get((season - 1, pid))
        next_present = nxt is not None
        lag2_available = prev is not None
        row = {
            "season": season + 1,
            "feature_season": season,
            "player_id": pid,
            "player_name": cur["player_name"],
            "position": cur["position"],
            "next_season_present": int(next_present),
            "team_change": int(bool(nxt and cur.get("team") and nxt.get("team") and cur["team"] != nxt["team"])),
            "lag1_games": fval(cur.get("games")),
            "lag2_available": int(lag2_available),
            "lag2_games": fval(prev.get("games")) if prev else 0.0,
            "next_games": fval(nxt.get("games")) if nxt else 0.0,
        }
        for name in TEAM_FEATURES:
            row[name] = fval(team_context.get((season, cur.get("team")), {}).get(name))
        for stat in STATS:
            row[f"lag1_{stat}"] = fval(cur.get(stat))
            row[f"lag2_{stat}"] = fval(prev.get(stat)) if prev else 0.0
            row[f"next_{stat}"] = fval(nxt.get(stat)) if nxt else 0.0
        out.append(row)
    return out


def run(start_season: int, end_season: int) -> dict:
    all_seasons = []
    source_counts = {}
    for season in range(start_season, end_season + 1):
        raw = fetch_csv(season)
        normalized = normalize_season(raw, season)
        if not normalized:
            raise ValueError(f"{season}: no QB/RB/WR/TE player rows after normalization; source schema may have changed")
        missing_source_stats = [c for c in SOURCE_STATS.values() if c not in raw[0]] if raw else list(SOURCE_STATS.values())
        if missing_source_stats:
            raise ValueError(f"{season}: missing required nflverse columns: {missing_source_stats}")
        all_seasons.extend(normalized)
        source_counts[str(season)] = {"raw_rows": len(raw), "eligible_player_seasons": len(normalized), "columns": sorted(raw[0].keys()) if raw else []}

    lagged = make_lagged_rows(all_seasons)
    reports = {p: temporal_holdout(lagged, p, FEATURES[p], TARGETS[p]) for p in ("QB", "RB", "WR", "TE")}
    summary = {}
    for position, report in reports.items():
        target_results = list(report["targets"].values())
        with_persistence = [r for r in target_results if "persistence_baseline_mae" in r]
        pos_holdout = [r for r in lagged if r["position"] == position and int(r["season"]) == report["holdout_season"]]
        summary[position] = {
            "holdout_season": report["holdout_season"],
            "train_n": report["train_n"],
            "holdout_n": report["holdout_n"],
            "holdout_next_season_present_n": sum(r["next_season_present"] for r in pos_holdout),
            "holdout_attrition_n": sum(1 - r["next_season_present"] for r in pos_holdout),
            "target_count": len(target_results),
            "targets_beating_mean_baseline": sum(r["model_mae"] < r["mean_train_baseline_mae"] for r in target_results),
            "targets_beating_persistence": sum(bool(r.get("beats_persistence")) for r in with_persistence),
            "targets_with_persistence_baseline": len(with_persistence),
            "mean_improvement_vs_mean_baseline_pct": sum(r["improvement_vs_mean_baseline_pct"] for r in target_results) / len(target_results),
            "mean_improvement_vs_persistence_pct": sum(r["improvement_vs_persistence_pct"] for r in with_persistence) / len(with_persistence) if with_persistence else None,
        }

    return {
        "schema_version": "1.5",
        "status": "PASS",
        "source": {"provider": "nflverse/nflverse-data", "release_tag": "stats_player", "asset_family": "stats_player_reg_{season}.csv", "url_template": URL, "attribution": "nflverse community data; verify dataset-specific license and attribution requirements before production redistribution"},
        "seasons_requested": [start_season, end_season],
        "source_counts": source_counts,
        "lagged_example_rows": len(lagged),
        "coverage_note": "Returning players plus prior-season players with zero next-season NFL production are evaluated. Rookies still require a separate projection path before production use.",
        "benchmark_summary": summary,
        "position_reports": reports,
        "governance": {
            "fantasy_points_used_as_target": False,
            "future_participation_used_as_feature": False,
            "survivorship_bias_policy": "players absent in target season retained with zero realized production",
            "holdout_policy": "latest completed season in downloaded range",
            "hyperparameter_selection": "training-period temporal inner validation only",
            "primary_simple_baseline": "prior-year same-stat persistence where available",
            "multi_year_history": "lag1 plus lag2 player production, with explicit lag2 availability indicator; WR lag2 restricted to receiving volume/production because rolling validation showed lag2 rushing usage was unstable and harmful",
            "team_environment": "feature-season team pass volume, rush volume, targets, total opportunities, pass rate, and TD environment derived only from the same lagged player-stat feed; no target-season realized team outcomes are features",
            "team_environment_known_limitation": "recent_team can assign traded-player season totals to the final team, adding team-summary noise; retain only if rolling holdouts show material value",
            "population_mean_baseline_role": "sanity check only",
            "production_promoted": False
        }
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--start-season", type=int, default=2016)
    p.add_argument("--end-season", type=int, default=2025)
    p.add_argument("--output", type=Path, default=Path("data/model_validation/native_projection_nflverse_benchmark.json"))
    args = p.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        result = run(args.start_season, args.end_season)
        args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps({"status": "PASS", "summary": result["benchmark_summary"], "output": str(args.output)}, indent=2))
    except Exception as exc:
        failure = {"schema_version": "1.5", "status": "FAIL", "error_type": type(exc).__name__, "error": str(exc), "traceback": traceback.format_exc(), "seasons_requested": [args.start_season, args.end_season], "source_url_template": URL}
        args.output.write_text(json.dumps(failure, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps(failure, indent=2), file=sys.stderr)
        raise


if __name__ == "__main__":
    main()
