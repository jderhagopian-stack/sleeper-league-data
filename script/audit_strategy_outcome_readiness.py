#!/usr/bin/env python3
"""Audit readiness for independent historical strategy-outcome calibration.

This audit distinguishes reconstructable facts, provenance-only source
inventories, reconstructed-at-time bundles, and pristine point-in-time training
evidence. Filename heuristics are explicitly insufficient.
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
PROJECTION_SOURCE_INVENTORY = DATA / "model_validation" / "historical_projection_source_inventory.json"
HISTORICAL_RECONSTRUCTION_REGISTRY = DATA / "historical_gm3" / "reconstruction_parameter_registry.json"
CURRENT_REFERENCE_SNAPSHOT = DATA / "gm30_reference" / "snapshot" / "franchise_index.json"
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

def projection_evidence():
    inv = load(PROJECTION_SOURCE_INVENTORY, {})
    sources = inv.get("sources") or []
    eligible = [
        row for row in sources
        if row.get("status") == "ELIGIBLE_PRESEASON"
        and row.get("snapshot_date")
        and row.get("raw_stat_projection_available") is True
    ]
    seasons = sorted({int(row["season"]) for row in eligible if row.get("season") is not None})
    positions = sorted({str(row.get("position")) for row in eligible if row.get("position")})
    # The inventory establishes provenance eligibility. It does not itself
    # contain the full player-level frozen observations needed for state-weight
    # decision calibration.
    raw_training_files = [
        p.relative_to(ROOT).as_posix()
        for p in (DATA / "model_validation").glob("*historical_projection*")
        if p.suffix.lower() in {".csv", ".parquet", ".jsonl"}
    ]
    return {
        "inventory_path": PROJECTION_SOURCE_INVENTORY.relative_to(ROOT).as_posix(),
        "inventory_exists": PROJECTION_SOURCE_INVENTORY.exists(),
        "eligible_source_position_season_cells": len(eligible),
        "eligible_seasons": seasons,
        "eligible_positions": positions,
        "inventory_authoritative_calibration_ready": bool(inv.get("authoritative_calibration_ready")),
        "player_level_frozen_training_files": raw_training_files,
        "provenance_evidence_available": bool(eligible),
        "pristine_player_level_archive_detected": bool(raw_training_files),
        "classification": (
            "PROVENANCE_ELIGIBLE_SOURCE_INVENTORY_ONLY"
            if eligible and not raw_training_files
            else "PLAYER_LEVEL_ARCHIVE_PRESENT" if raw_training_files
            else "NO_ELIGIBLE_PROJECTION_EVIDENCE"
        ),
    }

def reconstruction_evidence():
    reg = load(HISTORICAL_RECONSTRUCTION_REGISTRY, {})
    policy = reg.get("policy") or {}
    source_files = sorted(
        p.relative_to(ROOT).as_posix()
        for p in (DATA / "historical_gm3" / "sources").glob("*.json")
    )
    current_ref = load(CURRENT_REFERENCE_SNAPSHOT, {})
    return {
        "registry_path": HISTORICAL_RECONSTRUCTION_REGISTRY.relative_to(ROOT).as_posix(),
        "registry_exists": HISTORICAL_RECONSTRUCTION_REGISTRY.exists(),
        "historical_source_files": source_files,
        "historical_input_class": policy.get("historical_input_class"),
        "strict_out_of_sample_backtest_eligible": policy.get("strict_out_of_sample_backtest_eligible"),
        "authoritative_recommendation_allowed": policy.get("authoritative_recommendation_allowed"),
        "classification": "RECONSTRUCTED_NON_PRISTINE",
        "current_reference_snapshot": {
            "path": CURRENT_REFERENCE_SNAPSHOT.relative_to(ROOT).as_posix(),
            "generated_at_utc": current_ref.get("generated_at_utc"),
            "season": current_ref.get("season"),
            "classification": "CURRENT_REGRESSION_REFERENCE_NOT_HISTORICAL_TRAINING_EVIDENCE",
        },
    }

def market_evidence(reconstruction):
    dated = []
    for rel in reconstruction.get("historical_source_files") or []:
        obj = load(ROOT / rel, {})
        as_of = obj.get("as_of_utc")
        player_source = obj.get("player_source") or {}
        if as_of and player_source.get("published") and player_source.get("values"):
            dated.append({
                "path": rel,
                "as_of_utc": as_of,
                "publisher": player_source.get("publisher"),
                "published": player_source.get("published"),
                "coverage": len(player_source.get("values") or {}),
                "role": obj.get("market_scale"),
            })
    # Dated source values can support case-specific reconstruction. A handful of
    # source files are not a league-wide multi-season immutable market archive.
    return {
        "dated_case_specific_market_sources": dated,
        "dated_case_specific_market_evidence_detected": bool(dated),
        "pristine_multi_season_market_archive_detected": False,
        "classification": (
            "DATED_CASE_SPECIFIC_RECONSTRUCTION_EVIDENCE"
            if dated else "NO_DATED_MARKET_EVIDENCE"
        ),
    }

def main():
    spec = load(SPEC, {})
    facts = {
        key: {"path": path.relative_to(ROOT).as_posix(), "exists": path.exists(), "rows": count_rows(path)}
        for key, path in FACT_SOURCES.items()
    }
    outcomes = {
        key: {"path": path.relative_to(ROOT).as_posix(), "exists": path.exists(), "rows": count_rows(path)}
        for key, path in OUTCOME_SOURCES.items()
    }

    projections = projection_evidence()
    reconstruction = reconstruction_evidence()
    market = market_evidence(reconstruction)

    has_fact_reconstruction = all(x["exists"] for x in facts.values())
    has_outcome_ledgers = all(x["exists"] for x in outcomes.values())
    has_pristine_projection_archive = projections["pristine_player_level_archive_detected"]
    has_pristine_market_archive = market["pristine_multi_season_market_archive_detected"]
    has_pristine_gm3_archive = (
        reconstruction.get("strict_out_of_sample_backtest_eligible") is True
        and reconstruction.get("historical_input_class") == "FROZEN_POINT_IN_TIME"
    )
    has_training_examples = KNOWN_TRAINING_ARTIFACT.exists()

    blockers = []
    if not has_pristine_projection_archive:
        blockers.append("NO_PRISTINE_PLAYER_LEVEL_POINT_IN_TIME_PROJECTION_ARCHIVE")
    if not has_pristine_market_archive:
        blockers.append("NO_PRISTINE_MULTI_SEASON_POINT_IN_TIME_MARKET_ARCHIVE")
    if not has_pristine_gm3_archive:
        blockers.append("HISTORICAL_GM3_IS_RECONSTRUCTED_NOT_STRICT_OUT_OF_SAMPLE")
    if not has_training_examples:
        blockers.append("NO_INDEPENDENT_STATE_WEIGHT_TRAINING_EXAMPLES_ARTIFACT")
    blockers.append("PLAUSIBLE_ALTERNATIVE_SET_RECONSTRUCTION_REQUIRES_ACTION_FAMILY_SPECIFIC_REVIEW")

    pristine_ready = (
        has_fact_reconstruction
        and has_outcome_ledgers
        and has_pristine_projection_archive
        and has_pristine_market_archive
        and has_pristine_gm3_archive
        and has_training_examples
    )

    report = {
        "model_version": "FSFFL-Strategy-Outcome-Readiness-1.1",
        "authority": "RESEARCH_READINESS_ONLY",
        "production_behavior_changed": False,
        "spec_version": spec.get("model_version"),
        "facts": facts,
        "outcome_sources": outcomes,
        "projection_evidence": projections,
        "historical_reconstruction_evidence": reconstruction,
        "market_evidence": market,
        "summary": {
            "historical_fact_reconstruction_ready": has_fact_reconstruction,
            "realized_outcome_ledgers_present": has_outcome_ledgers,
            "projection_provenance_inventory_available": projections["provenance_evidence_available"],
            "pristine_frozen_projection_archive_detected": has_pristine_projection_archive,
            "dated_case_specific_market_evidence_detected": market["dated_case_specific_market_evidence_detected"],
            "pristine_frozen_market_archive_detected": has_pristine_market_archive,
            "historical_gm3_reconstruction_available": bool(reconstruction["historical_source_files"]),
            "pristine_frozen_gm3_bundle_archive_detected": has_pristine_gm3_archive,
            "independent_state_weight_training_artifact_detected": has_training_examples,
            "pristine_temporal_state_weight_calibration_ready": pristine_ready,
            "authoritative_empirical_state_weight_claim_allowed": False,
            "non_pristine_reconstruction_sensitivity_allowed": True,
            "projection_source_benchmark_research_allowed_under_its_own_provenance_gate": True,
        },
        "blockers": blockers,
        "policy": {
            "filename_or_path_name_is_not_evidence_of_frozen_archive_status": True,
            "current_reference_snapshot_is_not_historical_training_evidence": True,
            "reconstructed_at_time_is_not_strict_out_of_sample": True,
            "provenance_inventory_is_not_player_level_training_dataset": True,
            "case_specific_dated_market_source_is_not_multi_season_market_archive": True,
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
