#!/usr/bin/env python3
"""Build leakage-safe next-season rows from nflverse-style seasonal stats CSV.

The transformer is deliberately source-column based and does not know fantasy
scoring. Each output row contains only information from season t as features and
season t+1 realized football statistics as targets.

Expected identifying columns:
  season, player_id, player_name, position
Optional context:
  age, team, games, games_started

Stat columns may include:
  passing_attempts, completions, passing_yards, passing_tds, interceptions,
  carries, rushing_yards, rushing_tds, targets, receptions,
  receiving_yards, receiving_tds

No network access is performed by this script; acquiring source data and
verifying its dataset-specific license are separate governed steps.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

ID_COLS = ["season", "player_id", "player_name", "position"]
CONTEXT = ["age", "team", "games", "games_started"]
STATS = [
    "passing_attempts", "completions", "passing_yards", "passing_tds", "interceptions",
    "carries", "rushing_yards", "rushing_tds", "targets", "receptions",
    "receiving_yards", "receiving_tds",
]


def fval(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def build(rows):
    index = {}
    for r in rows:
        season = int(r["season"])
        pid = str(r["player_id"])
        index[(season, pid)] = r
    out = []
    for (season, pid), cur in sorted(index.items()):
        nxt = index.get((season + 1, pid))
        if nxt is None:
            continue
        if str(cur.get("position") or "").upper() != str(nxt.get("position") or "").upper():
            continue
        row = {
            "season": season + 1,
            "feature_season": season,
            "player_id": pid,
            "player_name": cur.get("player_name", ""),
            "position": str(cur.get("position") or "").upper(),
        }
        for c in CONTEXT:
            if c in cur:
                row[f"lag1_{c}"] = cur.get(c, "")
        for c in STATS:
            if c in cur:
                row[f"lag1_{c}"] = fval(cur.get(c))
            if c in nxt:
                row[f"next_{c}"] = fval(nxt.get(c))
        if "games" in nxt:
            row["next_games"] = fval(nxt.get("games"))
        if cur.get("team") and nxt.get("team"):
            row["team_change"] = 1 if cur.get("team") != nxt.get("team") else 0
        out.append(row)
    return out


def self_test():
    rows = [
        {"season":"2022","player_id":"p1","player_name":"A","position":"WR","team":"AAA","games":"17","targets":"100","receptions":"65","receiving_yards":"900","receiving_tds":"6"},
        {"season":"2023","player_id":"p1","player_name":"A","position":"WR","team":"BBB","games":"16","targets":"120","receptions":"80","receiving_yards":"1100","receiving_tds":"8"},
        {"season":"2024","player_id":"p1","player_name":"A","position":"WR","team":"BBB","games":"17","targets":"130","receptions":"85","receiving_yards":"1200","receiving_tds":"9"},
    ]
    out = build(rows)
    assert len(out) == 2
    assert out[0]["feature_season"] == 2022
    assert out[0]["season"] == 2023
    assert out[0]["lag1_targets"] == 100
    assert out[0]["next_targets"] == 120
    assert out[0]["team_change"] == 1
    return {"status":"PASS","rows":len(out)}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--input", type=Path)
    p.add_argument("--output", type=Path)
    p.add_argument("--self-test", action="store_true")
    args = p.parse_args()
    if args.self_test:
        print(json.dumps(self_test(), indent=2))
        return
    if not args.input or not args.output:
        p.error("--input and --output required")
    with args.input.open(newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    out = build(rows)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fields = sorted({k for r in out for k in r})
    with args.output.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(out)
    print(json.dumps({"status":"PASS","rows":len(out),"output":str(args.output)}, indent=2))


if __name__ == "__main__":
    main()
