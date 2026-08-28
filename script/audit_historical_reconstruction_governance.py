#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REG = ROOT / "data" / "historical_gm3" / "reconstruction_parameter_registry.json"
BUILDER = ROOT / "script" / "build_historical_gm3_bundle.py"
OUT = ROOT / "data" / "audit" / "historical_reconstruction_governance.json"


def main():
    registry = json.loads(REG.read_text(encoding="utf-8"))
    source = BUILDER.read_text(encoding="utf-8")
    families = registry.get("parameter_families") or []
    missing_literals = []
    unbounded_provisional = []
    for family in families:
        tier = family.get("evidence_tier")
        bounds = family.get("bounds") or {}
        if tier == "ASSUMPTION_SENSITIVE_PROVISIONAL" and not bounds:
            unbounded_provisional.append(family.get("id"))
        for lit in family.get("literals") or []:
            if str(lit) not in source:
                missing_literals.append({"family": family.get("id"), "literal": lit})

    policy = registry.get("policy") or {}
    report = {
        "model_version": "FSFFL-Historical-Reconstruction-Governance-Audit-1.0",
        "registry_model_version": registry.get("model_version"),
        "software_validation": {
            "registry_present": REG.exists(),
            "builder_present": BUILDER.exists(),
            "registered_literals_found_in_builder": not missing_literals,
            "missing_registered_literals": missing_literals,
            "unbounded_provisional_families": unbounded_provisional,
        },
        "empirical_validation": {
            "strict_out_of_sample_backtest_eligible": False,
            "authoritative_recommendation_allowed": False,
            "status": "NOT_EMPIRICALLY_VALIDATED",
            "reason": "Reconstructed-at-time inputs contain bounded fallback/proxy families that have not yet demonstrated held-out calibration improvement.",
        },
        "sensitivity_policy": {
            "grouped_families_registered": [f.get("id") for f in families],
            "bounds_available": {f.get("id"): f.get("bounds") or {} for f in families},
            "automatic_authority_downgrade": True,
            "interpretation": "Until executable grouped perturbation is wired into the reconstruction builder, every RECONSTRUCTED_AT_TIME result is conservatively non-authoritative rather than allowing untested sensitivity to drive a recommendation.",
        },
        "promotion_policy": {
            "learned_coefficients_versioned": True,
            "promotion_requires_held_out_improvement": bool(policy.get("promotion_requires_held_out_validation_improvement")),
            "reconstructed_cases_excluded_from_pristine_oos_claims": True,
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    assert not missing_literals, missing_literals
    assert not unbounded_provisional, unbounded_provisional
    assert policy.get("authoritative_recommendation_allowed") is False
    assert policy.get("strict_out_of_sample_backtest_eligible") is False
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
