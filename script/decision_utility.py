#!/usr/bin/env python3
"""Shared FSFFL decision-utility primitives.

This module exists because Trade Decision and GM3 Team Improvement now consume
the same primitive current/future/liquidity/resilience utility. The constants
remain explicitly provisional; centralizing them removes competing application
weight systems and creates one place for subsequent evidence-based scaling.

Negotiation/acceptance is intentionally excluded. This answers "how good is the
move for the focal franchise?" rather than "how likely is the other manager to
accept?".
"""
from __future__ import annotations

from typing import Any, Dict

MODEL_VERSION = "FSFFL-Shared-Decision-Utility-1.0"

# Legacy primitive scales retained exactly during structural convergence.
# They are not claimed empirically calibrated and are governed by
# TRADE-SCORE-001. A later evidence-based scaling pass can replace them here for
# every consuming application at once.
CURRENT_TITLE_SCALE = 25000.0
CURRENT_PLAYOFF_SCALE = 5000.0
CURRENT_WINS_SCALE = 400.0
CURRENT_POINTS_SCALE = 1.25
FUTURE_OPTIONALITY_SCALE = 0.18
LIQUIDITY_SCALE = 0.25
RESILIENCE_SCALE = 0.15
OPPONENT_EXTERNALITY_SCALE = 12000.0

REFERENCE_OBJECTIVE_WEIGHTS = {
    "current": 0.40,
    "future": 0.35,
    "liquidity": 0.10,
    "resilience": 0.15,
}


def sf(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def primitive_blocks(sim: Dict[str, Any]) -> Dict[str, float]:
    d = sim.get("focus_delta") or {}
    s = sim.get("strategic") or {}
    title = sf(d.get("championship_probability"))
    playoff = sf(d.get("playoff_probability"))
    wins = sf(d.get("expected_wins"))
    points = sf(d.get("expected_points_for"))
    dynasty = sf(s.get("market_dynasty_delta"))
    optionality = sf(s.get("optionality_value_delta"))
    liquidity = sf(s.get("liquidity_value_delta"))
    resilience = sf(s.get("resilience_value_delta"))
    externality = sf(sim.get("net_title_equity_swing_against_focus"))
    return {
        "current": (
            CURRENT_TITLE_SCALE * title
            + CURRENT_PLAYOFF_SCALE * playoff
            + CURRENT_WINS_SCALE * wins
            + CURRENT_POINTS_SCALE * points
        ),
        "future": dynasty + FUTURE_OPTIONALITY_SCALE * optionality,
        "liquidity": LIQUIDITY_SCALE * liquidity,
        "resilience": RESILIENCE_SCALE * resilience,
        "opponent_externality": externality,
    }


def score(sim: Dict[str, Any]) -> Dict[str, Any]:
    s = sim.get("strategic") or {}
    weights = s.get("objective_weights")
    if not weights:
        raise RuntimeError(
            "Shared decision utility requires governed continuous objective_weights; "
            "categorical fallback weights are forbidden"
        )

    mult = {
        k: sf(weights.get(k), REFERENCE_OBJECTIVE_WEIGHTS[k]) / REFERENCE_OBJECTIVE_WEIGHTS[k]
        for k in REFERENCE_OBJECTIVE_WEIGHTS
    }
    blocks = primitive_blocks(sim)
    components = {
        "current": mult["current"] * blocks["current"],
        "future": mult["future"] * blocks["future"],
        "liquidity": mult["liquidity"] * blocks["liquidity"],
        "resilience": mult["resilience"] * blocks["resilience"],
        "opponent_externality": -mult["current"] * OPPONENT_EXTERNALITY_SCALE * blocks["opponent_externality"],
    }
    total = sum(components.values())
    return {
        "score": round(total, 2),
        "components": {k: round(v, 2) for k, v in components.items()},
        "objective_weights": dict(weights),
        "model_version": MODEL_VERSION,
        "scale_status": "PROVISIONAL_CENTRALIZED_PENDING_EVIDENCE_BASED_SCALING",
        "negotiation_plausibility_incremental_weight": 0.0,
        "composite_strategic_and_break_glass_incremental_weight": 0.0,
    }
