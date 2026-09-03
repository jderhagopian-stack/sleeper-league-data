#!/usr/bin/env python3
"""Research-only 2014 raw-stat comparison across timestamp-qualified archive slices.

Compares CBS, ESPN, FantasyPros and FFToday on exact common player cohorts against
realized nflverse 2014 regular-season statistics. This is a one-season cross-section:
it may inform category priors and source diagnostics but cannot establish temporal
ensemble weights or production authority by itself.
"""
from __future__ import annotations

import argparse
import csv
import json
import tempfile
import urllib.request
from collections import defaultdict
from pathlib import Path

from normalize_ffa_historical_projection_archive import normalize
from run_native_projection_nflverse_benchmark import fetch_csv, normalize_season
from benchmark_projection_championship_ensemble import norm_name

SEASON = 2014
ARCHIVE_BASE = "https://raw.githubusercontent.com/FantasyFootballAnalytics/FantasyFootballAnalyticsR/master/Data/Historical%20Projections/"
FILES = {
    "CBS": "CBS-Projections-2014.csv",
    "ESPN": "ESPN-Projections-2014.csv",
    "FantasyPros": "FantasyPros-Projections-2014.csv",
    "FFToday": "FFtoday-Projections-2014.csv",
}
ACTUAL_STAT = {
    "attempts": "attempts",
    "completions": "completions",
    "passing_yards": "passing_yards",
    "passing_tds": "passing_tds",
    "interceptions": "interceptions",
    "rushing_attempts": "carries",
    "rushing_yards": "rushing_yards",
    "rushing_tds": "rushing_tds",
    "receptions": "receptions",
    "receiving_yards": "receiving_yards",
    "receiving_tds": "receiving_tds",
}


def mean(values):
    values = list(values)
    return sum(values) / len(values) if values else None


def download(url: str, target: Path):
    req = urllib.request.Request(url, headers={"User-Agent": "FSFFL-projection-research/1.0"})
    with urllib.request.urlopen(req, timeout=60) as response:
        target.write_bytes(response.read())


def build_rows(provider_rows, actual_rows):
    actual = {(r["position"], norm_name(r["player_name"])): r for r in actual_rows}
    out = []
    for row in provider_rows:
        stat = row["stat"]
        actual_field = ACTUAL_STAT.get(stat)
        if not actual_field:
            continue
        matched = actual.get((row["position"], norm_name(row["player_name"])))
        if matched is None:
            continue
        out.append({**row, "actual": float(matched.get(actual_field, 0.0))})
    return out


def compare(rows):
    categories = defaultdict(list)
    for row in rows:
        categories[(row["position"], row["stat"])].append(row)
    results = {}
    for (position, stat), group in sorted(categories.items()):
        sources = sorted({r["source"] for r in group})
        if len(sources) < 2:
            continue
        by_player = defaultdict(dict)
        for r in group:
            by_player[norm_name(r["player_name"])][r["source"]] = r
        common = [v for v in by_player.values() if all(s in v for s in sources)]
        if not common:
            continue
        maes = {}
        for source in sources:
            maes[source] = mean(abs(v[source]["projection"] - v[source]["actual"]) for v in common)
        equal_mae = mean(
            abs(mean(v[s]["projection"] for s in sources) - next(iter(v.values()))["actual"])
            for v in common
        )
        best_source = min(maes, key=maes.get)
        winner = "equal_weight" if equal_mae < maes[best_source] else best_source
        results[f"{position}|{stat}"] = {
            "common_players": len(common),
            "sources": sources,
            "mae_by_source": maes,
            "equal_weight_mae": equal_mae,
            "best_single_source": best_source,
            "winner_in_2014_cross_section": winner,
            "equal_weight_beats_best_single": equal_mae < maes[best_source],
        }
    return results


def run():
    all_rows = []
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        for source, filename in FILES.items():
            target = root / filename
            download(ARCHIVE_BASE + filename, target)
            all_rows.extend(normalize(target, source, SEASON))
    actual_rows = normalize_season(fetch_csv(SEASON), SEASON)
    matched = build_rows(all_rows, actual_rows)
    categories = compare(matched)
    source_wins = defaultdict(int)
    equal_wins = 0
    for value in categories.values():
        winner = value["winner_in_2014_cross_section"]
        if winner == "equal_weight":
            equal_wins += 1
        else:
            source_wins[winner] += 1
    return {
        "schema_version": "1.0",
        "status": "RESEARCH_ONLY_SINGLE_SEASON",
        "season": SEASON,
        "sources": sorted(FILES),
        "timestamp_qualification": "PASS_PRESEASON for these provider files per projection_archive_2014_timestamp_qualification.json",
        "production_behavior_changed": False,
        "matched_projection_rows": len(matched),
        "category_count": len(categories),
        "summary": {
            "single_source_category_wins": dict(sorted(source_wins.items())),
            "equal_weight_category_wins": equal_wins,
        },
        "categories": categories,
        "limitations": [
            "Only one season; cannot learn or validate temporal ensemble weights.",
            "Provider reuse rights remain a separate unresolved gate.",
            "Exact common-cohort matching can exclude name-mismatch or source-coverage rows.",
            "Use as category prior/diagnostic evidence only, never sole production-promotion evidence."
        ],
        "governance": {
            "raw_stats_only": True,
            "common_cohort_required": True,
            "archive_timing_verified_separately": True,
            "rights_inferred_from_public_repository": False,
            "production_authority": False,
        },
    }


def self_test():
    rows = []
    for player, actual in [("A", 100.0), ("B", 200.0), ("C", 300.0)]:
        rows.append({"position":"WR","stat":"receiving_yards","player_name":player,"source":"X","projection":actual+10,"actual":actual})
        rows.append({"position":"WR","stat":"receiving_yards","player_name":player,"source":"Y","projection":actual+30,"actual":actual})
    result = compare(rows)["WR|receiving_yards"]
    assert result["best_single_source"] == "X"
    assert result["common_players"] == 3
    print("2014 multi-source raw-stat benchmark self-test: PASS")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--output", type=Path, default=Path("data/model_validation/projection_2014_multisource_raw_stat_scorecard.json"))
    p.add_argument("--self-test", action="store_true")
    a = p.parse_args()
    if a.self_test:
        self_test()
        return
    result = run()
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "categories": result["category_count"], "output": str(a.output)}, indent=2))


if __name__ == "__main__":
    main()
