#!/usr/bin/env python3
"""Run the first real-data benchmark for the FSFFL-native projection challenger.

This script downloads nflverse player_stats CSV releases, aggregates regular-
season weekly football statistics to player-season rows, creates strictly
lagged next-season examples, and evaluates position-specific ridge models on a
latest-season temporal holdout.

Important governance properties:
- targets are underlying football statistics, never fantasy points;
- season t features are used only to predict season t+1 outcomes;
- alpha selection occurs inside the training period;
- the latest available completed season is held out entirely;
- no external fantasy projection source is used.

Data source: nflverse/nflverse-data player_stats GitHub release.
The nflverse data ecosystem documents the relevant open datasets as broadly
CC-BY 4.0; this script records attribution in its output and does not redistribute
raw source files in the repository.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import sys
import traceback
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, Iterable, List

sys.path.insert(0, str(Path(__file__).resolve().parent))
from native_projection_challenger import temporal_holdout  # noqa: E402

URL = "https://github.com/nflverse/nflverse-data/releases/download/player_stats/player_stats_{season}.csv"
POSITIONS = {"QB", "RB", "WR", "TE"}
SUM_STATS = [
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
    req = urllib.request.Request(URL.format(season=season), headers={"User-Agent": "FSFFL-native-projection-benchmark/1.0"})
    with urllib.request.urlopen(req, timeout=60) as response:
        text = response.read().decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(text)))


def aggregate_season(rows: Iterable[dict], season: int) -> List[dict]:
    """Aggregate weekly regular-season nflverse rows to player-season totals."""
    grouped: Dict[str, dict] = {}
    weeks = defaultdict(set)
    position_votes = defaultdict(Counter)
    team_votes = defaultdict(Counter)

    for raw in rows:
        if raw.get("season_type") and str(raw.get("season_type")).upper() != "REG":
            continue
        pid = str(raw.get("player_id") or raw.get("gsis_id") or "").strip()
        if not pid:
            continue
        position = str(raw.get("position") or raw.get("position_group") or "").upper().strip()
        if position not in POSITIONS:
            continue
        name = str(raw.get("player_display_name") or raw.get("player_name") or "").strip()
        team = str(raw.get("recent_team") or raw.get("team") or "").strip()
        if pid not in grouped:
            grouped[pid] = {
                "season": season,
                "player_id": pid,
                "player_name": name,
                "position": position,
                "team": team,
                **{stat: 0.0 for stat in SUM_STATS},
            }
        position_votes[pid][position] += 1
        if team:
            team_votes[pid][team] += 1
        week = raw.get("week")
        if week not in (None, ""):
            weeks[pid].add(str(week))
        for stat in SUM_STATS:
            grouped[pid][stat] += fval(raw.get(stat))

    out = []
    for pid, row in grouped.items():
        row["position"] = position_votes[pid].most_common(1)[0][0]
        if team_votes[pid]:
            row["team"] = team_votes[pid].most_common(1)[0][0]
        row["games"] = float(len(weeks[pid])) if weeks[pid] else fval(row.get("games"))
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
        for stat in SUM_STATS:
            row[f"lag1_{stat}"] = fval(cur.get(stat))
            row[f"next_{stat}"] = fval(nxt.get(stat))
        out.append(row)
    return out


def run(start_season: int, end_season: int) -> dict:
    all_seasons = []
    source_counts = {}
    for season in range(start_season, end_season + 1):
        raw = fetch_csv(season)
        aggregated = aggregate_season(raw, season)
        all_seasons.extend(aggregated)
        source_counts[str(season)] = {"raw_rows": len(raw), "player_seasons": len(aggregated)}

    lagged = make_lagged_rows(all_seasons)
    reports = {}
    for position in ("QB", "RB", "WR", "TE"):
        reports[position] = temporal_holdout(lagged, position, FEATURES[position], TARGETS[position])

    summary = {}
    for position, report in reports.items():
        target_results = list(report["targets"].values())
        summary[position] = {
            "holdout_season": report["holdout_season"],
            "train_n": report["train_n"],
            "holdout_n": report["holdout_n"],
            "targets_beating_mean_baseline": sum(r["model_mae"] < r["mean_train_baseline_mae"] for r in target_results),
            "target_count": len(target_results),
            "mean_improvement_vs_mean_baseline_pct": sum(r["improvement_vs_mean_baseline_pct"] for r in target_results) / len(target_results),
        }

    return {
        "schema_version": "1.0",
        "status": "PASS",
        "source": {
            "provider": "nflverse/nflverse-data",
            "release": "player_stats",
            "url_template": URL,
            "attribution": "nflverse community data; retain source attribution and verify dataset-specific license before production distribution",
        },
        "seasons_requested": [start_season, end_season],
        "source_counts": source_counts,
        "lagged_example_rows": len(lagged),
        "benchmark_summary": summary,
        "position_reports": reports,
        "governance": {
            "fantasy_points_used_as_target": False,
            "holdout_policy": "latest completed season in downloaded range",
            "hyperparameter_selection": "training-period temporal inner validation only",
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
            "schema_version": "1.0",
            "status": "FAIL",
            "error_type": type(exc).__name__,
            "error": str(exc),
            "traceback": traceback.format_exc(),
            "seasons_requested": [args.start_season, args.end_season],
        }
        args.output.write_text(json.dumps(failure, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps(failure, indent=2), file=sys.stderr)
        raise


if __name__ == "__main__":
    main()
