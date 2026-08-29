#!/usr/bin/env python3
"""Run the empirically calibrated weekly layer on Native V2 season means."""
from __future__ import annotations

import json
from pathlib import Path

import build_fsffl_weekly_projections as weekly

ORIGINAL_LOAD_JSON = weekly.load_json


def load_json_prefer_native(path: Path):
    path = Path(path)
    if path.name == "preseason_fsffl_points.json":
        native = path.with_name("native_preseason_fsffl_points.json")
        if native.exists():
            return ORIGINAL_LOAD_JSON(native)
    return ORIGINAL_LOAD_JSON(path)


def write_json(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main():
    weekly.load_json = load_json_prefer_native
    weekly.main()

    league = ORIGINAL_LOAD_JSON(weekly.DATA / "league.json")
    season = str(league.get("season") or "").strip()
    sim_dir = weekly.SIM_ROOT / season
    native_path = sim_dir / "sources" / "native_preseason_fsffl_points.json"
    weekly_path = sim_dir / "inputs" / "player_weekly_projections.json"
    audit_path = sim_dir / "outputs" / "weekly_projection_audit.json"

    native = ORIGINAL_LOAD_JSON(native_path)
    out = ORIGINAL_LOAD_JSON(weekly_path)
    audit = ORIGINAL_LOAD_JSON(audit_path)
    if not native or not out:
        raise RuntimeError("Native baseline or weekly output missing after build")

    baseline_audit = native.get("audit") or {}
    fallback_players = int(baseline_audit.get("fallback_players") or 0)
    fully_native = fallback_players == 0 and (native.get("governance") or {}).get("external_projection_values_used") is False
    out["source"] = (
        "FSFFL Native V2 raw-stat season means: validated veteran role-aware model plus native no-history role/age model; "
        "weekly distribution width calibrated from known NFL weekly outcomes"
        if fully_native else
        "FSFFL Native V2 raw-stat season means with explicitly identified fallback coverage; weekly distribution width calibrated from known NFL weekly outcomes"
    )
    out["model_stage"] = "native_v2_preseason_weekly_baseline"
    out["season_mean_model"] = "FSFFL-Native-V2-role-aware"
    out["external_projection_blend_enabled"] = False
    out["external_projection_values_used"] = not fully_native
    out["native_mean_coverage_pct"] = baseline_audit.get("native_coverage_pct")
    write_json(weekly_path, out)

    audit["season_mean_source"] = "native_preseason_fsffl_points.json"
    audit["native_mean_audit"] = baseline_audit
    audit["external_projection_blend_enabled"] = False
    audit["external_projection_values_used"] = not fully_native
    audit.setdefault("important_limitations", [])
    audit["important_limitations"] = [x for x in audit["important_limitations"] if "Razzball" not in str(x) and "provisional preseason fallback" not in str(x)]
    if fully_native:
        audit["important_limitations"].append(
            "Current-season role/depth input is timestamped and leakage-safe, but its present data provider remains provisional for commercial dependency until a free commercially suitable replacement or rights clearance is documented."
        )
    else:
        audit["important_limitations"].append(
            "Some players remain outside the native mean models and retain explicitly identified fallback coverage."
        )
    write_json(audit_path, audit)

    print(json.dumps({
        "status": "PASS",
        "season": season,
        "weekly_players": len(out.get("players") or {}),
        "native_mean_coverage_pct": baseline_audit.get("native_coverage_pct"),
        "external_projection_values_used": not fully_native,
        "weekly_quality_gate_passed": (audit.get("quality_gate") or {}).get("passed"),
    }, indent=2))


if __name__ == "__main__":
    main()
