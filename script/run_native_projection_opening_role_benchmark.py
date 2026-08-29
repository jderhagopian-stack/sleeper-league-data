#!/usr/bin/env python3
"""Test opening-week role context as a restrained native-projection challenger.

Historical nflverse depth-chart files through 2024 do not carry a timestamped
preseason snapshot. They do carry Week-1 administrative depth-chart records.
This experiment therefore treats Week-1 role fields as PROVISIONAL opening-week
context: no game statistics or target-season outcomes are used, but freeze-time
provenance is not strong enough for final commercial production dependency.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import urllib.request
from pathlib import Path

from run_native_projection_core_context_benchmark import DURABILITY, enrich, fetch_players
from run_native_projection_nflverse_benchmark import FEATURES as BASE_FEATURES, TARGETS, fetch_csv, make_lagged_rows, normalize_season
from native_projection_challenger import temporal_holdout

DEPTH_URL = "https://github.com/nflverse/nflverse-data/releases/download/depth_charts/depth_charts_{season}.csv"
QB_BASE_EXTRA = list(DURABILITY["QB"])
CANDIDATES = {
    "selected_v1": QB_BASE_EXTRA,
    "opening_roster_presence": QB_BASE_EXTRA + ["opening_role_available"],
    "opening_qb1": QB_BASE_EXTRA + ["opening_role_available", "opening_is_qb1"],
    "opening_depth_rank": QB_BASE_EXTRA + ["opening_role_available", "opening_is_qb1", "opening_depth_rank"],
}


def fnum(value, default=0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def fetch_depth(season: int) -> list[dict]:
    req = urllib.request.Request(DEPTH_URL.format(season=season), headers={"User-Agent":"FSFFL-opening-role-benchmark/1.0"})
    with urllib.request.urlopen(req, timeout=90) as r:
        return list(csv.DictReader(io.StringIO(r.read().decode("utf-8-sig"))))


def opening_qb_roles(season: int) -> dict[str, dict]:
    rows = fetch_depth(season)
    out: dict[str, dict] = {}
    for r in rows:
        if str(r.get("game_type") or "").upper() != "REG" or str(r.get("week") or "").strip() != "1":
            continue
        pid = str(r.get("gsis_id") or "").strip()
        if not pid:
            continue
        pos = str(r.get("depth_position") or r.get("position") or "").upper().strip()
        if pos != "QB" and str(r.get("position") or "").upper().strip() != "QB":
            continue
        rank = fnum(r.get("depth_team"), 9.0)
        if rank <= 0:
            rank = 9.0
        prior = out.get(pid)
        if prior is None or rank < prior["rank"]:
            out[pid] = {"rank": rank, "team": str(r.get("club_code") or "").strip()}
    return out


def attach_opening_context(rows: list[dict], seasons: list[int]) -> tuple[list[dict], dict]:
    by_season = {s: opening_qb_roles(s) for s in seasons}
    coverage = {}
    for s in seasons:
        roles = by_season[s]
        coverage[str(s)] = {
            "qb_rows": len(roles),
            "qb1_rows": sum(1 for x in roles.values() if x["rank"] == 1),
        }
    enriched = []
    for raw in rows:
        row = dict(raw)
        s = int(row["season"])
        role = by_season.get(s, {}).get(str(row["player_id"])) if row["position"] == "QB" else None
        rank = float(role["rank"]) if role else 9.0
        row["opening_role_available"] = int(role is not None)
        row["opening_is_qb1"] = int(bool(role and rank == 1.0))
        # Cap the numerical rank so an absent player is not given an arbitrarily huge value.
        row["opening_depth_rank"] = min(rank, 4.0) if role else 4.0
        enriched.append(row)
    return enriched, coverage


def evaluate_candidate(rows: list[dict], extra: list[str], holdouts: list[int]) -> dict:
    by_season = {}
    for holdout in holdouts:
        eligible = [r for r in rows if int(r["season"]) <= holdout]
        report = temporal_holdout(eligible, "QB", list(BASE_FEATURES["QB"]) + list(extra), TARGETS["QB"])
        vals = list(report["targets"].values())
        by_season[str(holdout)] = {
            "mean_improvement_vs_persistence_pct": sum(float(v.get("improvement_vs_persistence_pct", 0.0)) for v in vals) / len(vals),
            "targets_beating_persistence": sum(bool(v.get("beats_persistence")) for v in vals),
            "targets": report["targets"],
        }
    mean_imp = sum(x["mean_improvement_vs_persistence_pct"] for x in by_season.values()) / len(by_season)
    mean_wins = sum(x["targets_beating_persistence"] for x in by_season.values()) / len(by_season)
    return {"mean_improvement_vs_persistence_pct":mean_imp,"mean_targets_beating_persistence":mean_wins,"by_season":by_season}


def run(start_season: int, end_season: int, first_holdout: int) -> dict:
    if end_season > 2024:
        raise ValueError("This provisional historical-schema experiment is intentionally capped at 2024; 2025+ depth charts use a different timestamped schema.")
    season_rows=[]
    for season in range(start_season,end_season+1):
        season_rows.extend(normalize_season(fetch_csv(season),season))
    lagged=enrich(make_lagged_rows(season_rows),season_rows,fetch_players())
    target_seasons=sorted({int(r["season"]) for r in lagged})
    rows,coverage=attach_opening_context(lagged,target_seasons)
    holdouts=[s for s in target_seasons if first_holdout <= s <= end_season]
    results={name:evaluate_candidate(rows,features,holdouts) for name,features in CANDIDATES.items()}
    base=results["selected_v1"]
    selection={}
    for name,res in results.items():
        if name == "selected_v1":
            continue
        delta=res["mean_improvement_vs_persistence_pct"]-base["mean_improvement_vs_persistence_pct"]
        season_deltas={s:res["by_season"][s]["mean_improvement_vs_persistence_pct"]-base["by_season"][s]["mean_improvement_vs_persistence_pct"] for s in res["by_season"]}
        selection[name]={
            "delta_vs_selected_v1_pp":delta,
            "seasons_improved":sum(v>0 for v in season_deltas.values()),
            "seasons_tested":len(season_deltas),
            "season_deltas_pp":season_deltas,
            "passes_restrained_gate":bool(delta >= 0.5 and sum(v>0 for v in season_deltas.values()) >= max(2,len(season_deltas)-1)),
        }
    passing=[(name,x["delta_vs_selected_v1_pp"]) for name,x in selection.items() if x["passes_restrained_gate"]]
    chosen=max(passing,key=lambda x:x[1])[0] if passing else None
    return {
        "schema_version":"1.0",
        "status":"PASS",
        "experiment":"provisional_opening_week_qb_role_context",
        "holdouts":holdouts,
        "coverage":coverage,
        "candidates":results,
        "selection":selection,
        "selected_candidate":chosen,
        "governance":{
            "target_season_realized_stats_used":False,
            "context_fields":"Week-1 depth-chart administrative role fields only: presence, QB1 indicator, depth rank.",
            "historical_freeze_provenance":"PROVISIONAL_WEEK1_ADMINISTRATIVE_RECORD_NOT_TIMESTAMPED",
            "commercial_dependency_approved":False,
            "production_promoted":False,
            "retention_gate":"At least +0.5 percentage points mean improvement vs accepted selected V1 and improvement in all but at most one tested holdout season.",
            "replacement_note":"Before commercial production, replace/verify with a free source demonstrably frozen before opening kickoff.",
        },
    }


def main() -> None:
    p=argparse.ArgumentParser()
    p.add_argument("--start-season",type=int,default=2016)
    p.add_argument("--end-season",type=int,default=2024)
    p.add_argument("--first-holdout",type=int,default=2021)
    p.add_argument("--output",type=Path,default=Path("data/model_validation/native_projection_opening_role_benchmark.json"))
    a=p.parse_args()
    result=run(a.start_season,a.end_season,a.first_holdout)
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(result,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print(json.dumps({"status":result["status"],"selected_candidate":result["selected_candidate"],"selection":result["selection"]},indent=2))

if __name__ == "__main__":
    main()
