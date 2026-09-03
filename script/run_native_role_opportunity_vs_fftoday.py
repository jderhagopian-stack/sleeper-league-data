#!/usr/bin/env python3
"""Benchmark retained WR/TE role-opportunity Native features vs FFToday.

This is the second gate after rolling holdout selection. It compares the current
Native V2 base and the retained WR/TE role-opportunity challenger against the
same verified preseason FFToday rows and the same realized outcomes.

The external forecast is never a training target. Candidate feature selection
was completed in a prior rolling-holdout experiment; this script only tests
whether that pre-selected bundle closes error on the exact external common
cohort.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

from native_projection_challenger import RidgeModel, choose_alpha_temporally
from run_native_projection_core_context_benchmark import AGE, DURABILITY, enrich, fetch_players
import run_native_vs_fftoday_historical_benchmark as ext
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

RETAINED_POSITIONS = {"WR", "TE"}


def fnum(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def build_rows(start_season: int, max_season: int):
    season_rows = []
    # One extra prior season is required for target-season prior-team context.
    for season in range(start_season - 1, max_season + 1):
        season_rows.extend(normalize_season(fetch_csv(season), season))
    lagged = add_feature_team(
        enrich(make_lagged_rows(season_rows), season_rows, fetch_players()),
        season_rows,
    )
    target_seasons = sorted({int(r["season"]) for r in lagged if int(r["season"]) <= max_season})
    role_maps = {season: opening_roles(season) for season in target_seasons}
    return attach_opportunity_features(lagged, season_rows, role_maps)


def predict(rows: list[dict], target_season: int, position: str, challenger: bool) -> dict:
    train = [r for r in rows if r["position"] == position and int(r["season"]) < target_season]
    test = [r for r in rows if r["position"] == position and int(r["season"]) == target_season]
    if not train or not test:
        raise ValueError(f"{target_season} {position}: empty train/test")

    extra = list(BASE_EXTRA[position])
    if challenger and position in RETAINED_POSITIONS:
        extra += list(SHARE[position]) + list(VACATED[position]) + list(INTERACTIONS[position])
    features = list(BASE_FEATURES[position]) + extra

    result = {
        str(r["player_id"]): {
            "player_name": r["player_name"],
            "position": position,
            "raw_stats": {},
        }
        for r in test
    }
    for target in TARGETS[position]:
        alpha, _ = choose_alpha_temporally(train, features, target)
        model = RidgeModel(alpha).fit(
            [[fnum(r.get(f)) for f in features] for r in train],
            [fnum(r.get(target)) for r in train],
        )
        preds = model.predict([[fnum(r.get(f)) for f in features] for r in test])
        stat = target.removeprefix("next_")
        for r, pred in zip(test, preds):
            result[str(r["player_id"])]["raw_stats"][stat] = max(0.0, float(pred))
    return result


def prediction_by_name(rows, target_season, position, challenger):
    raw = predict(rows, target_season, position, challenger)
    out = {}
    for item in raw.values():
        name = ext.norm_name(item["player_name"])
        for stat, value in item["raw_stats"].items():
            out[(name, stat)] = value
    return out


def mae(vals):
    vals = list(vals)
    return sum(vals) / len(vals) if vals else None


def run(inventory_path: Path, start_season: int = 2016):
    inv = ext.eligible_inventory(inventory_path)
    inv = [r for r in inv if r["position"] in RETAINED_POSITIONS]
    if not inv:
        raise ValueError("No eligible WR/TE FFToday snapshots")
    max_season = max(int(r["season"]) for r in inv)
    rows = build_rows(start_season, max_season)

    detail = {}
    summary = defaultdict(lambda: {
        "base_wins": 0,
        "challenger_wins": 0,
        "ties": 0,
        "base_maes": [],
        "challenger_maes": [],
        "external_maes": [],
        "group_improvement_vs_base_pct": [],
    })
    coverage = []

    for item in inv:
        season, pos = int(item["season"]), item["position"]
        fft = ext.fetch_fftoday(season, pos, item["snapshot_date"])
        base = prediction_by_name(rows, season, pos, False)
        challenger = prediction_by_name(rows, season, pos, True)
        actual_rows = [r for r in rows if int(r["season"]) == season and r["position"] == pos]
        actual_index = {ext.norm_name(r["player_name"]): r for r in actual_rows}
        fft_index = {ext.norm_name(r["player_name"]): r for r in fft}
        common_players = sorted(set(actual_index) & set(fft_index))
        common_stats = sorted(
            set(ext.NATIVE_TARGET) & set(dict(ext.LAYOUT[pos]).keys()) &
            {t.removeprefix("next_") for t in TARGETS[pos]}
        )
        rows_scored = 0
        for stat in common_stats:
            b_errors = []
            c_errors = []
            e_errors = []
            for name in common_players:
                ar = actual_index[name]
                er = fft_index[name]
                key = (name, stat)
                target = ext.NATIVE_TARGET[stat]
                if key not in base or key not in challenger or target not in ar or stat not in er:
                    continue
                actual = float(ar[target])
                b_errors.append(abs(base[key] - actual))
                c_errors.append(abs(challenger[key] - actual))
                e_errors.append(abs(float(er[stat]) - actual))
            if not b_errors:
                continue
            bmae, cmae, emae = mae(b_errors), mae(c_errors), mae(e_errors)
            winner = "challenger" if cmae < bmae else "base" if bmae < cmae else "tie"
            d = {
                "n": len(b_errors),
                "base_native_mae": bmae,
                "challenger_native_mae": cmae,
                "fftoday_mae": emae,
                "challenger_improvement_vs_base_pct": 100.0 * (bmae - cmae) / bmae if bmae else 0.0,
                "base_improvement_vs_fftoday_pct": 100.0 * (emae - bmae) / emae if emae else 0.0,
                "challenger_improvement_vs_fftoday_pct": 100.0 * (emae - cmae) / emae if emae else 0.0,
                "native_internal_winner": winner,
            }
            detail[f"{season}|{pos}|{stat}"] = d
            s = summary[pos]
            s[f"{winner}_wins" if winner != "tie" else "ties"] += 1
            s["base_maes"].append(bmae)
            s["challenger_maes"].append(cmae)
            s["external_maes"].append(emae)
            s["group_improvement_vs_base_pct"].append(d["challenger_improvement_vs_base_pct"])
            rows_scored += len(b_errors)
        coverage.append({
            "season": season,
            "position": pos,
            "common_players": len(common_players),
            "common_stats": common_stats,
            "common_stat_rows": rows_scored,
            "snapshot_date": item["snapshot_date"],
        })

    normalized = {}
    for pos, s in summary.items():
        improvements = sorted(s.pop("group_improvement_vs_base_pct"))
        n = len(improvements)
        median = improvements[n // 2] if n % 2 else (improvements[n // 2 - 1] + improvements[n // 2]) / 2.0
        normalized[pos] = {
            **{k:v for k,v in s.items() if not k.endswith("_maes")},
            "group_count": n,
            "mean_challenger_improvement_vs_base_pct": sum(improvements) / n,
            "median_challenger_improvement_vs_base_pct": median,
            "mean_base_native_mae": mae(s["base_maes"]),
            "mean_challenger_native_mae": mae(s["challenger_maes"]),
            "mean_fftoday_mae": mae(s["external_maes"]),
        }

    gate = {}
    for pos in RETAINED_POSITIONS:
        p = normalized.get(pos)
        if not p:
            gate[pos] = {"passes_external_common_cohort_gate": False, "reason":"no eligible groups"}
            continue
        # Require majority of shared groups to improve and positive mean/median.
        passed = bool(
            p["challenger_wins"] > p["base_wins"]
            and p["mean_challenger_improvement_vs_base_pct"] > 0
            and p["median_challenger_improvement_vs_base_pct"] > 0
        )
        gate[pos] = {
            "passes_external_common_cohort_gate": passed,
            "reason": "Requires more improved than degraded groups plus positive mean and median internal improvement on exact FFToday common cohort."
        }

    return {
        "schema_version":"1.0",
        "status":"PASS",
        "experiment":"retained_wr_te_role_opportunity_vs_fftoday_common_cohort",
        "coverage":coverage,
        "summary":normalized,
        "detail":detail,
        "external_common_cohort_gate":gate,
        "governance":{
            "feature_selection_used_external_results":False,
            "external_projection_used_as_training_target":False,
            "candidate_bundle_preselected_on_rolling_holdouts":True,
            "production_promoted":False,
            "historical_role_provenance":"PROVISIONAL_WEEK1_ADMINISTRATIVE_RECORD_NOT_TIMESTAMPED"
        }
    }


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--inventory",type=Path,default=Path("data/model_validation/historical_projection_source_inventory.json"))
    p.add_argument("--start-season",type=int,default=2016)
    p.add_argument("--output",type=Path,default=Path("data/model_validation/native_role_opportunity_vs_fftoday.json"))
    a=p.parse_args()
    result=run(a.inventory,a.start_season)
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(result,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print(json.dumps({"status":result["status"],"summary":result["summary"],"gate":result["external_common_cohort_gate"],"output":str(a.output)},indent=2))


if __name__=="__main__":
    main()
