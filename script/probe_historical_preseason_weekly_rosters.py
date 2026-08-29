#!/usr/bin/env python3
"""Inspect nflverse weekly-roster files for a leakage-safe pre-Week-1 state."""
from __future__ import annotations

import csv
import io
import json
import urllib.request
from collections import Counter
from pathlib import Path

URL = "https://github.com/nflverse/nflverse-data/releases/download/weekly_rosters/roster_weekly_{season}.csv"
SEASONS = (2021, 2022, 2024)


def fetch(season: int) -> list[dict]:
    req = urllib.request.Request(URL.format(season=season), headers={"User-Agent":"FSFFL-preseason-provenance/1.0"})
    with urllib.request.urlopen(req, timeout=90) as r:
        return list(csv.DictReader(io.StringIO(r.read().decode("utf-8-sig"))))


def summarize(season: int, rows: list[dict]) -> dict:
    if not rows:
        return {"season": season, "status": "EMPTY"}
    cols = list(rows[0])
    week_col = next((c for c in ("week","week_number") if c in cols), None)
    season_type_col = next((c for c in ("game_type","season_type") if c in cols), None)
    ts_col = next((c for c in ("dt","timestamp","date","roster_date") if c in cols), None)
    weeks = Counter(str(r.get(week_col) or "") for r in rows) if week_col else Counter()
    season_types = Counter(str(r.get(season_type_col) or "") for r in rows) if season_type_col else Counter()
    ts = sorted({str(r.get(ts_col) or "") for r in rows if r.get(ts_col)}) if ts_col else []
    explicit_pre = [r for r in rows if season_type_col and str(r.get(season_type_col) or "").upper() in {"PRE","PRESEASON"}]
    week0 = [r for r in rows if week_col and str(r.get(week_col) or "").strip() in {"0","PRE","PRESEASON"}]
    return {
        "season":season,
        "status":"PASS",
        "row_count":len(rows),
        "columns":cols,
        "week_column":week_col,
        "week_values":sorted(weeks),
        "season_type_column":season_type_col,
        "season_type_counts":dict(sorted(season_types.items())),
        "timestamp_column":ts_col,
        "timestamp_min":ts[0] if ts else None,
        "timestamp_max":ts[-1] if ts else None,
        "explicit_preseason_rows":len(explicit_pre),
        "week0_rows":len(week0),
        "candidate_preseason_rows":len(explicit_pre or week0),
        "demonstrably_pre_regular_season":bool(explicit_pre or week0),
        "provenance_rule_candidate":(
            "Use latest explicit PRE/PRESEASON roster snapshot" if explicit_pre else
            "Use week 0 roster snapshot" if week0 else
            "UNRESOLVED"
        ),
    }


def main() -> None:
    out={"schema_version":"1.0","purpose":"Test whether weekly rosters expose an explicit pre-Week-1 snapshot.","seasons":[]}
    for season in SEASONS:
        out["seasons"].append(summarize(season,fetch(season)))
    out["all_seasons_resolved"]=all(x.get("demonstrably_pre_regular_season") for x in out["seasons"])
    path=Path("data/model_validation/preseason_weekly_roster_provenance_probe.json")
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(out,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print(json.dumps(out,indent=2,sort_keys=True))

if __name__ == "__main__":
    main()
