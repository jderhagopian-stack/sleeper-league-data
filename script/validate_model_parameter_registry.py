#!/usr/bin/env python3
"""Validate the FSFFL governed parameter registry.

This is a governance gate, not an empirical-validation claim. It ensures that
material parameter families have explicit provenance, uncertainty, downstream
impact and update policy, and that provisional parameters cannot silently be
presented as authoritative.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "data" / "model_parameter_registry.json"

EVIDENCE_TIERS = {
    "RULE_DEFINED",
    "HISTORICALLY_STATISTICALLY_ESTIMATED",
    "EVIDENCE_BASED_EXTERNAL_ANCHOR",
    "RESEARCH_SUPPORTED_PROXY",
    "ASSUMPTION_SENSITIVE_PROVISIONAL",
}

REQUIRED_FIELDS = {
    "id",
    "component",
    "paths",
    "parameter_family",
    "evidence_tier",
    "status",
    "source",
    "validation",
    "uncertainty",
    "update_policy",
    "downstream",
    "authoritative_use",
}

# These are the high-leverage families identified by the 1.0 governance audit.
# The registry must not regress by silently dropping one of them.
REQUIRED_MATERIAL_IDS = {
    "PROJECTION-MEAN-001",
    "PROJECTION-UNCERTAINTY-001",
    "STATE-WEIGHTS-001",
    "CONSOLIDATION-PREMIUM-001",
    "GM22-CONFIG-001",
    "PICK-MODEL-001",
    "PACKAGE-ECON-001",
    "ROSTER-CUT-001",
    "ROSTER-INTERACTION-001",
    "BEHAVIOR-001",
    "TRADE-PRESCREEN-001",
    "DECISION-GATES-001",
    "TEAM-IMPROVEMENT-001",
    "TRADE-SCORE-001",
    "WHAT-IF-001",
}


def fail(message: str) -> None:
    raise SystemExit(f"parameter registry validation failed: {message}")


def main() -> None:
    if not REGISTRY.exists():
        fail(f"missing {REGISTRY.relative_to(ROOT)}")

    data = json.loads(REGISTRY.read_text(encoding="utf-8"))
    hierarchy = data.get("evidence_hierarchy")
    if hierarchy != [
        "RULE_DEFINED",
        "HISTORICALLY_STATISTICALLY_ESTIMATED",
        "EVIDENCE_BASED_EXTERNAL_ANCHOR",
        "RESEARCH_SUPPORTED_PROXY",
        "ASSUMPTION_SENSITIVE_PROVISIONAL",
    ]:
        fail("evidence hierarchy changed or is incomplete")

    policy = data.get("policy") or {}
    required_policy = {
        "preserve_functionality": True,
        "removal_is_last_resort": True,
        "provisional_parameters_must_be_bounded": True,
        "provisional_parameters_must_not_silently_drive_authoritative_recommendations": True,
        "learned_parameters_must_be_versioned": True,
        "learned_parameter_promotion_requires_validation_improvement": True,
        "software_validation_is_not_empirical_validation": True,
    }
    for key, expected in required_policy.items():
        if policy.get(key) is not expected:
            fail(f"policy {key!r} must be {expected!r}")

    params = data.get("parameters")
    if not isinstance(params, list) or not params:
        fail("parameters must be a non-empty list")

    ids = []
    errors = []
    for i, p in enumerate(params):
        if not isinstance(p, dict):
            errors.append(f"entry {i} is not an object")
            continue
        missing = sorted(REQUIRED_FIELDS - set(p))
        if missing:
            errors.append(f"{p.get('id', i)} missing fields: {', '.join(missing)}")
            continue
        pid = p["id"]
        ids.append(pid)
        if p["evidence_tier"] not in EVIDENCE_TIERS:
            errors.append(f"{pid}: invalid evidence_tier {p['evidence_tier']!r}")
        if not p["paths"] or not all(isinstance(x, str) and x for x in p["paths"]):
            errors.append(f"{pid}: paths must be non-empty strings")
        if not p["downstream"] or not all(isinstance(x, str) and x for x in p["downstream"]):
            errors.append(f"{pid}: downstream must identify at least one consumer")
        for field in ("source", "validation", "uncertainty", "update_policy"):
            if not isinstance(p[field], str) or not p[field].strip():
                errors.append(f"{pid}: {field} must be documented")

        if p["evidence_tier"] == "ASSUMPTION_SENSITIVE_PROVISIONAL":
            if p.get("bounds_required") is not True:
                errors.append(f"{pid}: provisional parameters must set bounds_required=true")
            if p["authoritative_use"] is not False:
                errors.append(f"{pid}: provisional parameter family cannot be marked authoritative")

        if p["evidence_tier"] in {
            "HISTORICALLY_STATISTICALLY_ESTIMATED",
            "EVIDENCE_BASED_EXTERNAL_ANCHOR",
            "RESEARCH_SUPPORTED_PROXY",
        }:
            update = p["update_policy"].lower()
            if not any(word in update for word in ("recalibr", "re-estimate", "refit", "update", "rolling")):
                errors.append(f"{pid}: learned/evidence-derived parameter lacks recalibration policy")

    duplicates = sorted({x for x in ids if ids.count(x) > 1})
    if duplicates:
        errors.append(f"duplicate ids: {duplicates}")

    missing_material = sorted(REQUIRED_MATERIAL_IDS - set(ids))
    if missing_material:
        errors.append(f"material parameter families missing from registry: {missing_material}")

    # Source paths are allowed to be future-facing only for explicitly named
    # module paths already present in the architecture. Catch ordinary typos.
    for p in params:
        for rel in p.get("paths", []):
            if not (ROOT / rel).exists():
                errors.append(f"{p.get('id')}: registered path does not exist: {rel}")

    if errors:
        fail("\n - " + "\n - ".join(errors))

    summary = {
        "schema_version": data["schema_version"],
        "parameter_families": len(params),
        "provisional_families": sum(
            p["evidence_tier"] == "ASSUMPTION_SENSITIVE_PROVISIONAL" for p in params
        ),
        "authoritative_families": sum(bool(p["authoritative_use"]) for p in params),
        "material_coverage": len(REQUIRED_MATERIAL_IDS),
    }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
