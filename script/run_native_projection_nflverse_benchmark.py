#!/usr/bin/env python3
"""Run the first real-data benchmark for the FSFFL-native projection challenger.

The benchmark downloads nflverse regular-season player summary CSVs, constructs
strictly lagged next-season examples, and evaluates position-specific ridge
models on the latest completed season. It predicts football statistics rather
than fantasy points, and it remains a challenger only.

The raw nflverse files are not committed. Source attribution is retained here;
dataset-specific licensing/redistribution terms must still be verified before
any production redistribution.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import sys
import traceback
import urllib.request
from pathlib import Path
from typing import List

sys.path.insert(0, str(Path(__file__).resolve().parent))
from native_projection_challenger import temporal_holdout  # noqa: E402

URL = "https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_reg_{season}.csv"
POSITIONS = {"QB", "RB", "WR", "TE"}
STATS = [
    "completions", "attempts", "passing_yards", "passing_tds", "interceptions",
    "carries", "rushing_yards", "rushing_tds", "targets", "receptions",
    "receiving_yards", "receiving_tds",
]

FEATURES = {
    "QB": [
        "lag1_games", "lag1_attempts", "lag1_passing_yards", "lag1_passing_tds",
        "lag1_interceptions", "lag1_carries", "lag1_rushing_yards", "lag1_rushing_tds",
    ],
    "RB": [
        "lag1_games", "lag1_carries", "lag1_rushing_yards", "lag1_rushing_tds",
        "lag1_targets", "lag1_receptions", "lag1_receiving_yards", "lag1_receiving_tds",
    ],
    "WR": [
        "lag1_games", "lag1_targets", "lag1_receptions", "lag1_receiving_yards",
        "lag1_receiving_tds", "lag1_carries", "lag1_rushing_yards", "lag1_rushing_tds",
    ],
    "TE": [
        "lag1_games", "lag1_targets", "lag1_receptions", "lag1_receiving_yards",
        "lag1_receiving_tds",
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
    req = urllib.request.Request(
        URL.format(season=season),
        headers={"User-Agent": "FSFFL-native-projection-benchmark/1.0"},
    )
    with urllib.request.urlopen(req, timeout=60) as response:
        text = response.read().decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(text)))


def normalize_season(rows: List[dict], season: int) -> List[dict]:
    """Normalize one nflverse regular-season player summary file."""
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
        for stat in STATS:
            row[stat] = fval(raw.get(stat))
        out.append(row)
    return out


def make_lagged_rows(season_rows: List[dict]) -> List[dict]:
    index = {(int(r["season"]), str(r["player_id"])): r for r in season_rows}
    out = []
    for (season, pid), cur in sorted(index.items()):
        nxt = index.get((season + 1, pid))
        if not nxt or cur["position"] != nxt["position"]:
            continue
        row = {
            "season": season + 1,
            "feature_season": season,
            "player_id": pid,
            "player_name": cur["player_name"],
            "position": cur["position"],
            "team_change": int(bool(cur.get("team") and nxt.get("team") and cur["team"] != nxt["team"])),
            "lag1_games": fval(cur.get("games")),
            "next_games": fval(nxt.get("games")),
        }
        for stat in STATS:
            row[f"lag1_{stat}"] = fval(cur.get(stat))
            row[f"next_{stat}"] = fval(nxt.get(stat))
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
        all_seasons.extend(normalized)
        source_counts[str(season)] = {
            "raw_rows": len(raw),
            "eligible_player_seasons": len(normalized),
            "columns": sorted(raw[0].keys()) if raw else [],
        }

    lagged = make_lagged_rows(all_seasons)
    reports = {}
    for position in ("QB", "RB", "WR", "TE"):
        reports[position] = temporal_holdout(lagged, position, FEATURES[position], TARGETS[position])

    summary = {}
    for position, report in reports.items():
        target_results = list(report["targets"].values())
        with_persistence = [r for r in target_results if "persistence_baseline_mae" in r]
        summary[position] = {
            "holdout_season": report["holdout_season"],
            "train_n": report["train_n"],
            "holdout_n": report["holdout_n"],
            "target_count": len(target_results),
            "targets_beating_mean_baseline": sum(r["model_mae"] < r["mean_train_baseline_mae"] for r in target_results),
            "targets_beating_persistence": sum(bool(r.get("beats_persistence")) for r in with_persistence),
            "targets_with_persistence_baseline": len(with_persistence),
            "mean_improvement_vs_mean_baseline_pct": sum(r["improvement_vs_mean_baseline_pct"] for r in target_results) / len(target_results),
            "mean_improvement_vs_persistence_pct": (
                sum(r["improvement_vs_persistence_pct"] for r in with_persistence) / len(with_persistence)
                if with_persistence else None
            ),
        }

    return {
        "schema_version": "1.1",
        "status": "PASS",
        "source": {
            "provider": "nflverse/nflverse-data",
            "release_tag": "stats_player",
            "asset_family": "stats_player_reg_{season}.csv",
            "url_template": URL,
            "attribution": "nflverse community data; verify dataset-specific license and attribution requirements before production redistribution",
        },
        "seasons_requested": [start_season, end_season],
        "source_counts": source_counts,
        "lagged_example_rows": len(lagged),
        "coverage_note": "V1 evaluates returning NFL players with a prior-season row. Rookies require a separate projection path before production use.",
        "benchmark_summary": summary,
        "position_reports": reports,
        "governance": {
            "fantasy_points_used_as_target": False,
            "holdout_policy": "latest completed season in downloaded range",
            "hyperparameter_selection": "training-period temporal inner validation only",
            "primary_simple_baseline": "prior-year same-stat persistence where available",
            "population_mean_baseline_role": "sanity check only",
            "production_promoted": False,
        },
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
        failure = {
            "schema_version": "1.1",
            "status": "FAIL",
            "error_type": type(exc).__name__,
            "error": str(exc),
            "traceback": traceback.format_exc(),
            "seasons_requested": [args.start_season, args.end_season],
            "source_url_template": URL,
        }
        args.output.write_text(json.dumps(failure, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps(failure, indent=2), file=sys.stderr)
        raise


if __name__ == "__main__":
    main()
