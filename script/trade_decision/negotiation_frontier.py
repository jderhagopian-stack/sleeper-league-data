#!/usr/bin/env python3
"""Canonical Trade Decision negotiation and price-frontier interpretation.

Trade Decision owns interpretation of generated-trade counterparty feasibility.
The price frontier is discrete: it compares packages already evaluated by GM3
and the governed counterparty utility path. It does not create trade value,
acceptance probability, or an exchange rate between feasibility and focal value.
"""
from __future__ import annotations
import copy

MODEL_VERSION = "FSFFL-Trade-Decision-Negotiation-Frontier-1.2"
AUTHORITY = "Trade Decision"


def sf(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _counterparty_utility(row):
    # Production trade feasibility must use the same governed Shared Decision
    # Utility used for the focal franchise. Legacy/pre-screen seller strategic
    # utility remains available only as discovery context/fallback for old rows.
    for key in ("counterparty_shared_decision_utility_score", "buyer_decision_utility_score", "seller_strategic_utility_precomputed"):
        if row.get(key) is not None:
            return sf(row.get(key))
    return None


def _focal_utility(row):
    if row.get("team_improvement_score") is None:
        return None
    return sf(row.get("team_improvement_score"))


def _asset_price(asset):
    for key in ("market_dynasty", "fsffl_value"):
        if asset.get(key) is not None:
            return max(0.0, sf(asset.get(key)))
    return 0.0


def _package_price(row):
    """Market-value coordinate used only to order evaluated packages by cost."""
    return round(sum(_asset_price(x) for x in (row.get("outgoing") or [])), 4)


def _target_key(row):
    target = row.get("target") or {}
    return (str(row.get("seller_user_id") or ""), str(target.get("asset_id") or target.get("player_id") or ""))


def _package_view(row):
    return {
        "description": row.get("description"),
        "seller_user_id": row.get("seller_user_id"),
        "seller_team": row.get("seller_team"),
        "target": copy.deepcopy(row.get("target")),
        "outgoing": copy.deepcopy(row.get("outgoing") or []),
        "package_market_value_coordinate": _package_price(row),
        "focal_team_improvement_utility": _focal_utility(row),
        "counterparty_shared_utility": _counterparty_utility(row),
        "counterparty_utility_source": (
            "shared_decision_utility"
            if row.get("counterparty_shared_decision_utility_score") is not None
            else "legacy_or_external_fallback"
        ),
        "descriptive_acceptance_fit": str(row.get("acceptance_fit") or row.get("source_recommendation_band") or "UNKNOWN").upper(),
    }


def classify_trade(row):
    """Interpret a governed generated package for negotiation use."""
    out = copy.deepcopy(row)
    utility = _counterparty_utility(out)
    focal = _focal_utility(out)
    fit = str(out.get("acceptance_fit") or out.get("source_recommendation_band") or "UNKNOWN").upper()
    bilateral = utility is not None and utility >= 0.0
    focal_positive = focal is not None and focal > 0.0
    if not focal_positive:
        bucket = "FOCAL_OVERPAY"
        posture = "DO_NOT_PURSUE_AT_EXPECTED_COST"
        reason = "The governed GM3 franchise-improvement utility is non-positive at this package price."
    elif not bilateral:
        bucket = "THEORETICAL_UPGRADE"
        posture = "DO_NOT_TREAT_AS_ACTIONABLE"
        reason = "The current generated package does not clear governed counterparty bilateral utility."
    elif fit in {"HIGH", "MEDIUM"}:
        bucket = "ACTIONABLE_NEGOTIATION"
        posture = "PURSUE"
        reason = "The package clears governed bilateral utility and remains positive for the focal franchise."
    else:
        bucket = "NEGOTIATION_TARGET"
        posture = "OPEN_NEGOTIATION"
        reason = "The package clears governed bilateral utility and remains positive for the focal franchise, but descriptive negotiation fit is weak."
    out["negotiation_frontier"] = {
        "model_version": MODEL_VERSION,
        "authority": AUTHORITY,
        "bucket": bucket,
        "negotiation_posture": posture,
        "focal_team_improvement_utility": focal,
        "focal_utility_positive": focal_positive,
        "counterparty_shared_utility": utility,
        "counterparty_bilateral_viable": bilateral,
        "descriptive_acceptance_fit": fit,
        "acceptance_fit_is_probability": False,
        "package_market_value_coordinate": _package_price(out),
        "reason": reason,
        "creates_new_trade_value": False,
        "creates_new_acceptance_probability": False,
    }
    return out

def build_target_price_frontier(rows):
    """Build a discrete negotiation frontier from already-evaluated packages.

    Seller clearing floor = cheapest evaluated package with non-negative
    counterparty shared utility.

    Rational focal ceiling = most expensive evaluated package whose GM3
    team-improvement utility remains positive.

    Deal zone = evaluated packages satisfying both conditions. The opening
    package is the cheapest package in that mutually beneficial zone.

    These are package-frontier observations, not a continuous reservation-price
    estimate and not an acceptance probability.
    """
    packages = [classify_trade(x) for x in rows if str(x.get("channel") or "") == "TRADE"]
    packages.sort(key=lambda x: (_package_price(x), -(sf(_focal_utility(x), -1e18))))
    seller_viable = [x for x in packages if _counterparty_utility(x) is not None and _counterparty_utility(x) >= 0.0]
    focal_viable = [x for x in packages if _focal_utility(x) is not None and _focal_utility(x) > 0.0]
    deal_zone = [x for x in packages if x in seller_viable and x in focal_viable]
    seller_floor = min(seller_viable, key=_package_price) if seller_viable else None
    focal_ceiling = max(focal_viable, key=_package_price) if focal_viable else None
    opener = min(deal_zone, key=_package_price) if deal_zone else None
    overlap = bool(deal_zone)
    if overlap:
        status = "ACTIONABLE_PRICE_OVERLAP"
        reason = "At least one evaluated package is non-negative for the seller and still positive for the focal franchise."
    elif seller_floor is None:
        status = "SELLER_CLEARING_NOT_FOUND_IN_EVALUATED_PACKAGES"
        reason = "No evaluated package reaches non-negative governed counterparty utility."
    elif focal_ceiling is None:
        status = "NO_RATIONAL_FOCAL_PRICE"
        reason = "No evaluated package remains positive for the focal franchise."
    else:
        status = "NO_PRICE_OVERLAP"
        reason = "The evaluated seller clearing floor lies beyond the focal team's positive-utility package set."
    return {
        "model_version": MODEL_VERSION,
        "authority": AUTHORITY,
        "target": copy.deepcopy((packages[0].get("target") if packages else None)),
        "seller_user_id": packages[0].get("seller_user_id") if packages else None,
        "seller_team": packages[0].get("seller_team") if packages else None,
        "evaluated_package_count": len(packages),
        "status": status,
        "price_overlap_exists": overlap,
        "opening_package": _package_view(opener) if opener else None,
        "seller_clearing_floor": _package_view(seller_floor) if seller_floor else None,
        "rational_focal_ceiling": _package_view(focal_ceiling) if focal_ceiling else None,
        "mutually_beneficial_deal_zone": [_package_view(x) for x in deal_zone],
        "reason": reason,
        "coverage_note": "Discrete frontier over evaluated packages only; absence of overlap may reflect search coverage and should trigger broader package generation before a target is abandoned.",
        "policy": {
            "price_frontier_uses_only_evaluated_packages": True,
            "seller_floor_uses_counterparty_shared_utility_sign": True,
            "production_counterparty_utility_uses_same_shared_decision_utility_as_focal": True,
            "seller_strategic_utility_precomputed_is_search_only_when_shared_utility_available": True,
            "focal_ceiling_uses_gm3_team_improvement_utility_sign": True,
            "market_value_is_ordering_coordinate_not_incremental_utility": True,
            "behavioral_fit_does_not_change_price_or_utility": True,
            "creates_new_trade_value": False,
            "creates_new_acceptance_probability": False,
            "no_arbitrary_elite_player_premium": True,
        },
    }


def build(rows):
    classified = [classify_trade(x) for x in rows if str(x.get("channel") or "") == "TRADE"]
    actionable = [x for x in classified if x["negotiation_frontier"]["bucket"] == "ACTIONABLE_NEGOTIATION"]
    explore = [x for x in classified if x["negotiation_frontier"]["bucket"] == "NEGOTIATION_TARGET"]
    theoretical = [x for x in classified if x["negotiation_frontier"]["bucket"] == "THEORETICAL_UPGRADE"]
    focal_overpay = [x for x in classified if x["negotiation_frontier"]["bucket"] == "FOCAL_OVERPAY"]
    grouped = {}
    for row in rows:
        if str(row.get("channel") or "") != "TRADE":
            continue
        grouped.setdefault(_target_key(row), []).append(row)
    price_frontiers = [build_target_price_frontier(group) for group in grouped.values()]
    price_frontiers.sort(key=lambda x: (
        x.get("status") == "ACTIONABLE_PRICE_OVERLAP",
        len(x.get("mutually_beneficial_deal_zone") or []),
    ), reverse=True)
    return {
        "model_version": MODEL_VERSION,
        "authority": AUTHORITY,
        "actionable_negotiations": actionable,
        "negotiation_targets": explore,
        "theoretical_upgrades": theoretical,
        "best_actionable_trade": actionable[0] if actionable else None,
        "best_negotiation_target": explore[0] if explore else None,
        "best_theoretical_upgrade": theoretical[0] if theoretical else None,
        "focal_overpay_packages": focal_overpay,
        "best_focal_overpay_package": focal_overpay[0] if focal_overpay else None,
        "high_impact_price_gap_targets": [x for x in price_frontiers if not x.get("price_overlap_exists")],
        "target_price_frontiers": price_frontiers,
        "best_price_overlap": next((x for x in price_frontiers if x.get("price_overlap_exists")), None),
        "policy": {
            "interpretation_owned_by_trade_decision": True,
            "preserves_upstream_gm3_order_within_each_bucket": True,
            "acceptance_fit_is_diagnostic_not_probability": True,
            "bilateral_viability_uses_governed_counterparty_utility": True,
            "price_frontier_uses_discrete_evaluated_packages": True,
            "no_arbitrary_utility_acceptance_exchange_rate": True,
            "behavioral_intelligence_supplies_evidence_not_decision_authority": True,
            "opportunity_engine_may_route_and_present_but_not_reclassify": True,
        },
    }
