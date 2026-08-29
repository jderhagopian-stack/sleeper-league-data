#!/usr/bin/env python3
"""Leakage-safe benchmark for historical FSFFL projection sources.

Input CSV columns:
  season, player_key, position, source, projected_points, actual_points
Optional:
  snapshot_date

The script compares each source, an equal-weight blend, and (when exactly two
sources share at least three seasons) a two-source weight learned ONLY on
pre-holdout seasons and evaluated on the latest season with common coverage.
It also measures whether source disagreement predicts larger blend errors.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path

REQUIRED = {
    "season", "player_key", "position", "source",
    "projected_points", "actual_points",
}


def fnum(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def mean(values):
    values = list(values)
    return sum(values) / len(values) if values else None


def median(values):
    values = list(values)
    return statistics.median(values) if values else None


def pearson(xs, ys):
    pairs = [(float(x), float(y)) for x, y in zip(xs, ys)]
    if len(pairs) < 3:
        return None
    xbar = mean(x for x, _ in pairs)
    ybar = mean(y for _, y in pairs)
    num = sum((x - xbar) * (y - ybar) for x, y in pairs)
    dx = math.sqrt(sum((x - xbar) ** 2 for x, _ in pairs))
    dy = math.sqrt(sum((y - ybar) ** 2 for _, y in pairs))
    if dx == 0 or dy == 0:
        return None
    return num / (dx * dy)


def average_ranks(values):
    indexed = sorted(enumerate(values), key=lambda pair: pair[1])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(indexed):
        j = i
        while j + 1 < len(indexed) and indexed[j + 1][1] == indexed[i][1]:
            j += 1
        rank = (i + j + 2) / 2.0
        for k in range(i, j + 1):
            ranks[indexed[k][0]] = rank
        i = j + 1
    return ranks


def spearman(xs, ys):
    if len(xs) < 3 or len(xs) != len(ys):
        return None
    return pearson(average_ranks(xs), average_ranks(ys))


def metrics(rows):
    if not rows:
        return None
    errors = [r["projection"] - r["actual"] for r in rows]
    projections = [r["projection"] for r in rows]
    actuals = [r["actual"] for r in rows]
    return {
        "n": len(rows),
        "mae": mean(abs(e) for e in errors),
        "median_absolute_error": median(abs(e) for e in errors),
        "bias_projection_minus_actual": mean(errors),
        "rank_correlation_spearman": spearman(projections, actuals),
    }


def load_rows(path):
    with Path(path).open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        missing = REQUIRED - set(reader.fieldnames or [])
        if missing:
            raise SystemExit(f"missing required columns: {sorted(missing)}")
        out = []
        for raw in reader:
            projected = fnum(raw.get("projected_points"))
            actual = fnum(raw.get("actual_points"))
            try:
                season = int(raw.get("season", ""))
            except ValueError:
                continue
            if projected is None or actual is None:
                continue
            out.append({
                "season": season,
                "player_key": raw["player_key"].strip(),
                "position": raw["position"].strip().upper(),
                "source": raw["source"].strip(),
                "projection": projected,
                "actual": actual,
                "snapshot_date": (raw.get("snapshot_date") or "").strip(),
            })
        return out


def grouped_source_metrics(rows):
    by_source = defaultdict(list)
    by_source_position = defaultdict(list)
    for row in rows:
        by_source[row["source"]].append(row)
        by_source_position[(row["source"], row["position"])].append(row)
    return {
        "overall": {source: metrics(rs) for source, rs in sorted(by_source.items())},
        "by_position": {
            f"{source}:{position}": metrics(rs)
            for (source, position), rs in sorted(by_source_position.items())
        },
    }


def aligned(rows):
    groups = defaultdict(list)
    for row in rows:
        key = (row["season"], row["player_key"], row["position"])
        groups[key].append(row)
    return groups


def blend_rows(rows, allowed_sources=None, weights=None, seasons=None):
    output = []
    for (season, player_key, position), group in aligned(rows).items():
        if seasons is not None and season not in seasons:
            continue
        candidates = [r for r in group if allowed_sources is None or r["source"] in allowed_sources]
        seen = {r["source"] for r in candidates}
        if allowed_sources is not None and not set(allowed_sources).issubset(seen):
            continue
        if len(candidates) < 2:
            continue
        actuals = {round(r["actual"], 8) for r in candidates}
        if len(actuals) != 1:
            raise SystemExit(f"actual_points mismatch for {season}/{player_key}/{position}")
        if weights:
            denom = sum(weights[r["source"]] for r in candidates)
            projection = sum(weights[r["source"]] * r["projection"] for r in candidates) / denom
        else:
            projection = mean(r["projection"] for r in candidates)
        projections = [r["projection"] for r in candidates]
        output.append({
            "season": season,
            "player_key": player_key,
            "position": position,
            "projection": projection,
            "actual": candidates[0]["actual"],
            "spread": max(projections) - min(projections),
        })
    return output


def common_seasons_for_sources(rows, sources):
    """Return seasons with at least one player-position covered by every source."""
    sources = set(sources)
    covered = defaultdict(set)
    for (season, _, _), group in aligned(rows).items():
        seen = {r["source"] for r in group}
        if sources.issubset(seen):
            covered[season].add(tuple(sorted(sources)))
    return sorted(season for season, matches in covered.items() if matches)


def learn_two_source_weight(rows, sources, train_seasons):
    a, b = sources
    best = None
    for step in range(21):
        wa = step / 20.0
        weights = {a: wa, b: 1.0 - wa}
        candidate = blend_rows(rows, sources, weights, train_seasons)
        result = metrics(candidate)
        if not result:
            continue
        score = result["mae"]
        if best is None or score < best["train_mae"]:
            best = {"weights": weights, "train_mae": score, "train_n": result["n"]}
    return best


def benchmark(rows):
    seasons = sorted({r["season"] for r in rows})
    sources = sorted({r["source"] for r in rows})
    result = {
        "seasons": seasons,
        "sources": sources,
        "source_metrics": grouped_source_metrics(rows),
    }

    equal = blend_rows(rows)
    result["equal_weight_multi_source_blend"] = metrics(equal)
    result["disagreement_signal"] = {
        "n": len(equal),
        "pearson_spread_vs_absolute_error": pearson(
            [r["spread"] for r in equal],
            [abs(r["projection"] - r["actual"]) for r in equal],
        ),
    }

    if len(sources) == 2:
        common_seasons = common_seasons_for_sources(rows, sources)
        result["common_source_seasons"] = common_seasons
        if len(common_seasons) >= 3:
            holdout = common_seasons[-1]
            train = set(common_seasons[:-1])
            learned = learn_two_source_weight(rows, sources, train)
            if learned:
                test_rows = blend_rows(rows, sources, learned["weights"], {holdout})
                equal_test = blend_rows(rows, sources, None, {holdout})
                if test_rows and equal_test:
                    result["temporal_holdout"] = {
                        "train_seasons": sorted(train),
                        "holdout_season": holdout,
                        "learned_weights": learned["weights"],
                        "learned_train_mae": learned["train_mae"],
                        "learned_holdout": metrics(test_rows),
                        "equal_weight_holdout": metrics(equal_test),
                    }
    return result


def self_test():
    rows = []
    for season in (2021, 2022, 2023):
        for i in range(1, 9):
            actual = 100 + i * 10 + (season - 2021) * 2
            rows.append({"season": season, "player_key": f"p{i}", "position": "WR", "source": "A", "projection": actual + 4, "actual": actual, "snapshot_date": ""})
            rows.append({"season": season, "player_key": f"p{i}", "position": "WR", "source": "B", "projection": actual + 12, "actual": actual, "snapshot_date": ""})
    out = benchmark(rows)
    assert out["source_metrics"]["overall"]["A"]["mae"] == 4
    assert out["source_metrics"]["overall"]["B"]["mae"] == 12
    assert out["equal_weight_multi_source_blend"]["mae"] == 8
    assert out["temporal_holdout"]["holdout_season"] == 2023
    assert out["temporal_holdout"]["learned_weights"]["A"] == 1.0

    # A later single-source season must never become the temporal holdout.
    rows.append({"season": 2024, "player_key": "p1", "position": "WR", "source": "A", "projection": 150, "actual": 145, "snapshot_date": ""})
    out = benchmark(rows)
    assert out["common_source_seasons"] == [2021, 2022, 2023]
    assert out["temporal_holdout"]["holdout_season"] == 2023
    return {"status": "PASS", "checks": 7}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input_csv", nargs="?")
    parser.add_argument("--output", default="data/model_validation/historical_projection_benchmark.json")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        print(json.dumps(self_test(), indent=2))
        return
    if not args.input_csv:
        raise SystemExit("input_csv is required unless --self-test is used")
    rows = load_rows(args.input_csv)
    if not rows:
        raise SystemExit("no valid benchmark rows")
    result = benchmark(rows)
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"status": "PASS", "rows": len(rows), "output": str(target)}, indent=2))


if __name__ == "__main__":
    main()
