#!/usr/bin/env python3
"""Canonical bilateral buyer-utility gate.

The gate no longer uses categorical contender/retool/rebuild thresholds.
Counterparty feasibility is based on the sign of the same continuous shared
decision utility used elsewhere. If governed buyer utility is unavailable, the
candidate is retained rather than rejected by a legacy heuristic.

Acceptance-fit bands remain descriptive and are not calibrated probabilities.
"""
from __future__ import annotations

MODEL_VERSION = "FSFFL-Bilateral-Buyer-Gate-2.0"


def sf(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def evaluate(br):
    score = br.get("buyer_decision_utility_score")
    if score is None:
        return True, "governed buyer utility unavailable; legacy categorical gate not used"
    if sf(score) >= 0.0:
        return True, "buyer shared continuous utility is non-negative"
    return False, "buyer shared continuous utility is negative"


def apply(br):
    passes, reason = evaluate(br)
    br["market_intelligence_hard_gate_pass"] = bool(passes)
    br["market_intelligence_hard_gate_reason"] = reason
    br["bilateral_gate_model_version"] = MODEL_VERSION
    br["categorical_state_thresholds_authoritative"] = False
    br["missing_utility_defaults_to_retain_for_search"] = True
    if not passes:
        br["current_state_viable"] = False
        br["current_state_gate"] = "BUYER_UTILITY_NEGATIVE"
        br["reason"] = reason
    return br
