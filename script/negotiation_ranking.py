#!/usr/bin/env python3
"""Canonical Trade Decision negotiation ranking.

Trade quality and focal utility are separated from counterparty acceptance.
The bilateral buyer-utility gate determines whether a trade is economically
viable for the other side; Behavioral Intelligence then reports descriptive
acceptance fit. Neither is converted into an arbitrary exchange rate against
focal franchise utility.

Among retained viable candidates, ranking is therefore by the canonical shared
focal decision utility itself. Acceptance and owner behavior remain visible
diagnostics/tie-break context for the human negotiator, not weighted value.
"""
from __future__ import annotations

MODEL_VERSION = "FSFFL-Negotiation-Ranking-3.0"
STRATEGIC_WEIGHT = 1.0
ACCEPTANCE_WEIGHT = 0.0
OWNER_BEHAVIOR_WEIGHT = 0.0


def clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, float(x)))


def _sf(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def compose(strategic, acceptance, behavior):
    """Compatibility composer for normalized callers.

    No acceptance/behavior exchange coefficient is authorized. The supplied
    strategic component alone determines score.
    """
    strategic = float(strategic)
    acceptance = clamp(acceptance)
    behavior = clamp(behavior)
    return {
        "score": round(strategic, 4),
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
        "acceptance_component_is_diagnostic_only": True,
        "arbitrary_strategic_acceptance_exchange_rate_authorized": False,
        "ranking_basis": "canonical_focal_decision_utility_after_bilateral_viability",
    }


def recompute_from_row(row):
    br = row.get("buyer_rationality") or {}
    post = _sf(row.get("post_sim_score"))
    acceptance = clamp(_sf(br.get("heuristic_acceptance_fit_score"), .5))
    behavior = clamp(.50 + _sf((br.get("owner_behavior") or {}).get("adjustment")), 0, 1)
    out = {
        "score": round(post, 2),
        "focal_strategic_gain_component": round(post, 2),
        "acceptance_fit_component": round(acceptance, 4),
        "owner_behavior_match_component": round(behavior, 4),
        "weights": {
            "focal_strategic_gain": 1.0,
            "acceptance_fit": 0.0,
            "owner_behavior_match": 0.0,
        },
        "ranking_model_version": MODEL_VERSION,
        "behavior_already_in_acceptance_fit": True,
        "owner_behavior_component_is_diagnostic_only": True,
        "acceptance_component_is_diagnostic_only": True,
        "arbitrary_strategic_acceptance_exchange_rate_authorized": False,
        "ranking_basis": "canonical_focal_decision_utility_after_bilateral_viability",
        "focal_strategic_gain_source": "state_aware_post_sim_score",
        "state_aware_post_sim_score": round(post, 2),
    }
    return out
