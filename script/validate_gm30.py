#!/usr/bin/env python3
"""FSFFL GM 3.0 consolidated validation gate."""
from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
GM = DATA / "gm"
ENGINE = ROOT / "script" / "build_fsffl_gm30.py"
REFERENCE = DATA / "gm22_reference"


def load(path, default=None):
    p = Path(path)
    if not p.exists():
        return default
    with p.open("r", encoding="utf-8") as f:
        return json.load(f)


def add(checks, name, passed, detail=None):
    row = {"check": name, "passed": bool(passed)}
    if detail is not None:
        row["detail"] = detail
    checks.append(row)


def main():
    checks = []
    warnings = []

    league = load(DATA / "league.json", {}) or {}
    season = str(league.get("season") or "")
    total_rosters = int(league.get("total_rosters") or 0)

    engine = ENGINE.read_text(encoding="utf-8") if ENGINE.exists() else ""
    add(checks, "engine_exists", ENGINE.exists())
    try:
        ast.parse(engine)
        add(checks, "engine_python_syntax", True)
    except SyntaxError as exc:
        add(checks, "engine_python_syntax", False, str(exc))

    add(checks, "league_metadata_has_season", bool(season), season or "missing")
    add(checks, "engine_uses_active_season_metadata", "def active_season(" in engine)
    add(checks, "engine_inherits_gm22_core", "import build_fsffl_gm_engine as core" in engine)
    add(checks, "no_compiled_user_identity",
        all(x not in engine for x in ("GM30_USER_ID", "jimmygoodjob", "Hurts So Good")))
    add(checks, "no_fixed_future_pick_list",
        "FUTURE_PICK_YEARS = [2027" not in engine)

    manifest = load(GM / "manifest.json", {}) or {}
    franchise = load(GM / "franchise_index.json", {}) or {}
    gm_validation = load(GM / "validation_report.json", {}) or {}

    add(checks, "manifest_is_gm30",
        manifest.get("model_version") == "FSFFL-GM-3.0",
        manifest.get("model_version"))
    add(checks, "architecture_is_evolved_gm22",
        manifest.get("architecture") == "GM_2_2_CORE_EVOLVED_TO_GM_3_0",
        manifest.get("architecture"))
    add(checks, "manifest_season_matches",
        str(manifest.get("season") or "") == season,
        {"manifest": manifest.get("season"), "league": season})
    add(checks, "manifest_scope_all_teams",
        manifest.get("scope") == "ALL_TEAMS")
    add(checks, "gm22_capabilities_declared_preserved",
        set([
            "universal_franchise_mode",
            "optimized_legal_lineups",
            "dynamic_hold_and_break_glass_values",
            "nonlinear_package_economics",
            "bilateral_trade_economics",
            "owner_specific_sell_leverage",
            "hold_wait_benchmark",
            "mutual_trade_map",
            "strategic_asset_profiles",
        ]).issubset(set(
            (manifest.get("core_inheritance") or {}).get("preserved_capabilities") or []
        )))

    teams = franchise.get("teams") or []
    expected = total_rosters or 12
    add(checks, "franchise_views_cover_league",
        len(teams) == expected,
        {"views": len(teams), "expected": expected})

    required_team_files = []
    for team in teams:
        for key in ("command_center", "strategic_asset_profiles",
                    "trade_opportunities", "sell_leverage"):
            rel = (team.get("paths") or {}).get(key)
            if rel:
                required_team_files.append(ROOT / rel)
    missing = [str(p.relative_to(ROOT)) for p in required_team_files if not p.exists()]
    add(checks, "all_team_gm22_capability_files_preserved",
        not missing, missing or "none")

    add(checks, "mutual_trade_map_present",
        (GM / "league" / "mutual_trade_map.json").exists())
    add(checks, "trade_analysis_context_present",
        (GM / "league" / "trade_analysis_context.json").exists())
    add(checks, "simulator_context_present",
        (GM / "league" / "simulator_context.json").exists())

    add(checks, "internal_gm30_validation_passed",
        bool(gm_validation.get("passed")),
        gm_validation.get("checks"))

    # Frozen pre-migration GM 2.2 reference must remain available for regression auditing.
    add(checks, "gm22_reference_preserved",
        (REFERENCE / "franchise_index.json").exists()
        and (REFERENCE / "league" / "mutual_trade_map.json").exists())

    coverage = manifest.get("evidence_coverage") or {}
    if int(coverage.get("prospect_count") or 0) == 0:
        warnings.append("PROSPECT_INTELLIGENCE_EMPTY")
    if (int(coverage.get("usage_records") or 0) == 0
            and int(coverage.get("snap_records") or 0) == 0):
        warnings.append("FOOTBALL_INTELLIGENCE_EMPTY")

    passed = all(x["passed"] for x in checks)
    payload = {
        "validator": "FSFFL-GM-3.0-CONSOLIDATED-GATE",
        "passed": passed,
        "checks": checks,
        "warnings": warnings,
    }
    print(json.dumps(payload, indent=2))
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
