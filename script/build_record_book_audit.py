#!/usr/bin/env python3
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OUT = DATA / "record_book"
SEASONS = ("2022", "2023", "2024", "2025")
EPS = 0.011

OUT.mkdir(parents=True, exist_ok=True)


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(name: str, payload: Any) -> None:
    path = OUT / name
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    print(f"Wrote {path.relative_to(ROOT)}")


def normalize_player_season_rows(raw: Any) -> list[dict]:
    if isinstance(raw, list):
        return [x for x in raw if isinstance(x, dict)]
    if isinstance(raw, dict):
        rows = []
        for pid, row in raw.items():
            if isinstance(row, dict):
                r = dict(row)
                r.setdefault("player_id", str(pid))
                rows.append(r)
        return rows
    raise TypeError(f"Unexpected player-season shape: {type(raw).__name__}")


def extract_weekly_rows(raw: Any) -> Iterable[dict]:
    """
    Recursively yield row-like dicts from common weekly JSON layouts.
    A yielded row must contain player_id plus an FSFFL-point field.
    """
    if isinstance(raw, list):
        for item in raw:
            yield from extract_weekly_rows(item)
        return

    if not isinstance(raw, dict):
        return

    point_keys = ("fsffl_points", "points_fsffl", "fantasy_points_fsffl")
    if "player_id" in raw and any(k in raw for k in point_keys):
        yield raw
        return

    # Common mapping form: {"4046": {...row...}, ...}
    for key, value in raw.items():
        if isinstance(value, dict):
            if any(k in value for k in point_keys):
                row = dict(value)
                row.setdefault("player_id", str(key))
                yield row
            else:
                yield from extract_weekly_rows(value)
        elif isinstance(value, list):
            yield from extract_weekly_rows(value)


def row_points(row: dict) -> float:
    for key in ("fsffl_points", "points_fsffl", "fantasy_points_fsffl"):
        if key in row:
            return float(row.get(key) or 0.0)
    return 0.0


def build_player_scoring():
    meta: dict[str, dict] = {}
    season_summary: dict[tuple[str, str], float] = {}
    weekly_summary: dict[tuple[str, str], float] = defaultdict(float)
    weekly_found_by_season: dict[str, int] = {}

    # Season-summary source.
    for season in SEASONS:
        season_path = DATA / "stats" / "fsffl" / season / "player_season_fsffl.json"
        rows = normalize_player_season_rows(load_json(season_path))

        for row in rows:
            pid = str(row.get("player_id") or "")
            if not pid:
                continue

            meta[pid] = {
                "player_id": pid,
                "player_name": row.get("player_name") or row.get("full_name"),
                "position": row.get("position"),
            }
            season_summary[(season, pid)] = float(row.get("fsffl_points") or 0.0)

    # Independent weekly player-scoring source.
    for season in SEASONS:
        weekly_path = DATA / "stats" / "fsffl" / season / "player_weekly_fsffl.json"
        count = 0
        if weekly_path.exists():
            raw = load_json(weekly_path)
            for row in extract_weekly_rows(raw):
                pid = str(row.get("player_id") or "")
                if not pid:
                    continue
                weekly_summary[(season, pid)] += row_points(row)
                count += 1

                if pid not in meta:
                    meta[pid] = {
                        "player_id": pid,
                        "player_name": row.get("player_name") or row.get("full_name"),
                        "position": row.get("position"),
                    }
        weekly_found_by_season[season] = count

    # Compare the two independent player-scoring sources.
    reconciliation = []
    all_keys = sorted(set(season_summary) | set(weekly_summary))
    for season, pid in all_keys:
        season_pts = round(season_summary.get((season, pid), 0.0), 3)
        weekly_pts = round(weekly_summary.get((season, pid), 0.0), 3)
        diff = round(weekly_pts - season_pts, 3)

        if abs(diff) > EPS:
            reconciliation.append({
                "season": season,
                "player_id": pid,
                "player_name": meta.get(pid, {}).get("player_name"),
                "position": meta.get(pid, {}).get("position"),
                "season_summary_points": season_pts,
                "weekly_sum_points": weekly_pts,
                "difference": diff,
            })

    # Keep season-summary totals as the published career source until reconciliation passes.
    career_totals = defaultdict(float)
    points_by_season = defaultdict(dict)
    single_season = []

    for (season, pid), pts in season_summary.items():
        career_totals[pid] += pts
        points_by_season[pid][season] = round(pts, 3)
        single_season.append({
            "season": season,
            "player_id": pid,
            "player_name": meta.get(pid, {}).get("player_name"),
            "position": meta.get(pid, {}).get("position"),
            "fsffl_points": round(pts, 3),
        })

    career_rows = [{
        "player_id": pid,
        "player_name": meta.get(pid, {}).get("player_name"),
        "position": meta.get(pid, {}).get("position"),
        "career_fsffl_points": round(pts, 3),
        "points_by_season": points_by_season[pid],
    } for pid, pts in career_totals.items()]

    career_rows.sort(key=lambda x: (-x["career_fsffl_points"], x["player_name"] or ""))
    single_season.sort(key=lambda x: (-x["fsffl_points"], x["player_name"] or ""))

    return meta, career_rows, single_season, reconciliation, weekly_found_by_season


