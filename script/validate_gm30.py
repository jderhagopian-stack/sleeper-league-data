#!/usr/bin/env python3
"""
GM 3.0 architecture validation gate.

Run after GM 3.0 build and before promoting/replacing a production workflow.
Fails loudly on hard-coded operating seasons, compiled manager identities,
legacy output roots, unresolved simulator templates, perspective regressions,
or stale fixed-year future-pick output fields.
"""
from __future__ import annotations

import ast
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
ENGINE = ROOT / "script" / "build_fsffl_gm30.py"
CONFIG = DATA / "gm3_config.json"
GM_OUT = DATA / "gm"


def read_text(path):
    return path.read_text(encoding="utf-8")


def load_json(path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def add(checks, name, passed, detail=None):
    row = {"check": name, "passed": bool(passed)}
    if detail is not None:
        row["detail"] = detail
    checks.append(row)


def main():
    checks = []

    add(checks, "engine_exists", ENGINE.exists(), str(ENGINE))
    add(checks, "config_exists", CONFIG.exists(), str(CONFIG))
    if not ENGINE.exists() or not CONFIG.exists():
        report(checks)

    engine = read_text(ENGINE)
    cfg = load_json(CONFIG)

    try:
        ast.parse(engine)
        add(checks, "engine_python_syntax", True)
    except SyntaxError as e:
        add(checks, "engine_python_syntax", False, f"{e.msg} line {e.lineno}")

    # The active league year must come from synchronized league metadata.
    league_path = DATA / "league.json"
    league = load_json(league_path) if league_path.exists() else {}
    season = str(league.get("season") or "")
    add(checks, "league_metadata_has_season", bool(season), season or "missing")
    add(
        checks,
        "config_season_is_dynamic",
        cfg.get("season_resolution", {}).get("hardcoded_season") is False
        and cfg.get("season_resolution", {}).get("source") == "data/league.json",
    )
    add(checks, "engine_resolves_season", "def resolve_season(" in engine)

    # Catch operating-year literals. Historical ranges are allowed only if explicitly
    # labelled historical; fixed future-pick output schemas are not.
    current_year_literal = bool(season and re.search(rf'(?<!\d){re.escape(season)}(?!\d)', engine))
    add(
        checks,
        "no_current_season_literal_in_engine",
        not current_year_literal,
        f"active season={season}" if season else "season unavailable",
    )
    fixed_pick_fields = sorted(set(re.findall(r'["\'](20\d{2})_first_(?:expected_slot|band)["\']', engine)))
    add(
        checks,
        "future_pick_output_schema_not_fixed_to_calendar_years",
        not fixed_pick_fields,
        fixed_pick_fields or "none",
    )

    # Identity must be runtime/configuration supplied, not compiled into Python.
    add(checks, "perspective_runtime_selectable", "GM30_USER_ID" in engine)
    add(
        checks,
        "league_view_fallback_configured",
        cfg.get("perspective", {}).get("fallback") == "LEAGUE_VIEW",
    )
    suspicious_identity_constants = re.findall(
        r'^\s*(?:USER_ID|USER_MANAGER|USER_TEAM|DEFAULT_USER_ID)\s*=',
        engine,
        flags=re.MULTILINE,
    )
    add(
        checks,
        "no_compiled_manager_identity_constants",
        not suspicious_identity_constants,
        suspicious_identity_constants or "none",
    )

    # GM 3.0 owns data/gm; it must not write to simulator or legacy gm3 output roots.
    add(checks, "gm_output_root_is_data_gm", 'OUT = DATA / "gm"' in engine)
    add(checks, "no_legacy_data_gm3_output_root", 'OUT = DATA / "gm3"' not in engine)
    add(checks, "not_described_as_downstream_gm22", "downstream_only" not in engine.lower()
        and "downstream decision layer" not in engine.lower())
    write_calls = re.findall(r'(?:open|dump)\s*\(\s*["\']([^"\']+)', engine)
    simulator_writes = [x for x in write_calls if "simulator" in x.lower()]
    add(checks, "no_obvious_simulator_writes", not simulator_writes, simulator_writes or "none")

    # Simulator paths must remain templates in config and resolve for the live season.
    sim_paths = {
        k: v for k, v in cfg.get("paths", {}).items()
        if k.startswith("sim_") and isinstance(v, str)
    }
    add(
        checks,
        "simulator_paths_are_season_templates",
        bool(sim_paths) and all("{season}" in v for v in sim_paths.values()),
        sim_paths,
    )
    unresolved = [v for v in sim_paths.values() if "{season}" not in v]
    add(checks, "no_fixed_simulator_paths", not unresolved, unresolved or "none")

    # Build outputs, when present, should agree with the live season and architecture.
    manifest_path = GM_OUT / "manifest.json"
    if manifest_path.exists():
        manifest = load_json(manifest_path)
        add(checks, "manifest_architecture_primary", manifest.get("architecture") == "primary_gm_engine")
        add(
            checks,
            "manifest_season_matches_league",
            str(manifest.get("season") or "") == season,
            {"manifest": manifest.get("season"), "league": season},
        )
    else:
        add(checks, "manifest_available_after_first_run", True, "not built yet; runtime check deferred")

    validation_path = GM_OUT / "validation_report.json"
    if validation_path.exists():
        old = load_json(validation_path)
        add(checks, "embedded_gm_validation_passed", bool(old.get("passed")), old.get("checks"))
    else:
        add(checks, "embedded_gm_validation_available_after_first_run", True, "not built yet; runtime check deferred")

    # Config itself must remain manager-agnostic and season-agnostic.
    cfg_text = json.dumps(cfg)
    add(checks, "config_contains_no_fixed_user_id", not bool(re.search(r'"user_id"\s*:\s*"\d{8,}"', cfg_text)))
    add(checks, "config_contains_no_fixed_calendar_season", not bool(re.search(r'"season"\s*:\s*"?20\d{2}"?', cfg_text)))

    report(checks)


def report(checks):
    passed = all(x["passed"] for x in checks)
    payload = {
        "validator": "FSFFL-GM-3.0-ARCHITECTURE-GATE",
        "passed": passed,
        "checks": checks,
    }
    print(json.dumps(payload, indent=2))
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
