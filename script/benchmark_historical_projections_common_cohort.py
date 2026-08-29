#!/usr/bin/env python3
"""Matched-cohort guard for historical projection source comparison.

This wrapper prevents a major benchmark bias: comparing sources on different
player samples. It augments the existing historical projection benchmark with
metrics computed only on players covered by every compared source, plus
pairwise head-to-head metrics when more than two sources are present.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

import benchmark_historical_projections as base


def common_source_rows(rows, sources):
    sources = tuple(sorted(sources))
    groups = base.aligned(rows)
    out = defaultdict(list)
    for _, group in groups.items():
        by_source = {r["source"]: r for r in group}
        if not all(source in by_source for source in sources):
            continue
        actuals = {round(by_source[source]["actual"], 8) for source in sources}
        if len(actuals) != 1:
            raise SystemExit("actual_points mismatch in common cohort")
        for source in sources:
            row = dict(by_source[source])
            out[source].append(row)
    return out


def matched_metrics(rows, sources):
    common = common_source_rows(rows, sources)
    return {
        "sources": list(sorted(sources)),
        "common_player_seasons": len(next(iter(common.values()))) if common else 0,
        "metrics": {source: base.metrics(source_rows) for source, source_rows in sorted(common.items())},
    }


def pairwise_matched(rows, sources):
    output = {}
    ordered = sorted(sources)
    for i, left in enumerate(ordered):
        for right in ordered[i + 1:]:
            key = f"{left}__vs__{right}"
            output[key] = matched_metrics(rows, [left, right])
    return output


def coverage(rows):
    unique_player_seasons = defaultdict(set)
    for row in rows:
        unique_player_seasons[row["source"]].add((row["season"], row["player_key"], row["position"]))
    return {source: len(keys) for source, keys in sorted(unique_player_seasons.items())}


def benchmark(rows):
    result = base.benchmark(rows)
    sources = sorted({r["source"] for r in rows})
    result["coverage_player_seasons"] = coverage(rows)
    result["all_source_common_cohort"] = matched_metrics(rows, sources)
    result["pairwise_common_cohorts"] = pairwise_matched(rows, sources)
    result["comparison_policy"] = (
        "Use common-cohort metrics for source/blend promotion decisions; raw source metrics are descriptive only "
        "because source coverage can differ."
    )
    return result


def self_test():
    rows = []
    for season in (2021, 2022, 2023):
        for i in range(1, 9):
            actual = 100 + i * 10 + (season - 2021) * 2
            rows.append({"season": season, "player_key": f"p{i}", "position": "WR", "source": "A", "projection": actual + 4, "actual": actual, "snapshot_date": ""})
            rows.append({"season": season, "player_key": f"p{i}", "position": "WR", "source": "B", "projection": actual + 12, "actual": actual, "snapshot_date": ""})
        # Extra A-only player would make a naive source comparison use different samples.
        rows.append({"season": season, "player_key": "a_only", "position": "WR", "source": "A", "projection": 200, "actual": 0, "snapshot_date": ""})
    out = benchmark(rows)
    common = out["all_source_common_cohort"]
    assert common["common_player_seasons"] == 24
    assert common["metrics"]["A"]["mae"] == 4
    assert common["metrics"]["B"]["mae"] == 12
    assert out["coverage_player_seasons"]["A"] == 27
    assert out["coverage_player_seasons"]["B"] == 24
    assert out["source_metrics"]["overall"]["A"]["mae"] != 4
    return {"status": "PASS", "checks": 5}


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
    rows = base.load_rows(args.input_csv)
    if not rows:
        raise SystemExit("no valid benchmark rows")
    result = benchmark(rows)
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"status": "PASS", "rows": len(rows), "output": str(target)}, indent=2))


if __name__ == "__main__":
    main()
