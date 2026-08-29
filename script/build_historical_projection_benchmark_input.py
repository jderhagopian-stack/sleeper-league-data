#!/usr/bin/env python3
"""Build leakage-safe input for the FSFFL historical projection benchmark.

This bridge deliberately separates provenance eligibility from forecasting
performance. Projection rows are admitted only when the corresponding
source/season/position snapshot is explicitly eligible in the versioned source
inventory. Same-day snapshots are excluded unless --allow-same-day is supplied.

Projection CSV required columns:
    season,source,position,projected_points
plus at least one player identifier:
    player_id or player_name

Actual CSV required columns:
    season,position,actual_points
plus at least one player identifier:
    player_id or player_name

Output columns match benchmark_historical_projections.py:
    season,source,player_id,player_name,position,projected_points,actual_points
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

ELIGIBLE = {"ELIGIBLE_PRESEASON"}
SAME_DAY = "SAME_DAY_REVIEW_REQUIRED"


def norm_name(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.lower().replace("’", "'")
    value = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b\.?", "", value)
    return re.sub(r"[^a-z0-9]+", "", value)


def read_csv(path: Path) -> List[Dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as fh:
        return [dict(row) for row in csv.DictReader(fh)]


def require_columns(rows: List[Dict[str, str]], required: Iterable[str], label: str) -> None:
    if not rows:
        raise ValueError(f"{label} CSV is empty")
    missing = [c for c in required if c not in rows[0]]
    if missing:
        raise ValueError(f"{label} CSV missing columns: {', '.join(missing)}")
    if not any("player_id" in r and str(r.get("player_id") or "").strip() for r in rows) and not any(
        "player_name" in r and str(r.get("player_name") or "").strip() for r in rows
    ):
        raise ValueError(f"{label} CSV needs player_id or player_name")


def load_inventory(path: Path, allow_same_day: bool) -> Dict[Tuple[str, str, str], Dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    out: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    for row in payload.get("sources") or []:
        status = str(row.get("status") or "")
        allowed = status in ELIGIBLE or (allow_same_day and status == SAME_DAY)
        if not allowed:
            continue
        key = (
            str(row.get("source") or "").strip().lower(),
            str(row.get("season") or "").strip(),
            str(row.get("position") or "").strip().upper(),
        )
        out[key] = row
    return out


def player_key(row: Dict[str, str]) -> Tuple[str, str, str]:
    season = str(row.get("season") or "").strip()
    position = str(row.get("position") or "").strip().upper()
    pid = str(row.get("player_id") or "").strip()
    if pid:
        return season, position, f"id:{pid}"
    name = norm_name(str(row.get("player_name") or ""))
    if not name:
        raise ValueError("Row has neither usable player_id nor player_name")
    return season, position, f"name:{name}"


def float_value(row: Dict[str, str], field: str) -> float:
    try:
        return float(str(row.get(field) or "").replace(",", "").strip())
    except ValueError as exc:
        raise ValueError(f"Invalid {field}: {row.get(field)!r}") from exc


def build(
    projections: List[Dict[str, str]],
    actuals: List[Dict[str, str]],
    eligible_inventory: Dict[Tuple[str, str, str], Dict[str, Any]],
) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    require_columns(projections, ["season", "source", "position", "projected_points"], "projection")
    require_columns(actuals, ["season", "position", "actual_points"], "actual")

    actual_index: Dict[Tuple[str, str, str], Dict[str, str]] = {}
    duplicate_actuals = []
    for row in actuals:
        key = player_key(row)
        if key in actual_index:
            duplicate_actuals.append(key)
        actual_index[key] = row
    if duplicate_actuals:
        raise ValueError(f"Duplicate actual player keys detected: {duplicate_actuals[:5]}")

    output: List[Dict[str, Any]] = []
    rejected = Counter()
    admitted = Counter()
    unmatched: List[Dict[str, Any]] = []
    seen_projection_keys = set()

    for row in projections:
        season = str(row.get("season") or "").strip()
        source = str(row.get("source") or "").strip()
        position = str(row.get("position") or "").strip().upper()
        inv_key = (source.lower(), season, position)
        if inv_key not in eligible_inventory:
            rejected["snapshot_not_eligible"] += 1
            continue

        pkey = player_key(row)
        dedupe = (source.lower(),) + pkey
        if dedupe in seen_projection_keys:
            raise ValueError(f"Duplicate projection key detected: {dedupe}")
        seen_projection_keys.add(dedupe)

        actual = actual_index.get(pkey)
        if actual is None and str(row.get("player_id") or "").strip() and str(row.get("player_name") or "").strip():
            fallback = (season, position, f"name:{norm_name(str(row.get('player_name') or ''))}")
            actual = actual_index.get(fallback)
        if actual is None:
            rejected["unmatched_actual"] += 1
            unmatched.append({
                "season": season,
                "source": source,
                "position": position,
                "player_id": str(row.get("player_id") or ""),
                "player_name": str(row.get("player_name") or ""),
            })
            continue

        projected_points = float_value(row, "projected_points")
        actual_points = float_value(actual, "actual_points")
        out = {
            "season": int(season),
            "source": source,
            "player_id": str(row.get("player_id") or actual.get("player_id") or ""),
            "player_name": str(row.get("player_name") or actual.get("player_name") or ""),
            "position": position,
            "projected_points": round(projected_points, 6),
            "actual_points": round(actual_points, 6),
        }
        output.append(out)
        admitted[(source, season, position)] += 1

    output.sort(key=lambda r: (r["season"], r["source"].lower(), r["position"], r["player_name"], r["player_id"]))

    source_seasons = defaultdict(set)
    for row in output:
        source_seasons[row["source"]].add(row["season"])

    diagnostics = {
        "schema_version": "1.0",
        "input_projection_rows": len(projections),
        "input_actual_rows": len(actuals),
        "admitted_rows": len(output),
        "rejected_rows": dict(sorted(rejected.items())),
        "admitted_by_source_season_position": {
            "|".join(map(str, key)): count for key, count in sorted(admitted.items())
        },
        "source_season_coverage": {k: sorted(v) for k, v in sorted(source_seasons.items())},
        "unmatched_actual_examples": unmatched[:50],
        "authoritative_ready_for_source_comparison": len(source_seasons) >= 2 and all(len(v) >= 2 for v in source_seasons.values()),
        "note": "Readiness here only means at least two sources each span at least two admitted seasons; benchmark holdout and matched-cohort promotion rules still govern model changes.",
    }
    return output, diagnostics


def write_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["season", "source", "player_id", "player_name", "position", "projected_points", "actual_points"]
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def self_test() -> None:
    inventory = {
        ("alpha", "2021", "QB"): {"status": "ELIGIBLE_PRESEASON"},
        ("beta", "2021", "QB"): {"status": "ELIGIBLE_PRESEASON"},
    }
    projections = [
        {"season": "2021", "source": "Alpha", "player_id": "1", "player_name": "Test QB", "position": "QB", "projected_points": "300"},
        {"season": "2021", "source": "Beta", "player_id": "1", "player_name": "Test QB", "position": "QB", "projected_points": "310"},
        {"season": "2021", "source": "Alpha", "player_id": "2", "player_name": "Blocked RB", "position": "RB", "projected_points": "100"},
    ]
    actuals = [
        {"season": "2021", "player_id": "1", "player_name": "Test QB", "position": "QB", "actual_points": "305"},
        {"season": "2021", "player_id": "2", "player_name": "Blocked RB", "position": "RB", "actual_points": "90"},
    ]
    rows, diag = build(projections, actuals, inventory)
    assert len(rows) == 2
    assert rows[0]["actual_points"] == 305.0
    assert diag["rejected_rows"]["snapshot_not_eligible"] == 1
    assert diag["authoritative_ready_for_source_comparison"] is False
    print("historical projection benchmark input self-test: PASS")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--projections", type=Path)
    parser.add_argument("--actuals", type=Path)
    parser.add_argument("--inventory", type=Path, default=Path("data/model_validation/historical_projection_source_inventory.json"))
    parser.add_argument("--output", type=Path, default=Path("data/model_validation/historical_projection_benchmark_input.csv"))
    parser.add_argument("--diagnostics", type=Path, default=Path("data/model_validation/historical_projection_benchmark_input_diagnostics.json"))
    parser.add_argument("--allow-same-day", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        self_test()
        return
    if not args.projections or not args.actuals:
        parser.error("--projections and --actuals are required unless --self-test is used")

    inventory = load_inventory(args.inventory, args.allow_same_day)
    rows, diagnostics = build(read_csv(args.projections), read_csv(args.actuals), inventory)
    write_csv(args.output, rows)
    args.diagnostics.parent.mkdir(parents=True, exist_ok=True)
    args.diagnostics.write_text(json.dumps(diagnostics, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {len(rows)} benchmark rows to {args.output}")
    print(f"wrote diagnostics to {args.diagnostics}")


if __name__ == "__main__":
    main()
