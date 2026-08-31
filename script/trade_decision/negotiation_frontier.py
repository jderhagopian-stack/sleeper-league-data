#!/usr/bin/env python3
"""Canonical Trade Decision negotiation-frontier interpretation.

Trade Decision owns interpretation of generated-trade counterparty feasibility.
This module consumes governed GM3 focal/counterparty utility and descriptive
Behavioral Intelligence acceptance fit. It does not create trade value, an
acceptance probability, or an exchange rate between feasibility and focal value.
"""
from __future__ import annotations
import copy

MODEL_VERSION = "FSFFL-Trade-Decision-Negotiation-Frontier-1.0"
AUTHORITY = "Trade Decision"


def sf(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _counterparty_utility(row):
    for key in ("seller_strategic_utility_precomputed", "buyer_decision_utility_score"):
        if row.get(key) is not None:
            return sf(row.get(key))
    return None


def classify_trade(row):
    """Interpret a governed generated package for negotiation use."""
    out = copy.deepcopy(row)
    utility = _counterparty_utility(out)
    fit = str(out.get("acceptance_fit") or out.get("source_recommendation_band") or "UNKNOWN").upper()
    bilateral = utility is not None and utility >= 0.0
    if not bilateral:
        bucket = "THEORETICAL_UPGRADE"
        posture = "DO_NOT_TREAT_AS_ACTIONABLE"
        reason = "The current generated package does not clear governed counterparty bilateral utility."
    elif fit in {"HIGH", "MEDIUM"}:
        bucket = "ACTIONABLE_NEGOTIATION"
        posture = "WORTH_SENDING_OR_OPENING_NEGOTIATION"
        reason = "The package clears governed bilateral utility and descriptive negotiation fit is medium/high."
    else:
        bucket = "NEGOTIATION_TARGET"
        posture = "EXPLORE_PRICE_NOT_EXECUTION_READY"
        reason = "The package clears governed bilateral utility, but descriptive negotiation fit is weak."
    out["negotiation_frontier"] = {
        "model_version": MODEL_VERSION,
        "authority": AUTHORITY,
        "bucket": bucket,
        "negotiation_posture": posture,
        "counterparty_shared_utility": utility,
        "counterparty_bilateral_viable": bilateral,
        "descriptive_acceptance_fit": fit,
        "acceptance_fit_is_probability": False,
        "reason": reason,
        "creates_new_trade_value": False,
        "creates_new_acceptance_probability": False,
    }
    return out


def build(rows):
    classified = [classify_trade(x) for x in rows if str(x.get("channel") or "") == "TRADE"]
    actionable = [x for x in classified if x["negotiation_frontier"]["bucket"] == "ACTIONABLE_NEGOTIATION"]
    explore = [x for x in classified if x["negotiation_frontier"]["bucket"] == "NEGOTIATION_TARGET"]
    theoretical = [x for x in classified if x["negotiation_frontier"]["bucket"] == "THEORETICAL_UPGRADE"]
    return {
        "model_version": MODEL_VERSION,
        "authority": AUTHORITY,
        "actionable_negotiations": actionable,
        "negotiation_targets": explore,
        "theoretical_upgrades": theoretical,
        "best_actionable_trade": actionable[0] if actionable else None,
        "best_negotiation_target": explore[0] if explore else None,
        "best_theoretical_upgrade": theoretical[0] if theoretical else None,
        "policy": {
            "interpretation_owned_by_trade_decision": True,
            "preserves_upstream_gm3_order_within_each_bucket": True,
            "acceptance_fit_is_diagnostic_not_probability": True,
            "bilateral_viability_uses_governed_counterparty_utility": True,
            "no_arbitrary_utility_acceptance_exchange_rate": True,
            "behavioral_intelligence_supplies_evidence_not_decision_authority": True,
            "opportunity_engine_may_route_and_present_but_not_reclassify": True,
        },
    }
