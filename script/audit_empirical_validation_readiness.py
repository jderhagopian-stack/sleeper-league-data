#!/usr/bin/env python3
"""Final FSFFL model-governance gate for empirical validation readiness.

This audit deliberately distinguishes software validation from empirical model
validation. It never invents historical inputs, never backfills present-day
values into old decisions, and never promotes a coefficient merely because a
regression workflow passes.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OUT = DATA / "audit"
REGISTRY = DATA / "model_parameter_registry.json"
HIST = DATA / "historical_gm3"
MODEL_VERSION = "FSFFL-Empirical-Validation-Readiness-1.1"


def loadj(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    registry = loadj(REGISTRY, {}) or {}
    params = list(registry.get("parameters") or [])
    learned_or_material = [p for p in params if p.get("evidence_tier") != "RULE_DEFINED"]
    authoritative_non_rule = [p for p in learned_or_material if p.get("authoritative_use") is True]
    provisional = [p for p in learned_or_material if p.get("authoritative_use") is False]

    bundle_files = sorted(HIST.glob("*/*.json")) if HIST.exists() else []
    complete_bundles = []
    incomplete_bundles = []
    for path in bundle_files:
        d = loadj(path, {}) or {}
        # A historical bundle is useful for promotion only if it explicitly says
        # it is frozen and complete; absence of those claims is not inferred.
        complete = bool(d.get("time_frozen") is True and d.get("complete") is True)
        (complete_bundles if complete else incomplete_bundles).append(str(path.relative_to(ROOT)))

    empirical_promotion_ready = bool(complete_bundles) and not authoritative_non_rule
    blockers = []
    if not complete_bundles:
        blockers.append(
            "No complete, explicitly time-frozen historical GM3 bundles are available. "
            "Reconstructed-at-time analysis can preserve historical decision functionality, but at-the-time trade-ranking coefficients cannot be promoted empirically without truly archived leakage-safe inputs."
        )
    if authoritative_non_rule:
        blockers.append(
            "One or more non-rule-defined parameter families are marked authoritative before the final empirical gate."
        )

    payload = {
        "model_version": MODEL_VERSION,
        "production_behavior_changed": False,
        "software_validation_is_empirical_validation": False,
        "policy": {
            "current_values_may_not_backfill_historical_decisions": True,
            "missing_archived_inputs_may_use_reconstructed_at_time_analysis": True,
            "reconstructed_at_time_results_are_not_pristine_out_of_sample_backtests": True,
            "coefficient_promotion_requires_out_of_sample_improvement": True,
            "rank_stability_alone_is_not_validation": True,
            "no_output_tuning_to_match_expectations": True,
        },
        "registry": {
            "parameter_count": len(params),
            "non_rule_material_parameter_count": len(learned_or_material),
            "non_rule_authoritative_count": len(authoritative_non_rule),
            "non_rule_non_authoritative_count": len(provisional),
            "non_rule_authoritative_ids": [p.get("id") for p in authoritative_non_rule],
        },
        "historical_inputs": {
            "historical_gm3_directory_exists": HIST.exists(),
            "bundle_file_count": len(bundle_files),
            "complete_time_frozen_bundle_count": len(complete_bundles),
            "complete_time_frozen_bundles": complete_bundles,
            "incomplete_or_unasserted_bundles": incomplete_bundles,
        },
        "empirical_promotion_ready": empirical_promotion_ready,
        "blockers": blockers,
        "status": "EMPIRICAL_PROMOTION_READY" if empirical_promotion_ready else "EMPIRICAL_PROMOTION_BLOCKED_INPUTS_OR_GOVERNANCE",
        "next_evidence_required": [
            "Versioned, timestamp-frozen projection/market/injury/usage inputs for strict historical backtests; reconstructed-at-time inputs remain valid for functional retrospective analysis",
            "A defensible prediction or decision target for each calibrated family",
            "Time-ordered train/validation/test splits or equivalent leakage-safe out-of-sample design",
            "Family-by-family and grouped ablations for overlapping final-score channels",
            "Promotion only when the challenger improves the prespecified validation metric and remains stable under sensitivity checks",
        ],
    }
    (OUT / "empirical_validation_readiness.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps({
        "status": payload["status"],
        "complete_time_frozen_bundle_count": len(complete_bundles),
        "non_rule_authoritative_count": len(authoritative_non_rule),
        "production_behavior_changed": False,
    }, indent=2))

    # Missing empirical data is a documented blocker, not a software failure.
    # Governance drift that marks unvalidated non-rule coefficients authoritative
    # *is* a failure and must stop promotion.
    if authoritative_non_rule:
        raise SystemExit("Unvalidated non-rule parameter family marked authoritative")


if __name__ == "__main__":
    main()