def build_franchise_ledgers(meta: dict[str, dict]):
    rostered = defaultdict(lambda: {"points": 0.0, "weeks": 0, "started_points": 0.0, "starts": 0})
    started = defaultdict(lambda: {"points": 0.0, "starts": 0})
    weekly_checks = []
    starter_not_on_roster = []

    for season in SEASONS:
        path = DATA / "stats" / "fsffl" / season / "league_matchups_raw.json"
        raw = load_json(path)

        if not isinstance(raw, dict):
            raise TypeError(f"{path} must be dict keyed by week")

        for week, records in raw.items():
            if not isinstance(records, list):
                continue

            for rec in records:
                rid = str(rec.get("roster_id") or "")
                if not rid:
                    continue

                players = [str(x) for x in (rec.get("players") or []) if x is not None]
                starters = [str(x) for x in (rec.get("starters") or []) if x is not None]
                players_set = set(players)

                pp = {
                    str(k): float(v or 0.0)
                    for k, v in (rec.get("players_points") or {}).items()
                }

                sp = rec.get("starters_points") or []
                if len(sp) == len(starters):
                    starter_points_by_pid = {
                        pid: float(points or 0.0)
                        for pid, points in zip(starters, sp)
                    }
                else:
                    starter_points_by_pid = {
                        pid: pp.get(pid, 0.0)
                        for pid in starters
                    }

                for pid in starters:
                    if pid not in players_set:
                        starter_not_on_roster.append({
                            "season": season,
                            "week": str(week),
                            "roster_id": rid,
                            "player_id": pid,
                            "player_name": meta.get(pid, {}).get("player_name"),
                        })

                for pid in players:
                    key = (season, rid, pid)
                    rostered[key]["points"] += pp.get(pid, 0.0)
                    rostered[key]["weeks"] += 1

                for pid in starters:
                    pts = starter_points_by_pid.get(pid, pp.get(pid, 0.0))
                    key = (season, rid, pid)
                    started[key]["points"] += pts
                    started[key]["starts"] += 1
                    rostered[key]["started_points"] += pts
                    rostered[key]["starts"] += 1

                official = float(rec.get("points") or 0.0)
                derived = sum(starter_points_by_pid.values())
                weekly_checks.append({
                    "season": season,
                    "week": str(week),
                    "roster_id": rid,
                    "official_lineup_points": round(official, 3),
                    "derived_starter_points": round(derived, 3),
                    "difference": round(derived - official, 3),
                })

    rostered_rows = []
    for (season, rid, pid), a in rostered.items():
        rostered_rows.append({
            "season": season,
            "roster_id": rid,
            "player_id": pid,
            "player_name": meta.get(pid, {}).get("player_name"),
            "position": meta.get(pid, {}).get("position"),
            "fsffl_points_while_rostered": round(a["points"], 3),
            "weeks_rostered": a["weeks"],
            "fsffl_points_while_started": round(a["started_points"], 3),
            "starts": a["starts"],
            "bench_points": round(a["points"] - a["started_points"], 3),
        })

    started_rows = []
    for (season, rid, pid), a in started.items():
        started_rows.append({
            "season": season,
            "roster_id": rid,
            "player_id": pid,
            "player_name": meta.get(pid, {}).get("player_name"),
            "position": meta.get(pid, {}).get("position"),
            "fsffl_points_while_started": round(a["points"], 3),
            "starts": a["starts"],
        })

    rostered_rows.sort(key=lambda x: (-x["fsffl_points_while_rostered"], x["player_name"] or ""))
    started_rows.sort(key=lambda x: (-x["fsffl_points_while_started"], x["player_name"] or ""))

    return rostered_rows, started_rows, weekly_checks, starter_not_on_roster


