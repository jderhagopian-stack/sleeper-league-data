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
    if state in {"contender", "elite_contender"} and row.get("championship_equity_constraint") == "FAIL":
        return False
    return True


def state_condition_behavior(row, br):
    """Condition aggregate behavior on the buyer's current competitive state."""
    sig = dict(br.get("owner_behavior") or {})
    static_adj = sf(sig.get("adjustment"))
    base = sf(
        br.get("state_utility_acceptance_fit_score"),
        sf(br.get("heuristic_acceptance_fit_score"), .5) - static_adj,
    )
    state = str(br.get("buyer_state") or "unknown")

    buyer_receives = [str(x) for x in (row.get("outgoing_assets") or [])]
    buyer_sends = [str(x) for x in (row.get("return_assets") or [])]
    recv_picks = sum(x.startswith("pick:") for x in buyer_receives)
    send_picks = sum(x.startswith("pick:") for x in buyer_sends)
    net_pick_in = recv_picks - send_picks

    if state == "elite_contender":
        compat = 1.0 if net_pick_in < 0 else .55 if net_pick_in > 0 else .75
    elif state == "contender":
        compat = .90 if net_pick_in < 0 else .60 if net_pick_in > 0 else .75
    elif state == "retool":
        compat = 1.0 if net_pick_in > 0 else .40 if net_pick_in < 0 else .70
    elif state == "rebuild":
        compat = 1.0 if net_pick_in > 0 else .15 if net_pick_in < 0 else .60
    else:
        compat = .50

    conditioned = static_adj * compat
    if state in {"rebuild", "retool"} and net_pick_in < 0 and conditioned > 0:
        conditioned = min(conditioned, .01)
    if state in {"contender", "elite_contender"} and net_pick_in > 0 and conditioned > 0:
        conditioned = min(conditioned, .02)

    score = round(clamp(base + conditioned, 0.0, 1.0), 4)
    sig.update({
        "static_historical_adjustment": round(static_adj, 4),
        "state_conditioned_adjustment": round(conditioned, 4),
        "adjustment": round(conditioned, 4),
        "current_state": state,
        "state_compatibility_weight": round(compat, 3),
        "buyer_net_pick_in": int(net_pick_in),
        "state_conditioning_note": (
            "Aggregate historical behavior is attenuated when it conflicts "
            "with the manager's current competitive state."
        ),
    })
    br["owner_behavior"] = sig
    br["heuristic_acceptance_fit_score"] = score
    br["heuristic_acceptance_fit"] = band(score)
    br["acceptance_fit_basis"] = "current_state_utility_plus_state_conditioned_historical_behavior"
    br["acceptance_band_is_descriptive_not_probability"] = True
    return br


def recompute_negotiation_ranking(row, ranker):
    """Recompute ranking using the canonical Trade Decision negotiation-ranker."""
    br = row.get("buyer_rationality") or {}
    post = sf(row.get("post_sim_score"))
    strategic = clamp(.50 + .50 * math.tanh(post / 5000.0), 0, 1)
    acceptance = clamp(sf(br.get("heuristic_acceptance_fit_score"), .5), 0, 1)
    behavior = clamp(
        .50 + sf((br.get("owner_behavior") or {}).get("adjustment")) / .32,
        0,
        1,
    )
    out = ranker.compose(strategic, acceptance, behavior)
    out["focal_strategic_gain_source"] = "state_aware_post_sim_score"
    out["state_aware_post_sim_score"] = round(post, 2)
    return out


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
