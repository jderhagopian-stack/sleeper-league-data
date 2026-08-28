#!/usr/bin/env python3
"""Audit empirical readiness of FSFFL projection means and uncertainty.

This is a governance audit only. It does not tune or change production outputs.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
BUILDER = ROOT / "script" / "build_fsffl_weekly_projections.py"
MANIFEST = DATA / "model_validation" / "projection_calibration_manifest.json"
OUT = DATA / "audit" / "projection_calibration_readiness_audit.json"


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def payload_has_player_projection_shape(payload: Any) -> bool:
    """Conservatively identify player-level forecast/projection payloads.

    A filename containing ``forecast`` is not enough: pick forecasts, standings
    forecasts, and other model outputs belong to different calibration families.
    This classifier deliberately favors false negatives over false positives so
    governance never promotes empirical readiness merely because an unrelated
    forecast artifact exists.
    """
    player_tokens = {
        "player_id", "player_name", "sleeper_id", "gsis_id", "position",
    }
    projection_tokens = {
        "projection", "projected", "projected_points", "projected_ppg",
        "fantasy_points", "mean", "median", "p25", "p75",
    }
    time_tokens = {
        "as_of", "as_of_date", "timestamp", "created_at", "season", "week",
    }

    def walk(node: Any, depth: int = 0) -> tuple[set[str], int]:
        if depth > 6:
            return set(), 0
        keys: set[str] = set()
        row_like = 0
        if isinstance(node, dict):
            local = {str(k).lower() for k in node.keys()}
            keys |= local
            if local & player_tokens and local & projection_tokens:
                row_like += 1
            for value in node.values():
                child_keys, child_rows = walk(value, depth + 1)
                keys |= child_keys
                row_like += child_rows
        elif isinstance(node, list):
            for value in node[:100]:
                child_keys, child_rows = walk(value, depth + 1)
                keys |= child_keys
                row_like += child_rows
        return keys, row_like

    keys, row_like = walk(payload)
    return (
        row_like > 0
        and bool(keys & player_tokens)
        and bool(keys & projection_tokens)
        and bool(keys & time_tokens)
    )


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

    # Historical outcomes are not historical forecasts. Candidate paths must
    # both look projection-related and have player-level projection structure.
    # This prevents unrelated artifacts (for example pick forecasts) from
    # falsely promoting projection calibration readiness.
    forecast_candidates = []
    rejected_forecast_named_artifacts = []
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

        qualified = False
        # JSON can be structurally inspected without adding dependencies. CSV
        # and parquet are conservatively left unqualified here; if a genuine
        # historical archive is added, the classifier should be extended and
        # validated in the same change rather than silently promoting it.
        if path.suffix.lower() == ".json":
            try:
                qualified = payload_has_player_projection_shape(load_json(path))
            except (OSError, ValueError, TypeError):
                qualified = False

        rel = str(path.relative_to(ROOT))
        if qualified:
            forecast_candidates.append(rel)
        else:
            rejected_forecast_named_artifacts.append(rel)

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
        "forecast_named_artifacts_rejected_as_non_player_archives": (
            rejected_forecast_named_artifacts[:50]
        ),
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
            "unrelated_forecast_artifacts_do_not_establish_projection_readiness": True,
            "promotion_requires_temporal_holdout_improvement": True,
        },
        "finding": finding,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")

    if not all_runtime_markers_detected:
        missing = [k for k, v in runtime_markers.items() if not v]
        raise SystemExit(f"Projection runtime changed; re-audit assumptions: {missing}")

    # The manifest is itself a governance claim. Do not allow implementation
    # and manifest to disagree silently in either direction.
    manifest_claim = bool(manifest.get("authoritative_empirical_claim_allowed"))
    if manifest_claim != empirical_forecast_archive_detected:
        raise SystemExit(
            "Projection calibration manifest/readiness disagreement: "
            f"manifest={manifest_claim}, detected={empirical_forecast_archive_detected}"
        )

    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
