#!/usr/bin/env python3
"""Audit whether uncertain FSFFL concepts were incorrectly collapsed to zero authority.

This audit distinguishes:
- genuinely duplicative/same-source signals;
- signals that belong outside economic utility;
- credible economic effects whose magnitude is uncertain;
- mixed registry families that must be decomposed before authority decisions.

It does not introduce coefficients or change production scoring.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "data" / "model_parameter_registry.json"
POLICY = ROOT / "data" / "model_governance" / "parameter_authority_policy.json"
OUT = ROOT / "data" / "audit" / "parameter_authority_missing_value_audit.json"

VALID_MODES = {
    "ACTIVE_STRUCTURAL_OR_EVIDENCE_DERIVED",
    "ACTIVE_BOUNDED_PROVISIONAL_PRIOR",
    "DIAGNOSTIC_DUPLICATIVE",
    "DIAGNOSTIC_WRONG_SEMANTIC_ROLE",
    "INACTIVE_UNCERTAIN_MAGNITUDE_REVIEW",
    "SPLIT_REQUIRED_MIXED_FAMILY",
}

def main() -> None:
    reg = json.loads(REGISTRY.read_text(encoding="utf-8"))
    pol = json.loads(POLICY.read_text(encoding="utf-8"))
    params = {p["id"]: p for p in reg.get("parameters") or []}
    reviews = pol.get("family_reviews") or []

    errors = []
    findings = []
    for review in reviews:
        pid = review.get("id")
        mode = review.get("mode")
        if pid not in params:
            errors.append(f"policy review references missing registry family: {pid}")
            continue
        if mode not in VALID_MODES:
            errors.append(f"{pid}: invalid authority mode {mode}")
            continue
        p = params[pid]
        uncertain_zero_risk = (
            mode == "INACTIVE_UNCERTAIN_MAGNITUDE_REVIEW"
            and p.get("authoritative_use") is False
        )
        mixed_family = mode == "SPLIT_REQUIRED_MIXED_FAMILY"
        findings.append({
            "id": pid,
            "component": p.get("component"),
            "registry_status": p.get("status"),
            "registry_evidence_tier": p.get("evidence_tier"),
            "registry_authoritative_use": p.get("authoritative_use"),
            "authority_mode": mode,
            "concept_status": review.get("concept_status"),
            "reason": review.get("reason"),
            "next_step": review.get("next_step"),
            "zero_may_be_unjustified_point_estimate": uncertain_zero_risk,
            "family_decomposition_required": mixed_family,
        })

    principles = pol.get("principles") or {}
    required_principles = {
        "zero_is_a_parameter",
        "uncertain_magnitude_is_not_evidence_of_zero_effect",
        "credible_distinct_effects_may_use_bounded_provisional_priors",
        "provisional_priors_must_expose_uncertainty",
        "provisional_priors_must_support_sensitivity_analysis",
        "duplicate_or_misclassified_signals_remain_zero_incremental_authority",
        "production_promotion_requires_no_double_counting",
        "empirical_recalibration_should_replace_or_narrow_priors_over_time",
    }
    missing_principles = sorted(k for k in required_principles if principles.get(k) is not True)
    if missing_principles:
        errors.append("required authority principles missing/false: " + ", ".join(missing_principles))

    under = pol.get("unregistered_or_underdecomposed_concepts") or []
    names = {x.get("concept") for x in under}
    for required in ("optionality", "asset_concentration_and_best_asset_premium"):
        if required not in names:
            errors.append(f"missing under-decomposed concept review: {required}")

    risk = [x for x in findings if x["zero_may_be_unjustified_point_estimate"]]
    split = [x for x in findings if x["family_decomposition_required"]]
    dup = [x for x in findings if x["authority_mode"] == "DIAGNOSTIC_DUPLICATIVE"]
    semantic = [x for x in findings if x["authority_mode"] == "DIAGNOSTIC_WRONG_SEMANTIC_ROLE"]
    active_prior = [x for x in findings if x["authority_mode"] == "ACTIVE_BOUNDED_PROVISIONAL_PRIOR"]

    report = {
        "schema_version": "1.0",
        "audit_family": "parameter authority and missing value",
        "model_version": pol.get("model_version"),
        "production_behavior_changed": False,
        "policy": principles,
        "summary": {
            "passed": not errors,
            "reviewed_families": len(findings),
            "credible_effects_at_zero_authority_needing_review": len(risk),
            "mixed_families_needing_decomposition": len(split),
            "genuine_duplicate_zero_authority_families": len(dup),
            "wrong_semantic_role_zero_authority_families": len(semantic),
            "bounded_provisional_prior_examples_already_active": len(active_prior),
            "underdecomposed_concepts": len(under),
            "errors": errors,
        },
        "central_finding": (
            "The audit standard must distinguish an unsupported coefficient from an unsupported concept. "
            "For credible, non-duplicative economic effects, zero is itself a parameter choice and should "
            "not be the automatic fallback. Use bounded, uncertainty-exposed provisional priors and refine "
            "them as historical/simulation evidence accumulates."
        ),
        "findings": findings,
        "unregistered_or_underdecomposed_concepts": under,
        "recommended_sequence": [
            "Decompose mixed umbrella families before changing production authority.",
            "Map each credible effect to existing utility channels to prevent double counting.",
            "Define bounded prior ranges from available transaction, market, simulation, and roster evidence.",
            "Run sensitivity/regression suites so recommendations expose dependence on uncertain priors.",
            "Promote or narrow priors through time-ordered residual validation as evidence accumulates.",
        ],
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], indent=2))
    if errors:
        raise SystemExit("parameter authority/missing-value audit failed")

if __name__ == "__main__":
    main()
