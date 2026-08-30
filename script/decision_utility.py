#!/usr/bin/env python3
"""Shared FSFFL decision-utility primitives.

Trade Decision and GM3 Team Improvement consume the same primitive utility.

Version 2 replaces the prior hand-set unit-conversion constants with data-derived
scales:
- current competitive impact is a median ensemble of league-relative Simulator
  outcome changes, converted to value units using the focal roster's observed
  market-redraft scale;
- future value remains the observed market-dynasty delta;
- liquidity and resilience retain their existing value-denominated deltas;
- optionality is diagnostic only because the market anchor already embeds
  expectations and no residual incremental value has been demonstrated;
- opponent title externality is folded directly into the championship outcome
  before normalization rather than receiving a separate coefficient.

The only cross-channel weights are the governed continuous objective weights.
No categorical state fallback or fixed current/future exchange-rate coefficient
is used here.
"""
from __future__ import annotations

import statistics
from typing import Any, Dict

MODEL_VERSION = "FSFFL-Shared-Decision-Utility-2.0"


def sf(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _positive_scale(x):
    x = sf(x)
    return x if x > 0 else None


def _market_redraft_scale(strategic):
    scale = _positive_scale(strategic.get("baseline_team_market_redraft_value"))
    if scale is not None:
        return scale, "baseline_team_market_redraft_value"

    # Compatibility fallback for legacy/test rows that do not yet expose the
    # full-team scale. Use observed transaction redraft exposure rather than a
    # hand-set constant.
    rows = list(strategic.get("sent") or []) + list(strategic.get("received") or [])
    exposure = sum(abs(sf(x.get("market_redraft"))) for x in rows)
    if exposure > 0:
        return exposure, "transaction_market_redraft_exposure"

    return 1.0, "unit_fallback_no_redraft_evidence"


def _relative_current_outcomes(sim):
    d = sim.get("focus_delta") or {}
    ref = sim.get("league_reference") or {}
    values = {}

    def add(name, numerator, denominator):
        den = _positive_scale(denominator)
        if den is not None:
            values[name] = sf(numerator) / den

    add("expected_points_for", d.get("expected_points_for"), ref.get("expected_points_for_mean"))
    add("expected_wins", d.get("expected_wins"), ref.get("expected_wins_mean"))
    add("playoff_probability", d.get("playoff_probability"), ref.get("playoff_probability_mean"))

    title_delta = sf(d.get("championship_probability"))
    buyer_title_delta = max(0.0, sf(sim.get("buyer_championship_probability_delta")))
    if "net_title_equity_swing_against_focus" in sim:
        net_title_delta = -sf(sim.get("net_title_equity_swing_against_focus"))
    else:
        net_title_delta = title_delta - buyer_title_delta
    add("net_championship_probability", net_title_delta, ref.get("championship_probability_mean"))

    # Median rather than a weighted sum prevents four highly correlated current
    # outcomes from being counted four times and requires no hand-set relative
    # coefficients among points, wins, playoffs and championship equity.
    signal = statistics.median(values.values()) if values else 0.0
    return signal, values


def primitive_blocks(sim: Dict[str, Any]) -> Dict[str, Any]:
    s = sim.get("strategic") or {}
    current_signal, normalized_outcomes = _relative_current_outcomes(sim)
    redraft_scale, redraft_scale_source = _market_redraft_scale(s)

    current_value = current_signal * redraft_scale
    future_value = sf(s.get("market_dynasty_delta"))
    liquidity_value = sf(s.get("liquidity_value_delta"))
    resilience_value = sf(s.get("resilience_value_delta"))

    return {
        "current": current_value,
        "future": future_value,
        "liquidity": liquidity_value,
        "resilience": resilience_value,
        "diagnostics": {
            "league_relative_current_outcomes": {
                k: round(v, 6) for k, v in normalized_outcomes.items()
            },
            "current_relative_signal": round(current_signal, 6),
            "current_value_scale": round(redraft_scale, 2),
            "current_value_scale_source": redraft_scale_source,
            "optionality_value_delta_diagnostic": sf(s.get("optionality_value_delta")),
            "optionality_incremental_value_authorized": False,
            "opponent_title_externality_has_separate_coefficient": False,
            "fixed_unit_conversion_coefficients_used": False,
        },
    }


def score(sim: Dict[str, Any]) -> Dict[str, Any]:
    s = sim.get("strategic") or {}
    weights = s.get("objective_weights")
    if not weights:
        raise RuntimeError(
            "Shared decision utility requires governed continuous objective_weights; "
            "categorical fallback weights are forbidden"
        )

    required = ("current", "future", "liquidity", "resilience")
    w = {k: max(0.0, sf(weights.get(k))) for k in required}
    total_weight = sum(w.values())
    if total_weight <= 0:
        raise RuntimeError("Shared decision utility received non-positive objective weights")
    # Normalize the governed weights themselves in case a calibration artifact
    # carries harmless rounding drift.
    w = {k: v / total_weight for k, v in w.items()}

    blocks = primitive_blocks(sim)
    components = {k: w[k] * sf(blocks[k]) for k in required}
    total = sum(components.values())

    return {
        "score": round(total, 2),
        "components": {k: round(v, 2) for k, v in components.items()},
        "primitive_blocks": {k: round(sf(blocks[k]), 2) for k in required},
        "diagnostics": blocks["diagnostics"],
        "objective_weights": {k: round(v, 6) for k, v in w.items()},
        "model_version": MODEL_VERSION,
        "scale_status": "DATA_DERIVED_LEAGUE_RELATIVE_NO_FIXED_UNIT_CONVERSION_COEFFICIENTS",
        "negotiation_plausibility_incremental_weight": 0.0,
        "composite_strategic_and_break_glass_incremental_weight": 0.0,
    }
