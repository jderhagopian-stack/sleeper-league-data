#!/usr/bin/env python3
"""Canonical bilateral buyer current-state hard gate.

Mechanical extraction of the current production v1.15 buyer-rationality
safeguard. The gate prevents candidate recommendations that are strongly
irrational for the counterparty in their current competitive state.

Behavioral/acceptance fit remains secondary evidence. This gate is based on
buyer utility losses and does not change focal trade valuation.
"""
from __future__ import annotations

MODEL_VERSION = "FSFFL-Bilateral-Buyer-Gate-1.0"


def sf(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def evaluate(br):
    state = str(br.get("buyer_state") or "unknown")
    title = sf(br.get("buyer_title_delta"))
    dynasty = sf(br.get("buyer_market_dynasty_delta"))
    redraft = sf(br.get("buyer_market_redraft_delta"))
    break_glass = sf(br.get("buyer_break_glass_delta"))

    fail = False
    reason = None
    if (
        state == "elite_contender"
        and title <= -0.03
        and dynasty < 0
        and break_glass < 0
    ):
        fail = True
        reason = "elite contender loses title equity plus dynasty and break-glass value"
    elif (
        state == "contender"
        and title <= -0.04
        and dynasty < 0
        and break_glass < 0
    ):
        fail = True
        reason = "contender loses meaningful title equity plus dynasty and break-glass value"
    elif state == "retool" and dynasty <= -1200 and break_glass <= -1200:
        fail = True
        reason = "retool buyer gives up excessive long-term and break-glass value"
    elif state == "rebuild" and dynasty <= -900 and break_glass <= -900:
        fail = True
        reason = "rebuild buyer gives up excessive long-term and break-glass value"

    if dynasty <= -1400 and redraft <= -1800 and break_glass <= -1200:
        fail = True
        reason = "buyer loses heavily across dynasty, redraft, and break-glass value"

    return (not fail), reason


def apply(br):
    passes, reason = evaluate(br)
    br["market_intelligence_hard_gate_pass"] = bool(passes)
    br["market_intelligence_hard_gate_reason"] = (
        reason or "buyer current-state utility clears bilateral hard gate"
    )
    if not passes:
        br["current_state_viable"] = False
        br["current_state_gate"] = "BUYER_IRRATIONAL"
        br["reason"] = reason
        br["heuristic_acceptance_fit_score"] = min(
            sf(br.get("heuristic_acceptance_fit_score")), 0.27
        )
        br["heuristic_acceptance_fit"] = "VERY_LOW"
    return br
