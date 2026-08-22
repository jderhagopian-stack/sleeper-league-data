#!/usr/bin/env python3
"""
FSFFL GM 3.0 — consolidated successor to GM 2.2.

Design:
- GM 2.2 remains the proven strategic/economic core.
- This engine imports that core directly rather than reimplementing it.
- Runtime patches remove operating-year assumptions before the core executes.
- Universal 12-team GM 2.2 outputs are then promoted/enriched with Simulator 1.0,
  GM 3.0 prospect intelligence, evidence coverage, and a clean GM 3.0 manifest.

This is intentionally an upgrade of GM 2.2, not a parallel replacement model.
"""
from __future__ import annotations

import json
import math
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
DATA = ROOT / "data"
GM = DATA / "gm"

sys.path.insert(0, str(SCRIPT_DIR))
import build_fsffl_gm_engine as core  # proven GM 2.2 engine

MODEL_VERSION = "FSFFL-GM-3.0"


def load(path, default=None):
    p = Path(path)
    if not p.exists():
        return default
    with p.open("r", encoding="utf-8") as f:
        return json.load(f)


def dump(path, obj):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2)


def active_season():
    league = load(DATA / "league.json", {}) or {}
    season = league.get("season")
    if season in (None, ""):
        raise SystemExit("GM 3.0 cannot resolve active season from data/league.json")
    return int(season)


def recent_trade_count(trade_profile):
    tp = trade_profile or {}
    if tp.get("recent_trades") is not None:
        return core.safe_float(tp.get("recent_trades"))
    keys = [k for k in tp if str(k).startswith("recent_trades_")]
    if not keys:
        return 0.0
    # Prefer the most recently-labelled field without assuming calendar years.
    key = sorted(keys)[-1]
    return core.safe_float(tp.get(key))


def dynamic_fallback_pick_value(current_season):
    def fn(year, tier, rnd, detected):
        if (year, tier, rnd) in detected:
            return detected[(year, tier, rnd)]
        if (year, "mid", rnd) in detected:
            base = detected[(year, "mid", rnd)]
            return base * {"early": 1.18, "mid": 1.0, "late": 0.84}[tier]
        known = [(y, v) for (y, t, r), v in detected.items() if t == tier and r == rnd]
        if known:
            y0, v0 = min(known, key=lambda z: abs(z[0] - year))
            return v0 * (0.88 ** max(0, year - y0)) * (1.08 ** max(0, y0 - year))
        mids = {1: 5200.0, 2: 2350.0, 3: 1050.0}
        horizon = max(int(year) - int(current_season), 1)
        year_discount = 0.88 ** max(horizon - 1, 0)
        tier_adj = {"early": 1.20, "mid": 1.0, "late": 0.82}[tier]
        return mids[rnd] * year_discount * tier_adj
    return fn


def dynamic_pick_quality_model(current_season):
    def build():
        owners, teams, profile_by_uid, roster_to_uid, uid_to_team = core._v2_owner_context()
        frag = load(DATA / "roster_fragility_index.json", {}) or {}
        frag_by_uid = {str(x.get("user_id")): x for x in frag.get("teams") or []}
        _, meta, _ = core._v2_asset_maps(owners)
        picks, seen = [], set()

        for aid, m in meta.items():
            if m.get("asset_type") != "pick" or not aid.startswith("pick:") or aid in seen:
                continue
            seen.add(aid)
            mt = re.match(r"pick:(\d{4}):R(\d+):orig(\d+)", aid)
            if not mt:
                continue
            year, rnd, orig_rid = int(mt.group(1)), int(mt.group(2)), int(mt.group(3))
            orig_uid = roster_to_uid.get(orig_rid)
            t = teams.get(str(orig_uid), {}) if orig_uid else {}
            contender = core.safe_float(t.get("contender_score"), 0.5)
            dynasty = core.safe_float(t.get("dynasty_roster_score"), 0.5)
            fragility = core.safe_float(
                (frag_by_uid.get(str(orig_uid)) or {}).get("fragility_score"), 0.5
            )
            years_out = max(year - current_season, 1)
            dynasty_weight = core.clamp(0.48 + 0.08 * (years_out - 1), 0.48, 0.68)
            current_weight = 1.0 - dynasty_weight
            strength = current_weight * contender + dynasty_weight * dynasty
            collapse_risk = core.clamp((1.0 - strength) * 0.72 + fragility * 0.28, 0.0, 1.0)
            early = core.clamp(0.10 + 0.58 * collapse_risk, 0.08, 0.68)
            late = core.clamp(0.10 + 0.58 * strength, 0.08, 0.68)
            mid = max(1.0 - early - late, 0.08)
            z = early + mid + late
            early, mid, late = early / z, mid / z, late / z
            tier = max((("early", early), ("mid", mid), ("late", late)), key=lambda x: x[1])[0]
            picks.append({
                "asset_id": aid,
                "pick": m.get("name"),
                "season": year,
                "round": rnd,
                "horizon_seasons": years_out,
                "original_roster_id": orig_rid,
                "original_owner_user_id": orig_uid,
                "original_team": uid_to_team.get(str(orig_uid)),
                "current_contender_score": round(contender, 3),
                "dynasty_roster_score": round(dynasty, 3),
                "fragility_score": round(fragility, 3),
                "structural_strength_score": round(strength, 3),
                "early_scenario_weight": round(early, 3),
                "mid_scenario_weight": round(mid, 3),
                "late_scenario_weight": round(late, 3),
                "most_likely_tier": tier,
                "quality_signal": round(collapse_risk, 3),
                "confidence": "medium" if orig_uid and t else "low",
            })
        picks.sort(key=lambda x: (x["round"], -x["quality_signal"], x["season"]))
        return {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "model_version": MODEL_VERSION,
            "active_season": current_season,
            "methodology_note": (
                "Pick-quality scenario weights inherit GM 2.2 logic but use a dynamic "
                "season horizon rather than fixed calendar years."
            ),
            "picks": picks,
        }
    return build


