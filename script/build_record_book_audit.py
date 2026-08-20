#!/usr/bin/env python3
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OUT = DATA / "record_book"
SEASONS = ("2022", "2023", "2024", "2025")
EPS = 0.011
OUT.mkdir(parents=True, exist_ok=True)

def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def write_json(name, payload):
    with open(OUT / name, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)

def player_rows(season):
    raw = load_json(DATA / "stats" / "fsffl" / season / "player_season_fsffl.json")
    if isinstance(raw, list):
        return raw
    rows = []
    for pid, row in raw.items():
        if isinstance(row, dict):
            r = dict(row)
            r.setdefault("player_id", str(pid))
            rows.append(r)
    return rows

def build():
    meta = {}
    career = defaultdict(float)
    by_season = defaultdict(dict)
    single_season = []

    for season in SEASONS:
        for row in player_rows(season):
            pid = str(row.get("player_id") or "")
            if not pid:
                continue
            name = row.get("player_name") or row.get("full_name")
            pos = row.get("position")
            pts = float(row.get("fsffl_points") or 0)
            meta[pid] = {"player_name": name, "position": pos}
            career[pid] += pts
            by_season[pid][season] = round(pts, 3)
            single_season.append({
                "season": season, "player_id": pid, "player_name": name,
                "position": pos, "fsffl_points": round(pts, 3)
            })

    career_rows = [{
        "player_id": pid,
        "player_name": meta.get(pid, {}).get("player_name"),
        "position": meta.get(pid, {}).get("position"),
        "career_fsffl_points": round(pts, 3),
        "points_by_season": by_season[pid],
    } for pid, pts in career.items()]
    career_rows.sort(key=lambda x: (-x["career_fsffl_points"], x["player_name"] or ""))
    single_season.sort(key=lambda x: (-x["fsffl_points"], x["player_name"] or ""))

    rostered = defaultdict(lambda: {"points": 0.0, "weeks": 0, "started": 0.0, "starts": 0})
    started = defaultdict(lambda: {"points": 0.0, "starts": 0})
    checks = []

    for season in SEASONS:
        raw = load_json(DATA / "stats" / "fsffl" / season / "league_matchups_raw.json")
        for week, records in raw.items():
            for rec in records:
                rid = str(rec.get("roster_id") or "")
                players = [str(x) for x in (rec.get("players") or []) if x is not None]
                starters = [str(x) for x in (rec.get("starters") or []) if x is not None]
                pp = {str(k): float(v or 0) for k, v in (rec.get("players_points") or {}).items()}
                sp = rec.get("starters_points") or []
                sp_map = dict(zip(starters, [float(x or 0) for x in sp])) if len(sp) == len(starters) else {p: pp.get(p, 0) for p in starters}

                for pid in players:
                    key = (season, rid, pid)
                    rostered[key]["points"] += pp.get(pid, 0)
                    rostered[key]["weeks"] += 1

                for pid in starters:
                    pts = sp_map.get(pid, pp.get(pid, 0))
                    key = (season, rid, pid)
                    started[key]["points"] += pts
                    started[key]["starts"] += 1
                    rostered[key]["started"] += pts
                    rostered[key]["starts"] += 1

                official = float(rec.get("points") or 0)
                derived = sum(sp_map.values())
                checks.append({
                    "season": season, "week": str(week), "roster_id": rid,
                    "official_lineup_points": round(official, 3),
                    "derived_starter_points": round(derived, 3),
                    "difference": round(derived - official, 3),
                })

    rostered_rows = []
    for (season, rid, pid), a in rostered.items():
        rostered_rows.append({
            "season": season, "roster_id": rid, "player_id": pid,
            "player_name": meta.get(pid, {}).get("player_name"),
            "position": meta.get(pid, {}).get("position"),
            "fsffl_points_while_rostered": round(a["points"], 3),
            "weeks_rostered": a["weeks"],
            "fsffl_points_while_started": round(a["started"], 3),
            "starts": a["starts"],
            "bench_points": round(a["points"] - a["started"], 3),
        })
    rostered_rows.sort(key=lambda x: (-x["fsffl_points_while_rostered"], x["player_name"] or ""))

    started_rows = []
    for (season, rid, pid), a in started.items():
        started_rows.append({
            "season": season, "roster_id": rid, "player_id": pid,
            "player_name": meta.get(pid, {}).get("player_name"),
            "position": meta.get(pid, {}).get("position"),
            "fsffl_points_while_started": round(a["points"], 3),
            "starts": a["starts"],
        })
    started_rows.sort(key=lambda x: (-x["fsffl_points_while_started"], x["player_name"] or ""))

    violations = []
    started_idx = {(r["season"], r["roster_id"], r["player_id"]): r["fsffl_points_while_started"] for r in started_rows}
    career_idx = {r["player_id"]: r["career_fsffl_points"] for r in career_rows}

    for r in rostered_rows:
        k = (r["season"], r["roster_id"], r["player_id"])
        if started_idx.get(k, 0) > r["fsffl_points_while_rostered"] + EPS:
            violations.append({"type": "started_exceeds_rostered", **r})
        if r["fsffl_points_while_rostered"] > career_idx.get(r["player_id"], 10**9) + EPS:
            violations.append({"type": "rostered_exceeds_career", **r})

    weekly_failures = [x for x in checks if abs(x["difference"]) > EPS]
    violations.extend({"type": "weekly_lineup_reconciliation", **x} for x in weekly_failures)

    sentinels = {}
    for name in ("Patrick Mahomes", "Trey McBride", "Jared Goff", "Geno Smith"):
        sentinels[name] = next((r for r in career_rows if r["player_name"] == name), None)

    audit = {
        "seasons": list(SEASONS),
        "status": "PASS" if not violations else "REVIEW",
        "weekly_lineups_checked": len(checks),
        "weekly_lineup_reconciliation_failures": len(weekly_failures),
        "violation_count": len(violations),
        "violations": violations,
        "sentinel_players": sentinels,
        "definitions": {
            "player_career_scoring": "All FSFFL points scored, regardless of roster/start status.",
            "franchise_rostered_scoring": "Points scored while on a franchise roster.",
            "franchise_started_scoring": "Only points scored while in a franchise starting lineup."
        }
    }

    write_json("player_career_scoring.json", career_rows)
    write_json("franchise_rostered_scoring.json", rostered_rows)
    write_json("franchise_started_scoring.json", started_rows)
    write_json("single_season_records.json", single_season)
    write_json("audit_report.json", audit)

    print("AUDIT STATUS:", audit["status"])
    print("Weekly lineups checked:", audit["weekly_lineups_checked"])
    print("Violations:", audit["violation_count"])
    for name, row in sentinels.items():
        if row:
            print(name, row["career_fsffl_points"])

if __name__ == "__main__":
    build()
