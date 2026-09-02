#!/usr/bin/env python3
"""Audit readiness for independent historical strategy-outcome calibration.

This audit distinguishes reconstructable historical facts from training-grade,
point-in-time model evidence. It never backfills historical projections or
market values with current information and never changes production behavior.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SPEC = DATA / "model_governance" / "strategy_outcome_dataset_spec.json"
OUT = DATA / "audit" / "strategy_outcome_readiness.json"

FACT_SOURCES = {
    "current_transactions": DATA / "transactions.json",
    "trade_ledger": DATA / "trade_ledger.json",
    "acquisition_ledger": DATA / "acquisition_ledger.json",
    "draft_ledger": DATA / "draft_ledger.json",
    "historical_state_provider": ROOT / "script" / "fsffl_historical_state_provider.py",
}
OUTCOME_SOURCES = {
    "transaction_performance_index": DATA / "transaction_performance_index.json",
    "draft_outcome_proxy_ledger": DATA / "draft_outcome_proxy_ledger.json",
    "waiver_outcome_proxy_ledger": DATA / "waiver_outcome_proxy_ledger.json",
}
KNOWN_TRAINING_ARTIFACT = DATA / "gm" / "state_weight_training_examples.json"

def load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default

def count_rows(path: Path) -> int:
    obj = load(path, None)
    if isinstance(obj, list):
        return len(obj)
    if isinstance(obj, dict):
        for key in ("rows", "transactions", "trades", "entries", "records", "items"):
            if isinstance(obj.get(key), list):
                return len(obj[key])
        return len(obj)
    return 0

def candidate_archives():
    """Find repository files that look like frozen point-in-time evidence."""
    projection = []
    market = []
    gm3 = []
    for p in DATA.rglob("*.json"):
        rel = p.relative_to(ROOT).as_posix().lower()
        if any(x in rel for x in ("/audit/", "/outputs/", "current_season")):
            continue
        if any(x in rel for x in ("historical", "archive", "snapshot", "source_history")):
            if "projection" in rel or "forecast" in rel:
                projection.append(rel)
            if "market" in rel or "fantasycalc" in rel or "value" in rel:
                market.append(rel)
            if "gm3" in rel or "franchise" in rel:
                gm3.append(rel)
    return {
        "projection_archives": sorted(set(projection)),
        "market_value_archives": sorted(set(market)),
        "gm3_bundle_archives": sorted(set(gm3)),
    }

def main():
    spec = load(SPEC, {})
    archives = candidate_archives()
    facts = {
        key: {"path": path.relative_to(ROOT).as_posix(), "exists": path.exists(), "rows": count_rows(path)}
        for key, path in FACT_SOURCES.items()
    }
    outcomes = {
        key: {"path": path.relative_to(ROOT).as_posix(), "exists": path.exists(), "rows": count_rows(path)}
        for key, path in OUTCOME_SOURCES.items()
    }

    has_fact_reconstruction = all(x["exists"] for x in facts.values())
    has_outcome_ledgers = all(x["exists"] for x in outcomes.values())
    has_projection_archive = bool(archives["projection_archives"])
    has_market_archive = bool(archives["market_value_archives"])
    has_gm3_archive = bool(archives["gm3_bundle_archives"])
    has_training_examples = KNOWN_TRAINING_ARTIFACT.exists()

    blockers = []
    if not has_projection_archive:
        blockers.append("NO_FROZEN_POINT_IN_TIME_PROJECTION_ARCHIVE_DETECTED")
    if not has_market_archive:
        blockers.append("NO_FROZEN_POINT_IN_TIME_MARKET_VALUE_ARCHIVE_DETECTED")
    if not has_gm3_archive:
        blockers.append("NO_FROZEN_HISTORICAL_GM3_BUNDLE_ARCHIVE_DETECTED")
    if not has_training_examples:
        blockers.append("NO_INDEPENDENT_STATE_WEIGHT_TRAINING_EXAMPLES_ARTIFACT")
    blockers.append("PLAUSIBLE_ALTERNATIVE_SET_RECONSTRUCTION_REQUIRES_ACTION_FAMILY_SPECIFIC_REVIEW")

    report = {
        "model_version": "FSFFL-Strategy-Outcome-Readiness-1.0",
        "authority": "RESEARCH_READINESS_ONLY",
        "production_behavior_changed": False,
        "spec_version": spec.get("model_version"),
        "facts": facts,
        "outcome_sources": outcomes,
        "candidate_archives": archives,
        "summary": {
            "historical_fact_reconstruction_ready": has_fact_reconstruction,
            "realized_outcome_ledgers_present": has_outcome_ledgers,
            "frozen_projection_archive_detected": has_projection_archive,
            "frozen_market_value_archive_detected": has_market_archive,
            "frozen_gm3_bundle_archive_detected": has_gm3_archive,
            "independent_state_weight_training_artifact_detected": has_training_examples,
            "pristine_temporal_state_weight_calibration_ready": (
                has_fact_reconstruction
                and has_outcome_ledgers
                and has_projection_archive
                and has_market_archive
                and has_training_examples
            ),
            "authoritative_empirical_state_weight_claim_allowed": False,
            "research_can_proceed_with_specification_and_non_pristine_sensitivity": True,
        },
        "blockers": blockers,
        "policy": {
            "current_market_backfill_for_historical_features_forbidden": True,
            "current_projection_backfill_for_historical_features_forbidden": True,
            "completed_actions_are_not_a_denominator_for_acceptance_probability": True,
            "reconstructed_non_pristine_examples_must_be_labeled": True,
            "multi_objective_validation_allowed_when_scalar_target_not_defensible": True,
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], indent=2))

if __name__ == "__main__":
    main()