def dynamic_pick_profile(current_season):
    def fn(aid, uid, ctx):
        meta = ctx["asset_meta"].get(aid, {})
        parsed = core._u_parse_pick(aid, meta) or {}
        quality = ctx["pick_quality"].get(aid, {})
        rnd = int(parsed.get("round") or quality.get("round") or 3)
        season = int(parsed.get("season") or quality.get("season") or (current_season + 3))
        qsignal = core.safe_float(quality.get("quality_signal"), 0.5)
        early = core.safe_float(quality.get("early_scenario_weight"), 0.33)
        late = core.safe_float(quality.get("late_scenario_weight"), 0.33)
        horizon = max(season - current_season, 1)

        if rnd == 1:
            liquidity = core.GM22["first_round_pick_liquidity"]
            upside = core.clamp(0.48 + 0.42 * qsignal, 0.48, 0.95)
            uncertainty = 0.45 + 0.25 * max(horizon - 1, 0)
        elif rnd == 2:
            liquidity = core.GM22["second_round_pick_liquidity"]
            upside = core.clamp(0.28 + 0.32 * qsignal, 0.28, 0.72)
            uncertainty = 0.38 + 0.20 * max(horizon - 1, 0)
        else:
            liquidity = core.GM22["third_round_pick_liquidity"]
            upside = core.clamp(0.12 + 0.20 * qsignal, 0.12, 0.45)
            uncertainty = 0.28 + 0.16 * max(horizon - 1, 0)

        option = core.clamp(upside + 0.18 * core.clamp(uncertainty, 0.0, 1.0), 0.0, 1.0)
        original_uid = str(quality.get("original_owner_user_id") or "")
        control_bonus = 0.10 if original_uid and original_uid == str(uid) else 0.0
        return {
            "round": rnd,
            "season": season,
            "horizon_seasons": horizon,
            "quality_signal": round(qsignal, 4),
            "early_scenario_weight": round(early, 4),
            "late_scenario_weight": round(late, 4),
            "liquidity": round(liquidity, 4),
            "upside_optionality": round(option, 4),
            "own_pick_control_bonus": round(control_bonus, 4),
            "most_likely_tier": quality.get("most_likely_tier"),
            "confidence": quality.get("confidence"),
        }
    return fn


def patch_gm22_runtime(season):
    """Patch only environment/year assumptions; preserve GM 2.2 economics."""
    core.FUTURE_PICK_YEARS = [season + 1, season + 2, season + 3]
    core.NFLVERSE_SNAP_COUNTS_URL = (
        "https://github.com/nflverse/nflverse-data/releases/download/"
        f"snap_counts/snap_counts_{season}.csv"
    )

    # base_main passes a legacy literal to these functions. Ignore that argument
    # and use current league metadata instead.
    orig_perf = core.load_recent_performance
    orig_usage = core.fetch_nflverse_usage
    orig_snaps = core.fetch_nflverse_snaps

    core.load_recent_performance = lambda active_season=None: orig_perf(active_season=season)
    core.fetch_nflverse_usage = lambda active_season=None: orig_usage(active_season=season)
    core.fetch_nflverse_snaps = lambda active_season=None: orig_snaps(active_season=season)

    core.fallback_pick_value = dynamic_fallback_pick_value(season)
    core.build_pick_quality_model = dynamic_pick_quality_model(season)
    core._u_pick_profile = dynamic_pick_profile(season)

    def activity(uid, ctx):
        p = ctx["profile_by_uid"].get(str(uid), {})
        tp = p.get("trade_profile") or {}
        total = core.safe_float(tp.get("total_trades"))
        recent = recent_trade_count(tp)
        return core.clamp(
            0.45 * min(total / 40.0, 1.0)
            + 0.30 * min(recent / 15.0, 1.0)
            + 0.25 * core.safe_float(tp.get("initiation_rate"), 0.5),
            0.0, 1.0
        )
    core._u_activity_score = activity

    # Legacy HSG-only helper functions still execute during base_main. Select a
    # valid league franchise dynamically so they remain backward-compatible,
    # while Universal Franchise Mode continues to build all teams.
    profiles = load(DATA / "owner_behavior_profiles.json", []) or []
    first = next((p for p in profiles if p.get("manager") or p.get("team_name")), {})
    core.USER_MANAGER = first.get("manager") or first.get("username") or ""
    core.USER_TEAM = first.get("team_name") or ""
    core.PROTECTED_HSG_PLAYERS = set()


