#!/usr/bin/env python3
"""Canonical continuous trade-state policy primitives.

Mechanical extraction of the current v1.17 state-policy semantics. This module
contains no market-sweep orchestration and does not mutate historical wrappers.
It provides Trade Decision-internal policy primitives for the current application:

- continuous focal-state eligibility;
- current-state conditioning of historical owner behavior;
- canonical negotiation-ranking recomputation after state conditioning;
- provisional upstream action without acceptance-band or score-distance cliffs.

The production trade path is not switched to this module until equivalence
against the existing v23 behavior is proven.
"""
from __future__ import annotations

import math

MODEL_VERSION = "FSFFL-Trade-State-Policy-1.0"


def sf(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def clamp(x, lo, hi):
    return max(lo, min(hi, x))


def band(score):
    return "HIGH" if score >= .68 else "MEDIUM" if score >= .48 else "LOW" if score >= .28 else "VERY_LOW"


def focal_current_state(row):
    strategic = ((row.get("simulation") or {}).get("strategic") or {})
    return str(strategic.get("objective_state") or row.get("focus_state") or "unknown")


def focal_state_beneficial(row):
    """Use continuous focal objective; descriptive state labels do not gate."""
    state = focal_current_state(row)
    post = sf(row.get("post_sim_score"))
    if post <= 0:
        return False
    return True


def state_condition_behavior(row, br):
    """Preserve historical behavior without categorical competitive-state cliffs.

    The buyer's current-state utility is already represented in the underlying
    acceptance model. Historical behavior remains a bounded secondary signal,
    but provisional team-state labels do not scale or suppress it.
    """
    sig = dict(br.get("owner_behavior") or {})
    static_adj = sf(sig.get("adjustment"))
    base = sf(
        br.get("state_utility_acceptance_fit_score"),
        sf(br.get("heuristic_acceptance_fit_score"), .5) - static_adj,
    )
    score = round(clamp(base + static_adj, 0.0, 1.0), 4)
    sig.update({
        "static_historical_adjustment": round(static_adj, 4),
        "state_conditioned_adjustment": round(static_adj, 4),
        "adjustment": round(static_adj, 4),
        "current_state": focal_current_state(row),
        "state_compatibility_weight": None,
        "categorical_state_conditioning_authorized": False,
        "state_conditioning_note": (
            "Historical behavior is retained as a bounded secondary feasibility signal; "
            "provisional competitive-state labels do not rescale it."
        ),
    })
    br["owner_behavior"] = sig
    br["heuristic_acceptance_fit_score"] = score
    br["heuristic_acceptance_fit"] = band(score)
    br["acceptance_fit_basis"] = "current_state_utility_plus_unconditioned_historical_behavior"
    br["acceptance_band_is_descriptive_not_probability"] = True
    return br


def recompute_negotiation_ranking(row, ranker):
    """Rank retained negotiations by canonical focal utility.

    Acceptance/behavior remain separate descriptive feasibility context and do
    not receive an arbitrary exchange weight against franchise utility.
    """
    return ranker.recompute_from_row(row)


def recompute_action_without_acceptance_band_gate(report):
    """Provisional upstream action with no acceptance/score-distance cliff."""
    top = list(report.get("top_5_alternatives") or report.get("ranked_finalists") or [])
    if not top:
        return "DECLINE"

    current = report.get("current_offer_evaluation") or {}
    current_buyer_ok = bool(
        (current.get("buyer_rationality") or {}).get("current_state_viable")
    )
    current_focal_ok = focal_state_beneficial(current)
    best = top[0]

    if current_focal_ok and current_buyer_ok:
        return (
            "SHOP_BEFORE_ACCEPTING"
            if sf(best.get("post_sim_score")) > sf(current.get("post_sim_score"))
            else "ACCEPT_NOW"
        )
    if any(r.get("candidate_type") == "SAME_PARTNER_COUNTER" for r in top[:5]):
        return "COUNTER_CURRENT_OFFEROR"
    return "SHOP_BEFORE_ACCEPTING"


def prepare_rows(rows, ranker):
    """Apply current-state conditioning and ranking to retained rows."""
    prepared = []
    for row in list(rows or []):
        br = row.get("buyer_rationality") or {}
        if br:
            state_condition_behavior(row, br)
            row["acceptance_likelihood"] = br.get("heuristic_acceptance_fit")
            row["negotiation_ranking"] = recompute_negotiation_ranking(row, ranker)
        prepared.append(row)
    return sorted(
        prepared,
        key=lambda r: (
            sf((r.get("negotiation_ranking") or {}).get("score")),
            sf(r.get("post_sim_score")),
        ),
        reverse=True,
    )
