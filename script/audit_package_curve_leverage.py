#!/usr/bin/env python3
"""Measure downstream leverage of provisional GM-2.2 package curves.

This is a sensitivity/architecture audit, not coefficient calibration. It runs
one fixed contemporary model state under the production GM-2.2 curve and two
bounded counterfactual aggregation rules to determine whether the unsupported
curve materially changes the trade-opportunity board. No projection code or
production coefficient is modified.
"""
from __future__ import annotations

import importlib.util
import json
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGINE_PATH = ROOT / "script" / "build_fsffl_gm_engine.py"
OVERRIDE_PATH = ROOT / "script" / "nonprojection_high_priority_overrides.py"
OUT = ROOT / "data" / "audit" / "package_curve_leverage.json"
MODEL_VERSION = "FSFFL-Package-Curve-Leverage-1.0"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to import {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def package_key(row):
    pkgs = row.get("best_candidate_packages") or []
    if not pkgs:
        return None
    p = pkgs[0]
    return (
        str(row.get("target_asset_id") or ""),
        tuple(sorted(map(str, p.get("focal_outgoing_asset_ids") or []))),
    )


def summarize(payload, top_n=10):
    rows = [x for x in (payload.get("opportunities") or []) if x.get("best_candidate_packages")]
    rows = rows[:top_n]
    return {
        "top_targets": [str(x.get("target_asset_id") or "") for x in rows],
        "top_package_keys": [package_key(x) for x in rows],
        "top_bands": [str(x.get("best_package_recommendation_band") or "") for x in rows],
        "top_scores": [x.get("best_package_decision_score") for x in rows],
        "rows": [
            {
                "target_asset_id": x.get("target_asset_id"),
                "target_player": x.get("target_player"),
                "seller_team": x.get("seller_team"),
                "best_package": (x.get("best_candidate_packages") or [{}])[0].get("focal_outgoing_asset_ids"),
                "band": x.get("best_package_recommendation_band"),
                "decision_score": x.get("best_package_decision_score"),
            }
            for x in rows
        ],
    }


def compare(base, alt):
    bk = base["top_package_keys"]
    ak = alt["top_package_keys"]
    bt = base["top_targets"]
    at = alt["top_targets"]
    common_packages = len(set(map(str, bk)) & set(map(str, ak)))
    common_targets = len(set(bt) & set(at))
    n = max(1, min(len(bk), len(ak)))
    return {
        "same_top_target": bool(bt and at and bt[0] == at[0]),
        "same_top_package": bool(bk and ak and bk[0] == ak[0]),
        "top_target_overlap_fraction": round(common_targets / max(1, min(len(bt), len(at))), 4),
        "top_package_overlap_fraction": round(common_packages / n, 4),
        "top_target_order_identical": bt == at,
        "top_package_order_identical": bk == ak,
        "recommendation_band_order_identical": base["top_bands"] == alt["top_bands"],
    }


def run_variant(engine, uid, ctx, profiles, weights):
    prior = list(engine.GM22["package_weights"])
    try:
        engine.GM22["package_weights"] = list(weights)
        return engine.build_universal_trade_opportunities(uid, ctx=ctx, profile_by_uid=profiles)
    finally:
        engine.GM22["package_weights"] = prior


def main():
    engine = load_module(ENGINE_PATH, "gm_engine_package_curve_audit")
    override = load_module(OVERRIDE_PATH, "np_override_package_curve_audit")
    override.install(engine)

    ctx = engine._u_load_context()
    uids = sorted(map(str, ctx.get("owners") or {}))
    profiles = {
        uid: engine._u_profile_map(engine.build_strategic_asset_profiles_for_team(uid, ctx))
        for uid in uids
    }

    production = [1.0, 0.78, 0.62, 0.50, 0.42]
    shallow = [1.0, 0.92, 0.84, 0.78, 0.72]
    neutral = [1.0, 1.0, 1.0, 1.0, 1.0]

    team_results = []
    for uid in uids:
        base = summarize(run_variant(engine, uid, ctx, profiles, production))
        alt_shallow = summarize(run_variant(engine, uid, ctx, profiles, shallow))
        alt_neutral = summarize(run_variant(engine, uid, ctx, profiles, neutral))
        team_results.append({
            "user_id": uid,
            "team": (ctx.get("owners", {}).get(uid) or {}).get("team_name"),
            "production": base,
            "shallow_counterfactual": alt_shallow,
            "neutral_counterfactual": alt_neutral,
            "comparison_vs_shallow": compare(base, alt_shallow),
            "comparison_vs_neutral": compare(base, alt_neutral),
        })

    teams_with_top_target_flip = sum(
        1 for x in team_results
        if (not x["comparison_vs_shallow"]["same_top_target"])
        or (not x["comparison_vs_neutral"]["same_top_target"])
    )
    teams_with_top_package_flip = sum(
        1 for x in team_results
        if (not x["comparison_vs_shallow"]["same_top_package"])
        or (not x["comparison_vs_neutral"]["same_top_package"])
    )
    min_target_overlap = min(
        [x["comparison_vs_shallow"]["top_target_overlap_fraction"] for x in team_results]
        + [x["comparison_vs_neutral"]["top_target_overlap_fraction"] for x in team_results]
        or [1.0]
    )
    min_package_overlap = min(
        [x["comparison_vs_shallow"]["top_package_overlap_fraction"] for x in team_results]
        + [x["comparison_vs_neutral"]["top_package_overlap_fraction"] for x in team_results]
        or [1.0]
    )

    payload = {
        "model_version": MODEL_VERSION,
        "interpretation": {
            "historical_validation": False,
            "coefficient_tuning": False,
            "projection_behavior_changed": False,
            "production_behavior_changed": False,
            "uses_current_fixed_model_state": True,
            "counterfactuals_are_sensitivity_bounds_not_recommendations": True,
            "governed_high_priority_overrides_applied": True,
        },
        "curves": {
            "production_gm22": production,
            "shallow_counterfactual": shallow,
            "neutral_counterfactual": neutral,
        },
        "scope": {
            "module": "GM-2.2 universal trade opportunity board",
            "final_trade_report_score_directly_uses_this_curve": False,
            "curve_can_affect_preliminary_package_screening_and_trade_idea_ranking": True,
            "teams_audited": len(team_results),
            "top_n_per_team": 10,
        },
        "summary": {
            "teams_with_top_target_flip_under_any_counterfactual": teams_with_top_target_flip,
            "teams_with_top_package_flip_under_any_counterfactual": teams_with_top_package_flip,
            "minimum_top_target_overlap_fraction": round(min_target_overlap, 4),
            "minimum_top_package_overlap_fraction": round(min_package_overlap, 4),
            "material_downstream_leverage_detected": bool(teams_with_top_target_flip or teams_with_top_package_flip or min_package_overlap < 0.8),
            "authoritative_curve_calibration_available": False,
        },
        "teams": team_results,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(payload["summary"], indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