def simulator_context(season):
    standings = load(DATA / "simulator" / str(season) / "outputs" / "standings_projection.json", {}) or {}
    validation = load(DATA / "simulator" / str(season) / "outputs" / "validation_report.json", {}) or {}
    by_uid = {str(x.get("user_id")): x for x in standings.get("teams") or []}
    return standings, validation, by_uid


def intelligence_coverage():
    fi = load(DATA / "football_intelligence_signals.json", {}) or {}
    prospect = load(GM / "prospect_board.json", {}) or {}
    return {
        "season_phase": fi.get("season_phase"),
        "usage_records": int(fi.get("usage_records") or 0),
        "snap_records": int(fi.get("snap_records") or 0),
        "prior_snap_records": int(fi.get("prior_snap_records") or 0),
        "preseason_usage_records": int(fi.get("preseason_usage_records") or 0),
        "manual_intelligence_records": int(fi.get("manual_intelligence_records") or 0),
        "prospect_count": int(prospect.get("prospect_count") or 0),
    }


def promote_universal_outputs(season):
    standings, sim_validation, sim_by_uid = simulator_context(season)
    coverage = intelligence_coverage()

    franchise = load(GM / "franchise_index.json", {}) or {}
    franchise["model_version"] = MODEL_VERSION
    franchise["inherited_core"] = "GM-2.2"
    franchise["season"] = season
    franchise["upgrade_status"] = "GM_2_2_CORE_PLUS_GM_3_0"
    dump(GM / "franchise_index.json", franchise)

    team_index = franchise.get("teams") or []
    for row in team_index:
        uid = str(row.get("user_id"))
        paths = row.get("paths") or {}
        sim = sim_by_uid.get(uid, {})

        for key in ("command_center", "strategic_asset_profiles", "trade_opportunities", "sell_leverage"):
            path = paths.get(key)
            if not path:
                continue
            payload = load(ROOT / path, {}) or {}
            payload["model_version"] = MODEL_VERSION
            payload["inherited_core"] = "GM-2.2"
            payload["season"] = season
            payload["gm30"] = {
                "simulator_1_0": {
                    "expected_wins": sim.get("expected_wins"),
                    "expected_points_for": sim.get("expected_points_for"),
                    "playoff_probability": sim.get("playoff_probability"),
                    "bye_probability": sim.get("bye_probability"),
                    "championship_probability": sim.get("championship_probability"),
                },
                "evidence_coverage": coverage,
            }
            dump(ROOT / path, payload)

    for name in ("mutual_trade_map.json", "trade_analysis_context.json"):
        path = GM / "league" / name
        payload = load(path, {}) or {}
        payload["model_version"] = MODEL_VERSION
        payload["inherited_core"] = "GM-2.2"
        payload["season"] = season
        dump(path, payload)

    dump(GM / "league" / "simulator_context.json", {
        "model_version": MODEL_VERSION,
        "season": season,
        "simulator_model_version": standings.get("model_version"),
        "validation": sim_validation,
        "teams": standings.get("teams") or [],
    })

    # Production manifest makes provenance explicit.
    dump(GM / "manifest.json", {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "model_version": MODEL_VERSION,
        "season": season,
        "architecture": "GM_2_2_CORE_EVOLVED_TO_GM_3_0",
        "scope": "ALL_TEAMS",
        "team_view_count": len(team_index),
        "core_inheritance": {
            "source_engine": "script/build_fsffl_gm_engine.py",
            "source_model": "GM-2.2",
            "preserved_capabilities": [
                "universal_franchise_mode",
                "optimized_legal_lineups",
                "dynamic_hold_and_break_glass_values",
                "nonlinear_package_economics",
                "bilateral_trade_economics",
                "owner_specific_sell_leverage",
                "hold_wait_benchmark",
                "mutual_trade_map",
                "strategic_asset_profiles",
            ],
        },
        "gm30_upgrades": [
            "dynamic_operating_season",
            "dynamic_future_pick_horizon",
            "simulator_1_0_team_context",
            "prospect_intelligence_interface",
            "football_intelligence_coverage",
            "explicit_2_2_provenance_and_regression_baseline",
        ],
        "evidence_coverage": coverage,
        "simulator_validation_passed": bool(
            sim_validation.get("validation_passed")
            if "validation_passed" in sim_validation
            else sim_validation.get("passed")
        ),
    })


