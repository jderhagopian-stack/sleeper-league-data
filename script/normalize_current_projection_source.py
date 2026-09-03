#!/usr/bin/env python3
"""Normalize current projection exports into FSFFL championship long form.

This utility intentionally does not scrape, authenticate to, or license any source.
It accepts a legitimately acquired CSV/JSON export and a source profile, then emits:
  season,position,player_name,team,stat,source,projection

It is research infrastructure only; normalization does not establish production rights.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

STAT_ALIASES = {
    "completions": ["completions", "cmp", "comp", "pass_cmp", "passcompletions"],
    "passing_attempts": ["passing_attempts", "att", "pass_att", "passattempts"],
    "passing_yards": ["passing_yards", "pass_yds", "passyds", "payds", "passyards"],
    "passing_tds": ["passing_tds", "pass_td", "passtd", "patd", "passtds"],
    "interceptions": ["interceptions", "int", "ints", "pass_int"],
    "rushing_attempts": ["rushing_attempts", "rush_att", "rushatt", "ruatt", "carries"],
    "rushing_yards": ["rushing_yards", "rush_yds", "rushyds", "ruyds"],
    "rushing_tds": ["rushing_tds", "rush_td", "rushtd", "rutd"],
    "targets": ["targets", "target", "tgt", "tgts"],
    "receptions": ["receptions", "rec", "recs"],
    "receiving_yards": ["receiving_yards", "rec_yds", "recyds", "reyds"],
    "receiving_tds": ["receiving_tds", "rec_td", "rectd", "retd"],
}

IDENTITY_ALIASES = {
    "player_name": ["player_name", "player", "name", "full_name"],
    "position": ["position", "pos"],
    "team": ["team", "tm", "team_abbr", "team_id"],
}


def canon(value: str) -> str:
    return "".join(ch.lower() for ch in str(value or "") if ch.isalnum() or ch == "_")


def fnum(value):
    if value in (None, "", "-", "NA", "N/A", "null"):
        return None
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def resolve(headers, aliases):
    lookup = {canon(h): h for h in headers}
    for alias in aliases:
        key = canon(alias)
        if key in lookup:
            return lookup[key]
    return None


def load_records(path: Path):
    if path.suffix.lower() == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            return payload
        for key in ("players", "projections", "data", "results"):
            if isinstance(payload.get(key), list):
                return payload[key]
        raise SystemExit("JSON input must be a list or contain players/projections/data/results list")
    with path.open(newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def normalize(records, source: str, season: int, explicit_map=None):
    if not records:
        return []
    headers = sorted({k for row in records for k in row.keys()})
    explicit_map = explicit_map or {}
    identity = {}
    for field, aliases in IDENTITY_ALIASES.items():
        identity[field] = explicit_map.get(field) or resolve(headers, aliases)
    if not identity["player_name"] or not identity["position"]:
        raise SystemExit(f"unable to resolve player/position columns from {headers}")

    stat_columns = {}
    for stat, aliases in STAT_ALIASES.items():
        column = explicit_map.get(stat) or resolve(headers, aliases)
        if column:
            stat_columns[stat] = column
    if not stat_columns:
        raise SystemExit("no supported raw-stat columns found")

    out = []
    for row in records:
        name = str(row.get(identity["player_name"], "")).strip()
        pos = str(row.get(identity["position"], "")).upper().strip()
        team = str(row.get(identity.get("team"), "")).upper().strip() if identity.get("team") else ""
        if not name or pos not in {"QB", "RB", "WR", "TE"}:
            continue
        for stat, column in stat_columns.items():
            value = fnum(row.get(column))
            if value is None:
                continue
            out.append({
                "season": season,
                "position": pos,
                "player_name": name,
                "team": team,
                "stat": stat,
                "source": source,
                "projection": value,
            })
    return out


def write_csv(rows, target: Path):
    target.parent.mkdir(parents=True, exist_ok=True)
    fields = ["season", "position", "player_name", "team", "stat", "source", "projection"]
    with target.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def self_test():
    rows = [
        {"Player": "Josh Test", "Pos": "QB", "Team": "AAA", "Att": "500", "Cmp": "330", "PaYds": "4100", "PaTD": "31", "INT": "11", "RuAtt": "80", "RuYds": "430", "RuTD": "5"},
        {"Player": "Receiver Test", "Pos": "WR", "Team": "BBB", "Tgt": "140", "Rec": "92", "RecYds": "1250", "RecTD": "8"},
    ]
    out = normalize(rows, "TEST", 2026)
    assert any(r["stat"] == "passing_yards" and r["projection"] == 4100 for r in out)
    assert any(r["stat"] == "targets" and r["projection"] == 140 for r in out)
    assert all(r["source"] == "TEST" and r["season"] == 2026 for r in out)
    print("current projection source normalizer self-test: PASS")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("input", nargs="?")
    p.add_argument("--source")
    p.add_argument("--season", type=int)
    p.add_argument("--output")
    p.add_argument("--column-map", help="optional JSON object mapping canonical fields/stats to source columns")
    p.add_argument("--self-test", action="store_true")
    a = p.parse_args()
    if a.self_test:
        self_test()
        return
    if not all([a.input, a.source, a.season, a.output]):
        p.error("input --source --season --output required unless --self-test")
    mapping = json.loads(a.column_map) if a.column_map else None
    rows = normalize(load_records(Path(a.input)), a.source, a.season, mapping)
    write_csv(rows, Path(a.output))
    print(json.dumps({"status": "PASS", "source": a.source, "rows": len(rows), "output": a.output}))


if __name__ == "__main__":
    main()
