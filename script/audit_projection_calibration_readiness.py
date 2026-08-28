#!/usr/bin/env python3
"""Audit empirical readiness of FSFFL projection means and uncertainty.

This is a governance audit only. It does not tune or change production outputs.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
BUILDER = ROOT / "script" / "build_fsffl_weekly_projections.py"
MANIFEST = DATA / "model_validation" / "projection_calibration_manifest.json"
OUT = DATA / "audit" / "projection_calibration_readiness_audit.json"


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    text = BUILDER.read_text(encoding="utf-8")
    manifest = load_json(MANIFEST)

    expected_runtime_markers = {
        "history_seasons": r"HISTORY_SEASONS\s*=\s*3\b",
        "min_player_games": r"MIN_PLAYER_GAMES\s*=\s*8\b",
        "min_position_games": r"MIN_POSITION_GAMES\s*=\s*80\b",
        "position_sd_floor": r"POSITION_SD_FLOOR\s*=",
        "cv_bounds": r"max\(0\.15,\s*min\(1\.35,",
        "shrinkage_cap": r"weight\s*=\s*min\(0\.75,\s*n\s*/\s*32\.0\)",
        "median_offset": r"mean\s*-\s*0\.08\s*\*\s*week_sd",
        "active_probability_one": r"active_probability\s*=\s*0\.0\s*if\s*is_bye\s*else\s*1\.0",
        "normal_percentiles": r"percentile_normal\(",
    }
    runtime_markers = {
        key: bool(re.search(pattern, text, flags=re.MULTILINE))
        for key, pattern in expected_runtime_markers.items()
    }

    # Historical outcomes are not historical forecasts. We require artifacts
    # whose path/name indicates contemporaneous projections/forecasts and that
    # live outside the active simulator source tree.
    forecast_candidates = []
    for path in DATA.rglob("*"):
        if not path.is_file():
            continue
        low = str(path.relative_to(ROOT)).lower()
        if not any(token in low for token in ("projection", "forecast")):
            continue
        if "/simulator/2026/" in low.replace("\\", "/"):
            continue
        if path == MANIFEST:
            continue
        if path.suffix.lower() not in {".json", ".csv", ".parquet"}:
            continue
        forecast_candidates.append(str(path.relative_to(ROOT)))

    historical_outcomes = sorted(
        str(p.relative_to(ROOT))
        for p in (DATA / "stats" / "nfl").glob("*/player_weekly_normalized.json")
    ) if (DATA / "stats" / "nfl").exists() else []

    empirical_forecast_archive_detected = len(forecast_candidates) > 0
    all_runtime_markers_detected = all(runtime_markers.values())

    finding = {
        "id": "PROJECTION-CALIBRATION-READINESS-001",
        "historical_realized_outcome_seasons": len(historical_outcomes),
        "historical_forecast_candidate_count": len(forecast_candidates),
        "historical_forecast_candidates": forecast_candidates[:50],
        "runtime_assumption_markers": runtime_markers,
        "all_expected_runtime_markers_detected": all_runtime_markers_detected,
        "mean_temporal_oos_calibration_ready": empirical_forecast_archive_detected,
        "uncertainty_forecast_residual_calibration_ready": empirical_forecast_archive_detected,
        "authoritative_empirical_claim_allowed": empirical_forecast_archive_detected,
        "production_behavior_changed": False,
        "interpretation": (
            "Historical realized scoring is available, but it cannot by itself "
            "identify contemporaneous forecast bias or forecast-error variance."
        ),
    }

    report = {
        "schema_version": "1.0",
        "manifest_version": manifest["version"],
        "policy": {
            "software_validation_is_not_empirical_validation": True,
            "realized_score_dispersion_is_not_forecast_error": True,
            "lookahead_backfill_for_historical_forecasts_forbidden": True,
            "promotion_requires_temporal_holdout_improvement": True,
        },
        "finding": finding,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")

    if not all_runtime_markers_detected:
        missing = [k for k, v in runtime_markers.items() if not v]
        raise SystemExit(f"Projection runtime changed; re-audit assumptions: {missing}")

    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