def gm30_validation(season):
    franchise = load(GM / "franchise_index.json", {}) or {}
    manifest = load(GM / "manifest.json", {}) or {}
    coverage = intelligence_coverage()
    sim_validation = load(
        DATA / "simulator" / str(season) / "outputs" / "validation_report.json", {}
    ) or {}

    checks = [
        ("gm22_core_inherited", manifest.get("core_inheritance", {}).get("source_model") == "GM-2.2"),
        ("all_12_team_views", len(franchise.get("teams") or []) == 12),
        ("season_dynamic", int(manifest.get("season") or 0) == season),
        ("future_pick_horizon_dynamic", core.FUTURE_PICK_YEARS == [season + 1, season + 2, season + 3]),
        ("simulator_connected", (GM / "league" / "simulator_context.json").exists()),
        ("mutual_trade_map_preserved", (GM / "league" / "mutual_trade_map.json").exists()),
        ("strategic_profiles_preserved", all(
            Path(ROOT / ((x.get("paths") or {}).get("strategic_asset_profiles", ""))).exists()
            for x in franchise.get("teams") or []
        )),
        ("trade_opportunities_preserved", all(
            Path(ROOT / ((x.get("paths") or {}).get("trade_opportunities", ""))).exists()
            for x in franchise.get("teams") or []
        )),
    ]

    # Empty optional intelligence should not make the engine fail, but it must be
    # surfaced as degraded coverage rather than an A-grade evidence condition.
    warnings = []
    phase = str(coverage.get("season_phase") or "")
    if phase == "PRESEASON":
        if (
            coverage["preseason_usage_records"] == 0
            and coverage["manual_intelligence_records"] == 0
            and coverage["prior_snap_records"] == 0
        ):
            warnings.append("FOOTBALL_INTELLIGENCE_EMPTY")
    elif coverage["usage_records"] == 0 and coverage["snap_records"] == 0:
        warnings.append("FOOTBALL_INTELLIGENCE_EMPTY")

    if coverage["prospect_count"] == 0:
        warnings.append("PROSPECT_INTELLIGENCE_EMPTY")

    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "model_version": MODEL_VERSION,
        "season": season,
        "passed": all(passed for _, passed in checks),
        "checks": [{"check": name, "passed": passed} for name, passed in checks],
        "warnings": warnings,
        "evidence_coverage": coverage,
        "simulator_validation": sim_validation,
    }
    dump(GM / "validation_report.json", report)
    if not report["passed"]:
        raise SystemExit("GM 3.0 consolidated validation failed")
    return report


def main():
    season = active_season()
    patch_gm22_runtime(season)

    # Preserve the authoritative phase-aware GM 3.0 intelligence file.
    intelligence_path = DATA / "football_intelligence_signals.json"
    phase_aware_intelligence = load(intelligence_path, None)

    # Run the complete proven GM 2.2 core.
    # GM 2.2 may temporarily overwrite football_intelligence_signals.json
    # with its legacy format.
    core.main()

    # Restore the authoritative GM 3.0 football-intelligence contract.
    #
    # Validate the DATA CONTRACT, not an exact model-version string. This allows
    # future v2/v3/v4 intelligence builders to evolve without the inherited
    # GM 2.2 core accidentally overwriting them.
    required_intelligence_fields = {
        "active_season",
        "season_phase",
        "phase_weights",
        "prior_snaps",
        "preseason_usage",
    }
    if (
        isinstance(phase_aware_intelligence, dict)
        and required_intelligence_fields.issubset(phase_aware_intelligence.keys())
    ):
        dump(intelligence_path, phase_aware_intelligence)

    # Promote/enrich the inherited GM 2.2 outputs into GM 3.0.
    promote_universal_outputs(season)
    report = gm30_validation(season)

    print("FSFFL GM 3.0 consolidated engine complete.")
    print("Architecture: GM 2.2 proven core + GM 3.0 upgrades")
    print(f"Season: {season}")
    print(f"Validation: {'PASS' if report['passed'] else 'FAIL'}")
    if report["warnings"]:
        print("Coverage warnings: " + ", ".join(report["warnings"]))


if __name__ == "__main__":
    main()
