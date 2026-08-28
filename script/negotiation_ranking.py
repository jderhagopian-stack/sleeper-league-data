#!/usr/bin/env python3
"""Canonical negotiation-ranking composition.

Owner behavior is already incorporated upstream into heuristic acceptance fit.
This composer therefore gives the separate owner-behavior diagnostic zero
additional ranking weight to avoid counting the same evidence twice.

The remaining strategic/acceptance weights preserve the prior 0.50:0.30 ratio:
0.50/(0.50+0.30)=0.625 and 0.30/(0.50+0.30)=0.375.
This is a structural de-duplication, not an output-tuned reweighting.
"""
from __future__ import annotations

MODEL_VERSION = "FSFFL-Negotiation-Ranking-2.0"
STRATEGIC_WEIGHT = 0.625
ACCEPTANCE_WEIGHT = 0.375
OWNER_BEHAVIOR_WEIGHT = 0.0


def clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, float(x)))


def compose(strategic, acceptance, behavior):
    strategic = clamp(strategic)
    acceptance = clamp(acceptance)
    behavior = clamp(behavior)
    score = (
        STRATEGIC_WEIGHT * strategic
        + ACCEPTANCE_WEIGHT * acceptance
        + OWNER_BEHAVIOR_WEIGHT * behavior
    )
    return {
        "score": round(score, 4),
        "focal_strategic_gain_component": round(strategic, 4),
        "acceptance_fit_component": round(acceptance, 4),
        "owner_behavior_match_component": round(behavior, 4),
        "weights": {
            "focal_strategic_gain": STRATEGIC_WEIGHT,
            "acceptance_fit": ACCEPTANCE_WEIGHT,
            "owner_behavior_match": OWNER_BEHAVIOR_WEIGHT,
        },
        "ranking_model_version": MODEL_VERSION,
        "behavior_already_in_acceptance_fit": True,
        "owner_behavior_component_is_diagnostic_only": True,
        "deduplication_basis": "renormalized_prior_distinct_component_ratio_0.50_to_0.30",
    }
