#!/usr/bin/env python3
"""Static governance checks for the FSFFL projection ensemble architecture."""

from __future__ import annotations

import json
from pathlib import Path

REGISTRY = Path("data/projection_source_registry.json")
BUILDER = Path("script/build_fsffl_projection_ensemble.py")


def require(condition: bool, message: str):
    if not condition:
        raise AssertionError(message)


def main():
    require(REGISTRY.exists(), "Missing projection source registry")
    require(BUILDER.exists(), "Missing projection ensemble builder")

    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    policy = registry.get("policy") or {}
    sources = registry.get("sources") or {}

    require(int(policy.get("minimum_independent_sources_for_authoritative_ensemble", 0)) >= 2,
            "Authoritative projection ensemble must require at least two independent sources")
    require(policy.get("single_source_may_be_authoritative") is False,
            "Single-source projection authority must be explicitly prohibited")
    require(policy.get("duplicate_information_families_must_not_receive_independent_votes") is True,
            "Registry must prohibit duplicate information families from receiving independent votes")
    require(policy.get("default_aggregation") == "equal_weight_mean",
            "Initial governed aggregation must remain the simple equal-weight baseline")
    require(policy.get("allow_complex_weighting_without_validation") is False,
            "Complex weighting must require empirical validation")

    eligible = [x for x in sources.values() if x.get("eligible_for_ensemble")]
    require(len(eligible) >= 2, "Registry must define at least two eligible projection sources")
    for cfg in eligible:
        require(bool(cfg.get("independence_family")), "Every eligible source needs an independence_family")
        require(bool(cfg.get("normalized_file_pattern")), "Every eligible source needs a normalized file pattern")

    text = BUILDER.read_text(encoding="utf-8")
    require("authoritative_projection_allowed" in text, "Builder must emit projection authority state")
    require("source_disagreement" in text, "Builder must preserve source disagreement")
    require("duplicate_independence_family" in text, "Builder must de-duplicate source families")
    require("preseason_fsffl_points_candidate_ensemble.json" in text,
            "Builder must preserve a candidate artifact when authority gate fails")

    print("Projection ensemble governance audit passed.")


if __name__ == "__main__":
    main()
