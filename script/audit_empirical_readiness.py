#!/usr/bin/env python3
"""Audit whether FSFFL model families have the data needed for empirical validation.

This is deliberately separate from software/regression validation.  It does not
change model outputs or coefficients.  It records whether the repository holds
the timestamped/frozen evidence required to make specific empirical claims.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
SCRIPT = ROOT / "script"
OUT = DATA / "audit"
OUT.mkdir(parents=True, exist_ok=True)

MODEL_VERSION = "FSFFL-Empirical-Readiness-Audit-1.0"
HISTORICAL_SEASONS = (2022, 2023, 2024, 2025)


def existing(paths):
    return [str(p.relative_to(ROOT)) for p in paths if p.exists()]


def simulator_seasons():
    root = DATA / "simulator"
    if not root.exists():
        return []
    out = []
    for p in root.iterdir():
        if p.is_dir() and p.name.isdigit():
            out.append(int(p.name))
    return sorted(out)


def frozen_bundle_files():
    root = DATA / "historical_gm3"
    if not root.exists():
        return []
    rows = []
    for season_dir in root.iterdir():
        if not season_dir.is_dir() or not season_dir.name.isdigit():
            continue
        for p in season_dir.glob("*.json"):
            if p.is_file():
                rows.append(str(p.relative_to(ROOT)))
    return sorted(rows)


def weekly_outcome_files():
    candidates = [
        DATA / "stats" / "fsffl" / str(y) / "player_weekly_fsffl.json"
        for y in HISTORICAL_SEASONS
    ]
    return existing(candidates)


def historical_projection_artifacts():
    """Return simulator projection inputs for seasons completed before 2026.

    A weekly outcome file is not a forecast.  We only count timestamped/frozen
    projection inputs under historical simulator seasons here.
    """
    rows = []
    root = DATA / "simulator"
    for y in HISTORICAL_SEASONS:
        season_root = root / str(y)
        if not season_root.exists():
            continue
        for pattern in (
            "**/*projection*.json", "**/*projection*.csv",
            "**/*preseason*.json", "**/*preseason*.csv",
            "**/*forecast*.json", "**/*forecast*.csv",
        ):
            for p in season_root.glob(pattern):
                if p.is_file():
                    rows.append(str(p.relative_to(ROOT)))
    return sorted(set(rows))


def source_evidence():
    weekly = (SCRIPT / "build_fsffl_weekly_projections.py").read_text(encoding="utf-8")
    gm30 = (SCRIPT / "build_fsffl_gm30.py").read_text(encoding="utf-8")
    historical = (SCRIPT / "historical_trade_gm3_adapter.py").read_text(encoding="utf-8")
    return {
        "weekly_uncertainty": {
            "raw_outcome_sd_used": "robust_sd" in weekly,
            "position_sd_floor_present": "POSITION_SD_FLOOR" in weekly,
            "player_position_shrinkage_present": "MIN_PLAYER_GAMES" in weekly or "n / (n +" in weekly,
            "normal_distribution_present": '"normal"' in weekly.lower() or "normal" in weekly.lower(),
            "availability_default_present": "availability" in weekly.lower(),
        },
        "pick_model": {
            "heuristic_scenario_formula_present": "collapse_risk" in gm30 and "early_scenario_weight" in gm30,
            "uncertainty_optionality_bonus_present": "0.18 * core.clamp(uncertainty" in gm30,
            "horizon_uncertainty_present": "uncertainty =" in gm30 and "horizon" in gm30,
        },
        "historical_adapter": {
            "supports_historical_bundle_schema": "REQUIRED_BUNDLE_KEYS" in historical,
            "supports_reconstructed_at_time": "GRADED_RECONSTRUCTED_AT_TIME" in historical,
            "refuses_current_value_backfill": "current_values_used\": False" in historical or '"current_values_used": False' in historical,
            "not_graded_path_present": "NOT_GRADED" in historical,
        },
    }


def main():
    outcomes = weekly_outcome_files()
    forecasts = historical_projection_artifacts()
    bundles = frozen_bundle_files()
    sim_seasons = simulator_seasons()
    src = source_evidence()

    findings = [
        {
            "id": "EMPIRICAL-PROJECTION-001",
            "severity": "CRITICAL",
            "status": "FORECAST_BACKTEST_DATA_UNAVAILABLE" if not forecasts else "FORECAST_BACKTEST_DATA_PRESENT",
            "claim": "Projection mean/forecast-error calibration",
            "historical_weekly_outcome_files": outcomes,
            "historical_forecast_artifacts": forecasts,
            "observation": (
                "Historical realized weekly outcomes are present, but no completed-season frozen projection artifacts were found. "
                "Raw outcome dispersion can estimate realized scoring variability; it cannot identify forecast residual error or validate projection-source weights."
                if outcomes and not forecasts else
                "Historical forecast artifacts were found and require timestamp/provenance validation before use."
            ),
            "authoritative_empirical_claim_allowed": bool(forecasts),
        },
        {
            "id": "EMPIRICAL-UNCERTAINTY-001",
            "severity": "CRITICAL",
            "status": "MIXED_EMPIRICAL_PROXY_AND_HEURISTICS",
            "claim": "Weekly projection uncertainty calibration",
            "source_evidence": src["weekly_uncertainty"],
            "observation": (
                "The uncertainty layer uses historical realized scoring dispersion as a proxy, with heuristic floors/shrinkage/distribution/availability mechanics. "
                "Without archived forecasts, distributional forecast calibration (coverage/CRPS by horizon) cannot currently be established from this repository."
            ),
            "authoritative_empirical_claim_allowed": False,
        },
        {
            "id": "EMPIRICAL-HISTORICAL-TRADE-001",
            "severity": "CRITICAL",
            "status": "FRAMEWORK_READY_DATASET_NOT_READY" if not bundles else "FROZEN_BUNDLES_PRESENT",
            "claim": "At-the-time GM3 historical trade backtest",
            "simulator_seasons_present": sim_seasons,
            "frozen_gm3_bundle_count": len(bundles),
            "frozen_gm3_bundles": bundles,
            "source_evidence": src["historical_adapter"],
            "observation": (
                "The historical adapter refuses hindsight/current-value substitution. Reconstructed-at-time inputs can preserve retrospective decision functionality, "
                "but no archived-at-time GM3 bundles are present for pristine empirical backtesting. Therefore authoritative GM3 backtest cases remain zero."
                if not bundles else
                "Frozen bundles exist; each must still pass provenance/completeness checks before inclusion in an empirical backtest."
            ),
            "authoritative_empirical_claim_allowed": bool(bundles),
        },
        {
            "id": "EMPIRICAL-PICK-001",
            "severity": "HIGH",
            "status": "HEURISTIC_ACTIVE_REQUIRES_INCREMENTAL_VALIDATION",
            "claim": "Future-pick horizon and optionality economics",
            "source_evidence": src["pick_model"],
            "observation": (
                "GM3 uses heuristic early/mid/late scenario formulas and separately increases optionality with modeled uncertainty. "
                "Because external dynasty pick values already price an unspent pick's flexibility, any incremental uncertainty/optionality premium requires residual validation to avoid double counting."
            ),
            "authoritative_empirical_claim_allowed": False,
        },
    ]

    payload = {
        "model_version": MODEL_VERSION,
        "purpose": "Separate empirical-data readiness from software/regression validation.",
        "software_validation_implication": "none; passing CI does not promote empirical status",
        "summary": {
            "historical_outcome_seasons_available": len(outcomes),
            "historical_projection_artifact_count": len(forecasts),
            "frozen_historical_gm3_bundle_count": len(bundles),
            "critical_empirical_blocks": sum(
                1 for x in findings
                if x["severity"] == "CRITICAL" and not x["authoritative_empirical_claim_allowed"]
            ),
        },
        "findings": findings,
    }
    (OUT / "empirical_readiness_audit.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )
    print(json.dumps(payload["summary"], indent=2))


if __name__ == "__main__":
    main()
