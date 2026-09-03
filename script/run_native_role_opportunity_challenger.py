#!/usr/bin/env python3
"""Test preseason-known role/opportunity allocation features for FSFFL Native.

The challenger extends the current Native V2 feature bundle with only information
knowable before Week 1: prior-season team opportunity shares, target-season
opening depth-chart role, and vacated prior-year opportunity implied by players
missing from the target-season opening depth chart.

No external projection value is used as a training target. No target-season
realized snap, route, carry, target, pass-attempt, team-total, injury, or
transaction outcome is used as a feature.
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
from run_native_projection_core_context_benchmark import AGE, DURABILITY, enrich, fetch_players
from run_native_projection_nflverse_benchmark import FEATURES as BASE_FEATURES, TARGETS, fetch_csv, make_lagged_rows, normalize_season

DEPTH_URL = "https://github.com/nflverse/nflverse-data/releases/download/depth_charts/depth_charts_{season}.csv"
POSITIONS = ("QB", "RB", "WR", "TE")
ROLE = ["opening_role_available", "opening_is_first_team", "opening_depth_rank"]
QB_REFINEMENT = [
    "opening_team_known",
    "opening_team_changed",
    "qb1_x_lag1_attempts",
    "qb1_x_lag1_pass_yards",
    "qb1_x_lag1_rush_yards",
]
BASE_EXTRA = {
    "QB": list(DURABILITY["QB"]) + ROLE + QB_REFINEMENT,
    "RB": ROLE,
    "WR": list(AGE["WR"]) + ROLE,
    "TE": list(AGE["TE"]) + ROLE,
}
SHARE = {
    "QB": ["prior_team_pass_attempt_share", "prior_team_qb_rush_share"],
    "RB": ["prior_team_rb_carry_share", "prior_team_skill_target_share"],
    "WR": ["prior_team_skill_target_share"],
    "TE": ["prior_team_skill_target_share"],
}
VACATED = {
    "QB": ["opening_team_vacated_qb_attempt_share"],
    "RB": ["opening_team_vacated_rb_carry_share", "opening_team_vacated_skill_target_share"],
    "WR": ["opening_team_vacated_skill_target_share"],
    "TE": ["opening_team_vacated_skill_target_share"],
}
INTERACTIONS = {
    "QB": ["qb1_x_prior_pass_attempt_share"],
    "RB": ["first_team_x_prior_rb_carry_share"],
    "WR": ["first_team_x_prior_skill_target_share"],
    "TE": ["first_team_x_prior_skill_target_share"],
}


def fnum(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return float(default)


def fetch_depth(season: int) -> list[dict]:
    req = urllib.request.Request(DEPTH_URL.format(season=season), headers={"User-Agent":"FSFFL-role-opportunity-challenger/1.0"})
    with urllib.request.urlopen(req, timeout=90) as r:
        return list(csv.DictReader(io.StringIO(r.read().decode("utf-8-sig"))))


def opening_roles(season: int) -> dict[tuple[str, str], dict]:
    """Historical <=2024 opening-week administrative role map.

    This source is intentionally classified as provisional because these older
    files do not expose timestamped daily snapshots. It is suitable for a
    challenger experiment, not automatic production promotion.
    """
    out = {}
    for r in fetch_depth(season):
        if str(r.get("game_type") or "").upper() != "REG" or str(r.get("week") or "").strip() != "1":
            continue
        pid = str(r.get("gsis_id") or "").strip()
        pos = str(r.get("position") or "").upper().strip()
        if not pid or pos not in POSITIONS:
            continue
        rank = max(1.0, fnum(r.get("depth_team"), 9.0))
        key = (pid, pos)
        prior = out.get(key)
        if prior is None or rank < prior["rank"]:
            out[key] = {"rank":rank, "team":str(r.get("club_code") or "").strip()}
    return out


def add_feature_team(lagged: list[dict], season_rows: list[dict]) -> list[dict]:
    idx = {(int(r["season"]), str(r["player_id"])): str(r.get("team") or "") for r in season_rows}
    out = []
    for raw in lagged:
        r = dict(raw)
        r["feature_team"] = idx.get((int(r["feature_season"]), str(r["player_id"])), "")
        out.append(r)
    return out


def build_prior_team_context(season_rows: list[dict], role_maps: dict[int, dict]):
    totals = defaultdict(lambda: {"qb_attempts":0.0, "qb_rushes":0.0, "rb_carries":0.0, "skill_targets":0.0})
    prior_players = defaultdict(list)
    for r in season_rows:
        season = int(r["season"]); team = str(r.get("team") or ""); pos = str(r.get("position") or "")
        if not team or pos not in POSITIONS:
            continue
        key = (season, team)
        if pos == "QB":
            totals[key]["qb_attempts"] += fnum(r.get("attempts"))
            totals[key]["qb_rushes"] += fnum(r.get("carries"))
        if pos == "RB":
            totals[key]["rb_carries"] += fnum(r.get("carries"))
        if pos in {"RB", "WR", "TE"}:
            totals[key]["skill_targets"] += fnum(r.get("targets"))
        prior_players[key].append(r)

    opening_members = defaultdict(lambda: {"QB":set(), "RB":set(), "SKILL":set()})
    for season, mapping in role_maps.items():
        for (pid, pos), role in mapping.items():
            team = str(role.get("team") or "")
            if not team:
                continue
            if pos == "QB": opening_members[(season, team)]["QB"].add(pid)
            if pos == "RB": opening_members[(season, team)]["RB"].add(pid)
            if pos in {"RB", "WR", "TE"}: opening_members[(season, team)]["SKILL"].add(pid)

    vacated = defaultdict(lambda: {"qb_attempts":0.0, "rb_carries":0.0, "skill_targets":0.0})
    target_seasons = sorted(role_maps)
    for target in target_seasons:
        prior = target - 1
        teams = {team for season, team in totals if season == prior}
        for team in teams:
            members = opening_members[(target, team)]
            for r in prior_players[(prior, team)]:
                pid = str(r["player_id"]); pos = str(r["position"])
                if pos == "QB" and pid not in members["QB"]:
                    vacated[(target, team)]["qb_attempts"] += fnum(r.get("attempts"))
                if pos == "RB" and pid not in members["RB"]:
                    vacated[(target, team)]["rb_carries"] += fnum(r.get("carries"))
                if pos in {"RB", "WR", "TE"} and pid not in members["SKILL"]:
                    vacated[(target, team)]["skill_targets"] += fnum(r.get("targets"))
    return totals, vacated


def safe_share(num, denom):
    return fnum(num) / fnum(denom) if fnum(denom) > 0 else 0.0


def attach_opportunity_features(rows: list[dict], season_rows: list[dict], role_maps: dict[int, dict]) -> list[dict]:
    totals, vacated = build_prior_team_context(season_rows, role_maps)
    out = []
    for raw in rows:
        r = dict(raw); target = int(r["season"]); pos = str(r["position"]); pid = str(r["player_id"])
        role = role_maps.get(target, {}).get((pid, pos))
        rank = float(role["rank"]) if role else 9.0
        opening_team = str(role.get("team") or "") if role else ""
        feature_team = str(r.get("feature_team") or "")
        prior_totals = totals[(int(r["feature_season"]), feature_team)]
        opening_prior_totals = totals[(target - 1, opening_team)]
        opening_vacated = vacated[(target, opening_team)]

        r["opening_role_available"] = int(role is not None)
        r["opening_is_first_team"] = int(bool(role and rank == 1.0))
        r["opening_depth_rank"] = min(rank, 4.0) if role else 4.0
        r["opening_team_known"] = int(bool(opening_team))
        r["opening_team_changed"] = int(bool(opening_team and feature_team and opening_team != feature_team))
        r["qb1_x_lag1_attempts"] = fnum(r.get("lag1_attempts")) * r["opening_is_first_team"]
        r["qb1_x_lag1_pass_yards"] = fnum(r.get("lag1_passing_yards")) * r["opening_is_first_team"]
        r["qb1_x_lag1_rush_yards"] = fnum(r.get("lag1_rushing_yards")) * r["opening_is_first_team"]

        r["prior_team_pass_attempt_share"] = safe_share(r.get("lag1_attempts"), prior_totals["qb_attempts"])
        r["prior_team_qb_rush_share"] = safe_share(r.get("lag1_carries"), prior_totals["qb_rushes"])
        r["prior_team_rb_carry_share"] = safe_share(r.get("lag1_carries"), prior_totals["rb_carries"])
        r["prior_team_skill_target_share"] = safe_share(r.get("lag1_targets"), prior_totals["skill_targets"])

        r["opening_team_vacated_qb_attempt_share"] = safe_share(opening_vacated["qb_attempts"], opening_prior_totals["qb_attempts"])
        r["opening_team_vacated_rb_carry_share"] = safe_share(opening_vacated["rb_carries"], opening_prior_totals["rb_carries"])
        r["opening_team_vacated_skill_target_share"] = safe_share(opening_vacated["skill_targets"], opening_prior_totals["skill_targets"])

        r["qb1_x_prior_pass_attempt_share"] = r["opening_is_first_team"] * r["prior_team_pass_attempt_share"]
        r["first_team_x_prior_rb_carry_share"] = r["opening_is_first_team"] * r["prior_team_rb_carry_share"]
        r["first_team_x_prior_skill_target_share"] = r["opening_is_first_team"] * r["prior_team_skill_target_share"]
        out.append(r)
    return out


def evaluate(rows, pos, extra, holdouts):
    by = {}
    for h in holdouts:
        rep = temporal_holdout([r for r in rows if int(r["season"]) <= h], pos, list(BASE_FEATURES[pos]) + list(extra), TARGETS[pos])
        vals = list(rep["targets"].values())
        by[str(h)] = {
            "mean_improvement_vs_persistence_pct": sum(float(v.get("improvement_vs_persistence_pct", 0.0)) for v in vals) / len(vals),
            "targets_beating_persistence": sum(bool(v.get("beats_persistence")) for v in vals),
            "targets": rep["targets"],
        }
    return {
        "mean_improvement_vs_persistence_pct": sum(v["mean_improvement_vs_persistence_pct"] for v in by.values()) / len(by),
        "mean_targets_beating_persistence": sum(v["targets_beating_persistence"] for v in by.values()) / len(by),
        "by_season": by,
    }


def compare(base, cur):
    deltas = {s: cur["by_season"][s]["mean_improvement_vs_persistence_pct"] - base["by_season"][s]["mean_improvement_vs_persistence_pct"] for s in base["by_season"]}
    delta = cur["mean_improvement_vs_persistence_pct"] - base["mean_improvement_vs_persistence_pct"]
    improved = sum(v > 0 for v in deltas.values())
    return {
        "delta_vs_native_v2_pp": delta,
        "seasons_improved": improved,
        "seasons_tested": len(deltas),
        "season_deltas_pp": deltas,
        "passes_restrained_gate": bool(delta >= 0.5 and improved >= max(2, len(deltas) - 1)),
    }


def run(start_season=2016, end_season=2024, first_holdout=2021):
    if end_season > 2024:
        raise ValueError("Historical opening-role challenger is capped at 2024 because older role provenance is week-1 administrative, not timestamped daily data.")
    season_rows = []
    for season in range(start_season - 1, end_season + 1):
        season_rows.extend(normalize_season(fetch_csv(season), season))
    lagged = add_feature_team(enrich(make_lagged_rows(season_rows), season_rows, fetch_players()), season_rows)
    target_seasons = sorted({int(r["season"]) for r in lagged if int(r["season"]) <= end_season})
    role_maps = {season: opening_roles(season) for season in target_seasons}
    rows = attach_opportunity_features(lagged, season_rows, role_maps)
    holdouts = [s for s in target_seasons if first_holdout <= s <= end_season]

    results = {}; selection = {}
    for pos in POSITIONS:
        base = evaluate(rows, pos, BASE_EXTRA[pos], holdouts)
        bundles = {
            "prior_team_share": BASE_EXTRA[pos] + SHARE[pos],
            "vacated_opportunity": BASE_EXTRA[pos] + VACATED[pos],
            "share_plus_vacated": BASE_EXTRA[pos] + SHARE[pos] + VACATED[pos],
            "share_vacated_interactions": BASE_EXTRA[pos] + SHARE[pos] + VACATED[pos] + INTERACTIONS[pos],
        }
        pos_results = {"native_v2_base": base, "candidates": {}}
        candidates = {}
        for name, features in bundles.items():
            cur = evaluate(rows, pos, features, holdouts)
            pos_results["candidates"][name] = cur
            candidates[name] = compare(base, cur)
        passing = [(name, d["delta_vs_native_v2_pp"]) for name, d in candidates.items() if d["passes_restrained_gate"]]
        selection[pos] = {
            "selected": max(passing, key=lambda x: x[1])[0] if passing else None,
            "candidates": candidates,
        }
        results[pos] = pos_results

    return {
        "schema_version":"1.0",
        "status":"PASS",
        "experiment":"native_preseason_role_opportunity_allocation_challenger",
        "holdouts":holdouts,
        "results":results,
        "selection":selection,
        "governance":{
            "external_projection_used_as_training_target":False,
            "target_season_realized_usage_used_as_feature":False,
            "target_season_realized_team_totals_used_as_feature":False,
            "historical_role_provenance":"PROVISIONAL_WEEK1_ADMINISTRATIVE_RECORD_NOT_TIMESTAMPED",
            "production_promoted":False,
            "next_gate":"Any retained bundle must be rerun on the exact Native-vs-external common cohort before production consideration."
        }
    }


def self_test():
    season_rows = [
        {"season":2023,"player_id":"q1","position":"QB","team":"AAA","attempts":500,"carries":50,"targets":0},
        {"season":2023,"player_id":"q2","position":"QB","team":"AAA","attempts":100,"carries":10,"targets":0},
        {"season":2023,"player_id":"r1","position":"RB","team":"AAA","carries":200,"targets":50,"attempts":0},
        {"season":2023,"player_id":"r2","position":"RB","team":"AAA","carries":100,"targets":30,"attempts":0},
        {"season":2023,"player_id":"w1","position":"WR","team":"AAA","targets":120,"attempts":0,"carries":0},
        {"season":2023,"player_id":"w2","position":"WR","team":"AAA","targets":80,"attempts":0,"carries":0},
        {"season":2023,"player_id":"t1","position":"TE","team":"AAA","targets":70,"attempts":0,"carries":0},
    ]
    role_maps = {2024:{
        ("q1","QB"):{"rank":1,"team":"AAA"},
        ("r1","RB"):{"rank":1,"team":"AAA"},
        ("w1","WR"):{"rank":1,"team":"AAA"},
        ("t1","TE"):{"rank":1,"team":"AAA"},
    }}
    rows = [
        {"season":2024,"feature_season":2023,"player_id":"q1","position":"QB","feature_team":"AAA","lag1_attempts":500,"lag1_carries":50,"lag1_targets":0,"lag1_passing_yards":4000,"lag1_rushing_yards":250},
        {"season":2024,"feature_season":2023,"player_id":"r1","position":"RB","feature_team":"AAA","lag1_attempts":0,"lag1_carries":200,"lag1_targets":50,"lag1_passing_yards":0,"lag1_rushing_yards":900},
    ]
    out = attach_opportunity_features(rows, season_rows, role_maps)
    q = out[0]; r = out[1]
    assert abs(q["prior_team_pass_attempt_share"] - (500/600)) < 1e-9
    assert abs(q["opening_team_vacated_qb_attempt_share"] - (100/600)) < 1e-9
    assert abs(r["prior_team_rb_carry_share"] - (200/300)) < 1e-9
    assert abs(r["opening_team_vacated_rb_carry_share"] - (100/300)) < 1e-9
    assert abs(r["opening_team_vacated_skill_target_share"] - (110/350)) < 1e-9
    assert q["qb1_x_prior_pass_attempt_share"] == q["prior_team_pass_attempt_share"]
    print("native role opportunity challenger self-test: PASS")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--start-season", type=int, default=2016)
    p.add_argument("--end-season", type=int, default=2024)
    p.add_argument("--first-holdout", type=int, default=2021)
    p.add_argument("--output", type=Path, default=Path("data/model_validation/native_role_opportunity_challenger.json"))
    p.add_argument("--self-test", action="store_true")
    a = p.parse_args()
    if a.self_test:
        self_test(); return
    result = run(a.start_season, a.end_season, a.first_holdout)
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status":result["status"],"selection":result["selection"],"output":str(a.output)}, indent=2))


if __name__ == "__main__":
    main()
