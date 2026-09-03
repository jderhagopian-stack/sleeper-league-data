#!/usr/bin/env python3
"""Benchmark prior offensive snap participation as an FSFFL Native challenger.

The benchmark is intentionally layered on the strongest currently supported
position-specific base: QB/RB retain current Native V2; WR/TE also include the
provisional role/target-share/vacated-opportunity bundle that passed rolling and
single-external common-cohort gates.

Only prior completed-season snap counts are used. Target-season realized snap
information and external projection values are never training features/targets.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import urllib.request
from collections import defaultdict
from pathlib import Path

from native_projection_challenger import temporal_holdout
from run_native_projection_core_context_benchmark import enrich, fetch_players
from run_native_projection_nflverse_benchmark import FEATURES as BASE_FEATURES, TARGETS, fetch_csv, make_lagged_rows, normalize_season
from run_native_role_opportunity_challenger import (
    BASE_EXTRA,
    SHARE,
    VACATED,
    INTERACTIONS,
    add_feature_team,
    attach_opportunity_features,
    opening_roles,
)

PLAYERS_URL = "https://github.com/nflverse/nflverse-data/releases/download/players/players.csv"
SNAP_URL = "https://github.com/nflverse/nflverse-data/releases/download/snap_counts/snap_counts_{season}.csv"
POSITIONS = ("QB", "RB", "WR", "TE")


def fnum(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return float(default)


def pct(v):
    x = fnum(v)
    if x > 1.0:
        x /= 100.0
    return max(0.0, min(1.0, x))


def fetch_id_map():
    req = urllib.request.Request(PLAYERS_URL, headers={"User-Agent":"FSFFL-snap-share-challenger/1.0"})
    with urllib.request.urlopen(req, timeout=90) as r:
        rows = list(csv.DictReader(io.StringIO(r.read().decode("utf-8-sig"))))
    out = {}
    for row in rows:
        pfr = str(row.get("pfr_id") or "").strip()
        gsis = str(row.get("gsis_id") or "").strip()
        if pfr and gsis:
            out[pfr] = gsis
    return out


def fetch_snap_counts(season: int):
    req = urllib.request.Request(SNAP_URL.format(season=season), headers={"User-Agent":"FSFFL-snap-share-challenger/1.0"})
    with urllib.request.urlopen(req, timeout=90) as r:
        return list(csv.DictReader(io.StringIO(r.read().decode("utf-8-sig"))))


def aggregate_snaps(start_season: int, end_season: int, pfr_to_gsis: dict):
    """Aggregate completed-season offense snaps by GSIS player id.

    offense_pct_mean is snap-weighted across rows when possible so a tiny game
    does not receive the same influence as a full workload game. The raw season
    snap total remains available as a separate feature.
    """
    out = {}
    audit = {}
    for season in range(start_season, end_season + 1):
        rows = fetch_snap_counts(season)
        agg = defaultdict(lambda: {"offense_snaps":0.0, "weighted_pct_num":0.0, "weighted_pct_den":0.0, "rows":0})
        mapped = 0
        for r in rows:
            if str(r.get("game_type") or "REG").upper() != "REG":
                continue
            pfr = str(r.get("pfr_player_id") or "").strip()
            gsis = pfr_to_gsis.get(pfr)
            if not gsis:
                continue
            snaps = max(0.0, fnum(r.get("offense_snaps")))
            share = pct(r.get("offense_pct"))
            a = agg[gsis]
            a["offense_snaps"] += snaps
            weight = snaps if snaps > 0 else 1.0
            a["weighted_pct_num"] += share * weight
            a["weighted_pct_den"] += weight
            a["rows"] += 1
            mapped += 1
        for gsis, a in agg.items():
            out[(season, gsis)] = {
                "offense_snaps": a["offense_snaps"],
                "offense_pct_mean": a["weighted_pct_num"] / a["weighted_pct_den"] if a["weighted_pct_den"] else 0.0,
                "game_rows": a["rows"],
            }
        audit[str(season)] = {"source_rows":len(rows), "mapped_rows":mapped, "mapped_players":len(agg)}
    return out, audit


def current_base_extra(pos: str):
    extra = list(BASE_EXTRA[pos])
    if pos in {"WR", "TE"}:
        extra += list(SHARE[pos]) + list(VACATED[pos]) + list(INTERACTIONS[pos])
    return extra


def attach_snaps(rows: list[dict], snaps: dict):
    out = []
    for raw in rows:
        r = dict(raw)
        key = (int(r["feature_season"]), str(r["player_id"]))
        s = snaps.get(key)
        snap_n = fnum(s.get("offense_snaps")) if s else 0.0
        snap_pct = fnum(s.get("offense_pct_mean")) if s else 0.0
        available = int(s is not None and snap_n > 0)
        denom = max(1.0, snap_n)

        r["lag1_snap_available"] = available
        r["lag1_offense_snaps"] = snap_n
        r["lag1_offense_snap_pct_mean"] = snap_pct
        r["first_team_x_lag1_offense_snap_pct"] = fnum(r.get("opening_is_first_team")) * snap_pct
        r["depth_rank_x_lag1_offense_snap_pct"] = fnum(r.get("opening_depth_rank"), 4.0) * snap_pct

        r["lag1_attempts_per_100_offense_snaps"] = 100.0 * fnum(r.get("lag1_attempts")) / denom
        r["lag1_qb_rushes_per_100_offense_snaps"] = 100.0 * fnum(r.get("lag1_carries")) / denom
        r["lag1_carries_per_100_offense_snaps"] = 100.0 * fnum(r.get("lag1_carries")) / denom
        r["lag1_targets_per_100_offense_snaps"] = 100.0 * fnum(r.get("lag1_targets")) / denom

        r["snap_pct_x_lag1_attempts"] = snap_pct * fnum(r.get("lag1_attempts"))
        r["snap_pct_x_lag1_qb_rushes"] = snap_pct * fnum(r.get("lag1_carries"))
        r["snap_pct_x_lag1_carries"] = snap_pct * fnum(r.get("lag1_carries"))
        r["snap_pct_x_lag1_targets"] = snap_pct * fnum(r.get("lag1_targets"))
        out.append(r)
    return out


ADDITIVE = ["lag1_snap_available", "lag1_offense_snaps", "lag1_offense_snap_pct_mean"]
ROLE_CONDITIONED = ["lag1_snap_available", "first_team_x_lag1_offense_snap_pct", "depth_rank_x_lag1_offense_snap_pct"]
RATE = {
    "QB": ["lag1_snap_available", "lag1_attempts_per_100_offense_snaps", "lag1_qb_rushes_per_100_offense_snaps"],
    "RB": ["lag1_snap_available", "lag1_carries_per_100_offense_snaps", "lag1_targets_per_100_offense_snaps"],
    "WR": ["lag1_snap_available", "lag1_targets_per_100_offense_snaps"],
    "TE": ["lag1_snap_available", "lag1_targets_per_100_offense_snaps"],
}
CONDITIONED_VOLUME = {
    "QB": ["lag1_snap_available", "snap_pct_x_lag1_attempts", "snap_pct_x_lag1_qb_rushes"],
    "RB": ["lag1_snap_available", "snap_pct_x_lag1_carries", "snap_pct_x_lag1_targets"],
    "WR": ["lag1_snap_available", "snap_pct_x_lag1_targets"],
    "TE": ["lag1_snap_available", "snap_pct_x_lag1_targets"],
}


def evaluate(rows, pos, extra, holdouts):
    by = {}
    for h in holdouts:
        report = temporal_holdout(
            [r for r in rows if int(r["season"]) <= h],
            pos,
            list(BASE_FEATURES[pos]) + list(extra),
            TARGETS[pos],
        )
        vals = list(report["targets"].values())
        by[str(h)] = {
            "mean_improvement_vs_persistence_pct": sum(float(v.get("improvement_vs_persistence_pct",0.0)) for v in vals) / len(vals),
            "targets_beating_persistence": sum(bool(v.get("beats_persistence")) for v in vals),
            "targets": report["targets"],
        }
    return {
        "mean_improvement_vs_persistence_pct": sum(x["mean_improvement_vs_persistence_pct"] for x in by.values()) / len(by),
        "mean_targets_beating_persistence": sum(x["targets_beating_persistence"] for x in by.values()) / len(by),
        "by_season": by,
    }


def compare(base, cur):
    deltas = {s:cur["by_season"][s]["mean_improvement_vs_persistence_pct"] - base["by_season"][s]["mean_improvement_vs_persistence_pct"] for s in base["by_season"]}
    delta = cur["mean_improvement_vs_persistence_pct"] - base["mean_improvement_vs_persistence_pct"]
    improved = sum(v > 0 for v in deltas.values())
    return {
        "delta_vs_stronger_base_pp":delta,
        "seasons_improved":improved,
        "seasons_tested":len(deltas),
        "season_deltas_pp":deltas,
        "passes_restrained_gate":bool(delta >= 0.5 and improved >= max(3, len(deltas)-1)),
    }


def run(start_season=2016, end_season=2024, first_holdout=2021):
    season_rows = []
    for season in range(start_season - 1, end_season + 1):
        season_rows.extend(normalize_season(fetch_csv(season), season))
    lagged = add_feature_team(enrich(make_lagged_rows(season_rows), season_rows, fetch_players()), season_rows)
    target_seasons = sorted({int(r["season"]) for r in lagged if int(r["season"]) <= end_season})
    role_maps = {season:opening_roles(season) for season in target_seasons}
    opportunity_rows = attach_opportunity_features(lagged, season_rows, role_maps)

    id_map = fetch_id_map()
    snap_map, snap_audit = aggregate_snaps(start_season - 1, end_season - 1, id_map)
    rows = attach_snaps(opportunity_rows, snap_map)
    holdouts = [s for s in target_seasons if first_holdout <= s <= end_season]

    results = {}; selection = {}
    for pos in POSITIONS:
        base_extra = current_base_extra(pos)
        base = evaluate(rows, pos, base_extra, holdouts)
        bundles = {
            "additive_snap": base_extra + ADDITIVE,
            "role_conditioned_snap": base_extra + ROLE_CONDITIONED,
            "snap_adjusted_rate": base_extra + RATE[pos],
            "snap_conditioned_volume": base_extra + CONDITIONED_VOLUME[pos],
            "combined_restrained_snap": base_extra + ADDITIVE + ROLE_CONDITIONED + RATE[pos] + CONDITIONED_VOLUME[pos],
        }
        candidates = {}; candidate_results = {}
        for name, features in bundles.items():
            cur = evaluate(rows, pos, features, holdouts)
            candidate_results[name] = cur
            candidates[name] = compare(base, cur)
        passing = [(n,d["delta_vs_stronger_base_pp"]) for n,d in candidates.items() if d["passes_restrained_gate"]]
        selection[pos] = {"selected":max(passing,key=lambda x:x[1])[0] if passing else None, "candidates":candidates}
        results[pos] = {"stronger_base":base, "candidates":candidate_results}

    eligible_rows = defaultdict(lambda:{"total":0,"snap_available":0})
    for r in rows:
        pos = r["position"]
        if pos not in POSITIONS:
            continue
        eligible_rows[pos]["total"] += 1
        eligible_rows[pos]["snap_available"] += int(r.get("lag1_snap_available",0))

    return {
        "schema_version":"1.0",
        "status":"PASS",
        "experiment":"native_prior_offensive_snap_share_challenger",
        "holdouts":holdouts,
        "snap_source_audit":snap_audit,
        "snap_feature_coverage":{
            pos:{**d,"coverage":d["snap_available"]/d["total"] if d["total"] else 0.0}
            for pos,d in eligible_rows.items()
        },
        "selection":selection,
        "results":results,
        "governance":{
            "target_season_snap_data_used":False,
            "external_projection_used_as_training_target":False,
            "wr_te_role_opportunity_bundle_included_in_base":True,
            "qb_rb_rejected_role_opportunity_bundle_excluded":True,
            "production_promoted":False,
            "next_gate":"Any retained formulation must be externally common-cohort tested without re-selection."
        }
    }


def self_test():
    rows = [{"season":2024,"feature_season":2023,"player_id":"p1","position":"WR","opening_is_first_team":1,"opening_depth_rank":1,"lag1_targets":100,"lag1_carries":4,"lag1_attempts":0}]
    snaps = {(2023,"p1"):{"offense_snaps":800,"offense_pct_mean":0.8}}
    r = attach_snaps(rows,snaps)[0]
    assert r["lag1_snap_available"] == 1
    assert r["lag1_offense_snaps"] == 800
    assert abs(r["lag1_targets_per_100_offense_snaps"]-12.5) < 1e-9
    assert abs(r["snap_pct_x_lag1_targets"]-80.0) < 1e-9
    assert abs(r["first_team_x_lag1_offense_snap_pct"]-0.8) < 1e-9
    assert pct("72") == 0.72 and pct("0.72") == 0.72
    print("native snap share challenger self-test: PASS")


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--start-season",type=int,default=2016)
    p.add_argument("--end-season",type=int,default=2024)
    p.add_argument("--first-holdout",type=int,default=2021)
    p.add_argument("--output",type=Path,default=Path("data/model_validation/native_snap_share_challenger.json"))
    p.add_argument("--self-test",action="store_true")
    a=p.parse_args()
    if a.self_test:
        self_test(); return
    result=run(a.start_season,a.end_season,a.first_holdout)
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(result,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print(json.dumps({"status":result["status"],"coverage":result["snap_feature_coverage"],"selection":result["selection"],"output":str(a.output)},indent=2))


if __name__=="__main__":
    main()