def build_audit(career_rows, reconciliation, weekly_found_by_season,
                rostered_rows, started_rows, weekly_checks, starter_not_on_roster):
    weekly_lineup_failures = [
        x for x in weekly_checks if abs(x["difference"]) > EPS
    ]

    # Correct negative-score-safe identity check:
    # rostered = started + bench by construction. Do NOT assert started <= rostered,
    # because a benched player can score negative fantasy points.
    identity_failures = []
    for r in rostered_rows:
        reconstructed = round(
            r["fsffl_points_while_started"] + r["bench_points"], 3
        )
        diff = round(reconstructed - r["fsffl_points_while_rostered"], 3)
        if abs(diff) > EPS:
            identity_failures.append({**r, "identity_difference": diff})

    sentinel_names = ("Patrick Mahomes", "Trey McBride", "Jared Goff", "Geno Smith")
    sentinels = {
        name: next((r for r in career_rows if r["player_name"] == name), None)
        for name in sentinel_names
    }

    mahomes_recon = [
        x for x in reconciliation
        if x["player_name"] == "Patrick Mahomes"
    ]

    blocking_issues = (
        len(weekly_lineup_failures)
        + len(starter_not_on_roster)
        + len(identity_failures)
        + len(reconciliation)
    )

    return {
        "seasons": list(SEASONS),
        "status": "PASS" if blocking_issues == 0 else "REVIEW",
        "methodology_version": 4,
        "weekly_player_rows_detected_by_season": weekly_found_by_season,
        "weekly_lineups_checked": len(weekly_checks),
        "weekly_lineup_reconciliation_failures": len(weekly_lineup_failures),
        "starter_not_on_roster_failures": len(starter_not_on_roster),
        "rostered_started_bench_identity_failures": len(identity_failures),
        "player_season_vs_weekly_mismatches": len(reconciliation),
        "blocking_issue_count": blocking_issues,
        "weekly_lineup_failures": weekly_lineup_failures,
        "starter_not_on_roster": starter_not_on_roster,
        "identity_failures": identity_failures,
        "player_scoring_reconciliation": reconciliation,
        "patrick_mahomes_reconciliation": mahomes_recon,
        "sentinel_players": sentinels,
        "definitions": {
            "player_career_scoring":
                "Published from player_season_fsffl totals until the independent weekly-player reconciliation has zero mismatches.",
            "franchise_rostered_scoring":
                "Points scored while present in a franchise weekly players array.",
            "franchise_started_scoring":
                "Only points scored while present in that franchise's starters array.",
            "bench_points":
                "Rostered points minus started points. Negative bench scoring is valid."
        }
    }


def main():
    meta, career, single_season, reconciliation, weekly_found = build_player_scoring()
    rostered, started, weekly_checks, starter_not_on_roster = build_franchise_ledgers(meta)

    audit = build_audit(
        career, reconciliation, weekly_found,
        rostered, started, weekly_checks, starter_not_on_roster
    )

    write_json("player_career_scoring.json", career)
    write_json("franchise_rostered_scoring.json", rostered)
    write_json("franchise_started_scoring.json", started)
    write_json("single_season_records.json", single_season)
    write_json("player_scoring_source_reconciliation.json", reconciliation)
    write_json("audit_report.json", audit)

    print()
    print("AUDIT STATUS:", audit["status"])
    print("Weekly lineups checked:", audit["weekly_lineups_checked"])
    print("Lineup reconciliation failures:", audit["weekly_lineup_reconciliation_failures"])
    print("Starter-not-on-roster failures:", audit["starter_not_on_roster_failures"])
    print("Started/bench identity failures:", audit["rostered_started_bench_identity_failures"])
    print("Player season-vs-weekly mismatches:", audit["player_season_vs_weekly_mismatches"])
    print()
    print("Patrick Mahomes reconciliation:")
    print(json.dumps(audit["patrick_mahomes_reconciliation"], indent=2))


if __name__ == "__main__":
    main()
