#!/usr/bin/env python3
"""Validate commercial-use data provenance governance.

This is a governance/commercial-readiness gate, not legal advice. It prevents
rights-unclear or research-only sources from silently becoming material
production-parameter dependencies.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "data/model_governance/commercial_data_provenance_policy.json"
REGISTRY = ROOT / "data/model_governance/source_rights_registry.json"

ALLOWED_CLASSES = {
    "FIRST_PARTY_OWNED",
    "OPEN_OR_COMMERCIALLY_PERMITTED",
    "COMMERCIAL_LICENSED",
    "COMMERCIAL_LICENSE_REQUIRED",
    "RESEARCH_REFERENCE_ONLY",
    "RIGHTS_UNCLEAR_OR_UNREVIEWED",
}


def fail(msg: str):
    raise SystemExit(f"commercial data provenance validation failed: {msg}")


def main():
    policy = json.loads(POLICY.read_text(encoding="utf-8"))
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))

    principles = policy.get("principles") or {}
    required = {
        "research_only_sources_may_inform_hypotheses_and_sensitivity_but_not_materially_determine_commercial_production_parameters": True,
        "rights_unclear_defaults_to_research_only": True,
        "deleting_raw_external_data_does_not_by_itself_cure_a_restricted_training_or_calibration_dependency": True,
        "production_parameter_provenance_must_be_versioned": True,
    }
    for key, expected in required.items():
        if principles.get(key) is not expected:
            fail(f"policy principle {key!r} must be {expected!r}")

    sources = registry.get("sources")
    if not isinstance(sources, list) or not sources:
        fail("source registry must be non-empty")

    ids = set()
    for src in sources:
        sid = src.get("source_id")
        if not sid or sid in ids:
            fail(f"invalid or duplicate source_id: {sid!r}")
        ids.add(sid)

        cls = src.get("source_class")
        if cls not in ALLOWED_CLASSES:
            fail(f"{sid}: invalid source_class {cls!r}")

        material = bool(src.get("material_production_dependency"))
        readiness = str(src.get("commercial_readiness") or "")
        legal = src.get("legal_determination_made")
        if legal not in {True, False}:
            fail(f"{sid}: legal_determination_made must be boolean")

        if cls == "COMMERCIAL_LICENSE_REQUIRED":
            if "REQUIRED" not in readiness:
                fail(f"{sid}: commercial-license-required source must remain explicitly gated")
        if cls in {"RESEARCH_REFERENCE_ONLY", "RIGHTS_UNCLEAR_OR_UNREVIEWED"}:
            if material and "REVIEW_REQUIRED" not in readiness:
                fail(f"{sid}: rights-unclear material dependency must require commercial review")
            if not material and not (
                "RESEARCH_REFERENCE_ONLY" in readiness
                or "REVIEW_REQUIRED" in readiness
            ):
                fail(f"{sid}: non-material rights-unclear source must remain research/review gated")

    # Explicitly guard the two historical reference providers currently present
    # in the reconstruction corpus from becoming material production dependencies.
    by_id = {x["source_id"]: x for x in sources}
    for sid in ("PFF-HISTORICAL-VALUES", "FANTASYPROS-HISTORICAL-VALUES"):
        src = by_id.get(sid)
        if not src:
            fail(f"missing required historical source governance entry {sid}")
        if src["material_production_dependency"] is not False:
            fail(f"{sid}: historical research reference cannot be material production dependency")
        if src["commercial_readiness"] != "RESEARCH_REFERENCE_ONLY_PENDING_REVIEW":
            fail(f"{sid}: must remain research-only pending rights review")

    print(json.dumps({
        "source_count": len(sources),
        "material_dependencies": sum(bool(x["material_production_dependency"]) for x in sources),
        "research_or_review_gated": sum(
            x["source_class"] in {"RESEARCH_REFERENCE_ONLY", "RIGHTS_UNCLEAR_OR_UNREVIEWED"}
            for x in sources
        ),
        "legal_determinations_made": sum(bool(x["legal_determination_made"]) for x in sources),
    }, indent=2))


if __name__ == "__main__":
    main()
