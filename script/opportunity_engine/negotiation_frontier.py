#!/usr/bin/env python3
"""Negotiation-frontier view for Opportunity Engine.

This module does not create a new valuation or acceptance model. It classifies
already-governed GM3 trade candidates using the canonical bilateral buyer
utility and exposes heuristic acceptance fit only as descriptive evidence.
"""
from __future__ import annotations

import copy

MODEL_VERSION = "FSFFL-Opportunity-Negotiation-Frontier-1.0"


def sf(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _seller_utility(row):
    for key in ("seller_strategic_utility_precomputed", "buyer_decision_utility_score"):
        if row.get(key) is not None:
            return sf(row.get(key))
    return None


def classify_trade(row):
    """Classify a governed candidate without rescoring it.

    Non-negative counterparty shared utility is the canonical bilateral
    viability condition. Acceptance bands remain heuristic diagnostics and are
    never described as probabilities or used as an exchange rate against focal
    utility.
    """
    out = copy.deepcopy(row)
    seller_utility = _seller_utility(out)
    fit = str(out.get("acceptance_fit") or out.get("source_recommendation_band") or "UNKNOWN").upper()
    bilateral = seller_utility is not None and seller_utility >= 0.0
    if not bilateral:
        bucket = "THEORETICAL_UPGRADE"
        reason = "Counterparty governed shared utility is negative or unavailable; current package is not an actionable opportunity."
    elif fit in {"HIGH", "MEDIUM"}:
        bucket = "ACTIONABLE_NEGOTIATION"
        reason = "Counterparty governed utility is non-negative and descriptive negotiation fit is medium/high."
    else:
        bucket = "NEGOTIATION_TARGET"
        reason = "Counterparty governed utility is non-negative, but descriptive negotiation fit is weak; explore price rather than treat as executable."
    out["negotiation_frontier"] = {
        "model_version": MODEL_VERSION,
        "bucket": bucket,
        "counterparty_shared_utility": seller_utility,
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
        "actionable_negotiations": actionable,
        "negotiation_targets": explore,
        "theoretical_upgrades": theoretical,
        "best_actionable_trade": actionable[0] if actionable else None,
        "best_negotiation_target": explore[0] if explore else None,
        "best_theoretical_upgrade": theoretical[0] if theoretical else None,
        "policy": {
            "preserves_upstream_gm3_order_within_each_bucket": True,
            "acceptance_fit_is_diagnostic_not_probability": True,
            "bilateral_viability_uses_governed_counterparty_utility": True,
            "no_arbitrary_utility_acceptance_exchange_rate": True,
        },
    }
