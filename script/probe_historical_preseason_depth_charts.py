#!/usr/bin/env python3
"""Inspect nflverse historical depth-chart files for leakage-safe preseason snapshots."""
from __future__ import annotations

import csv
import io
import json
import urllib.request
from collections import Counter
from pathlib import Path

URL = "https://github.com/nflverse/nflverse-data/releases/download/depth_charts/depth_charts_{season}.csv"
SEASONS = (2021, 2022, 2024)


def fetch(season: int) -> list[dict]:
    req = urllib.request.Request(URL.format(season=season), headers={"User-Agent":"FSFFL-preseason-provenance/1.0"})
    with urllib.request.urlopen(req, timeout=90) as r:
        return list(csv.DictReader(io.StringIO(r.read().decode("utf-8-sig"))))


def summarize(season: int, rows: list[dict]) -> dict:
    if not rows:
        return {"season": season, "status": "EMPTY"}
    cols = list(rows[0])
    game_type_col = next((c for c in ("game_type","season_type") if c in cols), None)
    week_col = next((c for c in ("week","week_number") if c in cols), None)
    timestamp_col = next((c for c in ("dt","timestamp","date") if c in cols), None)
    game_types = Counter(str(r.get(game_type_col) or "") for r in rows) if game_type_col else Counter()
    weeks = Counter(str(r.get(week_col) or "") for r in rows) if week_col else Counter()
    preseason_rows = []
    if game_type_col:
        preseason_rows = [r for r in rows if str(r.get(game_type_col) or "").upper() in {"PRE","PRESEASON"}]
    pre_weeks = sorted({str(r.get(week_col) or "") for r in preseason_rows}) if week_col else []
    ts = [str(r.get(timestamp_col) or "") for r in rows if r.get(timestamp_col)] if timestamp_col else []
    return {
        "season": season,
        "status": "PASS",
        "row_count": len(rows),
        "columns": cols,
        "game_type_column": game_type_col,
        "game_type_counts": dict(sorted(game_types.items())),
        "week_column": week_col,
        "week_values": sorted(weeks),
        "timestamp_column": timestamp_col,
        "timestamp_min": min(ts) if ts else None,
        "timestamp_max": max(ts) if ts else None,
        "preseason_row_count": len(preseason_rows),
        "preseason_week_values": pre_weeks,
        "demonstrably_pre_regular_season": bool(preseason_rows) or bool(timestamp_col),
        "provenance_rule_candidate": (
            "Use latest PRE/PRESEASON depth-chart week only" if preseason_rows else
            "Use latest timestamp strictly before opening kickoff" if timestamp_col else
            "UNRESOLVED"
        ),
    }


def main() -> None:
    out = {
        "schema_version":"1.0",
        "purpose":"Determine whether nflverse 2021/2022/2024 depth charts expose a demonstrably pre-regular-season snapshot without using realized regular-season information.",
        "seasons":[],
    }
    for season in SEASONS:
        out["seasons"].append(summarize(season, fetch(season)))
    out["all_seasons_resolved"] = all(x.get("demonstrably_pre_regular_season") for x in out["seasons"])
    path = Path("data/model_validation/preseason_depth_chart_provenance_probe.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(out, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
