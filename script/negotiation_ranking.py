#!/usr/bin/env python3
"""Canonical negotiation-ranking composition.

Owner behavior is already incorporated upstream into heuristic acceptance fit.
This composer therefore gives the separate owner-behavior diagnostic zero
additional ranking weight to avoid counting the same evidence twice.

The remaining strategic/acceptance weights preserve the prior 0.50:0.30 ratio:
0.50/(0.50+0.30)=0.625 and 0.30/(0.50+0.30)=0.375.
This is a structural de-duplication, not an output-tuned reweighting.

The row-level helper preserves the exact historical v1.17 transform so callers
can use the canonical component directly instead of importing a superseded
trade-sweep wrapper solely to reach that mechanic.
"""
from __future__ import annotations

import math

MODEL_VERSION = "FSFFL-Negotiation-Ranking-2.0"
STRATEGIC_WEIGHT = 0.625
ACCEPTANCE_WEIGHT = 0.375
OWNER_BEHAVIOR_WEIGHT = 0.0


def clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, float(x)))


def _sf(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


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


def recompute_from_row(row):
    """Recompute negotiation ranking from a trade-evaluation row.

    This is behavior-equivalent to the historical
    run_trade_market_sweep_v23.recompute_negotiation_ranking helper. Keeping the
    transform here lets current callers depend on a stable Trade Decision-internal capability
    rather than a superseded model wrapper.
    """
    br = row.get("buyer_rationality") or {}
    post = _sf(row.get("post_sim_score"))
    strategic = clamp(.50 + .50 * math.tanh(post / 5000.0), 0, 1)
    acceptance = clamp(_sf(br.get("heuristic_acceptance_fit_score"), .5), 0, 1)
    behavior = clamp(.50 + _sf((br.get("owner_behavior") or {}).get("adjustment")) / .32, 0, 1)
    out = compose(strategic, acceptance, behavior)
    out["focal_strategic_gain_source"] = "state_aware_post_sim_score"
    out["state_aware_post_sim_score"] = round(post, 2)
    return out
