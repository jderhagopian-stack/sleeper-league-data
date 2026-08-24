#!/usr/bin/env python3
"""Diagnose comprehensive FSFFL historical weekly scoring artifacts.

The large player_weekly_fsffl.json files are difficult to inspect through the
GitHub file viewer. This script parses the artifact in CI, reports its structural
shape, and runs the exact tolerant parser used by the historical usage policy.
It is diagnostic/validation only and writes no canonical data.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict

import alternate_history_engine as ah
from run_fsffl_historical_usage_policy import _parse_weekly_payload

DATA = Path("data")


def describe(value: Any, depth: int = 0) -> Dict[str, Any]:
    out: Dict[str, Any] = {"type": type(value).__name__}
    if isinstance(value, dict):
        keys = list(value.keys())
        out["length"] = len(keys)
        out["sample_keys"] = [str(x) for x in keys[:8]]
        if keys and depth < 2:
            key = keys[0]
            out["first_value"] = describe(value[key], depth + 1)
    elif isinstance(value, list):
        out["length"] = len(value)
        if value and depth < 2:
            out["first_value"] = describe(value[0], depth + 1)
            if isinstance(value[0], dict):
                out["first_record_keys"] = list(value[0].keys())[:20]
    else:
        out["sample"] = value
    return out


def run(season: str) -> Dict[str, Any]:
    path = DATA / "stats" / "fsffl" / str(season) / "player_weekly_fsffl.json"
    if not path.exists():
        raise ah.AlternateHistoryError(f"Missing weekly scoring source: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    parsed = _parse_weekly_payload(payload)
    records = sum(len(rows) for rows in parsed.values())
    unique_players = sorted({str(pid) for rows in parsed.values() for pid in rows})
    populated_weeks = sorted(int(w) for w, rows in parsed.items() if rows)
    puka = {
        str(week): rows.get("9493")
        for week, rows in sorted(parsed.items())
        if "9493" in rows
    }
    result = {
        "season": str(season),
        "path": str(path),
        "bytes": path.stat().st_size,
        "raw_shape": describe(payload),
        "parser": "run_fsffl_historical_usage_policy._parse_weekly_payload",
        "parsed_week_count": len(populated_weeks),
        "parsed_weeks": populated_weeks,
        "parsed_player_week_records": records,
        "parsed_unique_players": len(unique_players),
        "sample_player_ids": unique_players[:20],
        "puka_9493_weekly_points": puka,
        "sufficient_for_cross_roster_counterfactual_scoring": bool(records and len(unique_players) > 100 and puka),
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    if not result["sufficient_for_cross_roster_counterfactual_scoring"]:
        raise ah.AlternateHistoryError(
            "Comprehensive weekly scoring source did not parse with sufficient player/week coverage"
        )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--season", default="2023")
    args = parser.parse_args()
    run(args.season)


if __name__ == "__main__":
    main()
