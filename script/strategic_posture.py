#!/usr/bin/env python3
"""Governed strategic-posture policy for FSFFL decision applications.

Competitive state remains model-owned. Strategic posture is a separate
preference layer:
- AUTO preserves the model-derived continuous objective weights.
- Explicit owner choices reuse the existing governed state-weight curve rather
  than introducing new valuation coefficients.
- Search directives affect discovery coverage only; final scoring remains
  Shared Decision Utility / GM3 authority.
"""
from __future__ import annotations

MODEL_VERSION = "FSFFL-Strategic-Posture-1.0"

POSTURES = (
    "AUTO",
    "PUSH_CHIPS_IN",
    "BALANCED_CONTENDER",
    "PRESERVE_FUTURE_VALUE",
    "RETOOL",
    "REBUILD",
)

ALIASES = {
    "AUTO": "AUTO",
    "MODEL": "AUTO",
    "DEFAULT": "AUTO",
    "PUSH": "PUSH_CHIPS_IN",
    "PUSH_CHIPS_IN": "PUSH_CHIPS_IN",
    "WIN_NOW": "PUSH_CHIPS_IN",
    "ALL_IN": "PUSH_CHIPS_IN",
    "BALANCED": "BALANCED_CONTENDER",
    "BALANCED_CONTENDER": "BALANCED_CONTENDER",
    "CONTEND": "BALANCED_CONTENDER",
    "PRESERVE": "PRESERVE_FUTURE_VALUE",
    "PRESERVE_FUTURE": "PRESERVE_FUTURE_VALUE",
    "PRESERVE_FUTURE_VALUE": "PRESERVE_FUTURE_VALUE",
    "ACCUMULATE_FUTURE_VALUE": "PRESERVE_FUTURE_VALUE",
    "RETOOL": "RETOOL",
    "REBUILD": "REBUILD",
}

DESCRIPTIONS = {
    "AUTO": "Use the model-derived competitive-state weights.",
    "PUSH_CHIPS_IN": "Maximize emphasis on immediate competitive improvement using the aggressive end of the existing governed curve.",
    "BALANCED_CONTENDER": "Use the existing contender anchor to balance current contention and future value.",
    "PRESERVE_FUTURE_VALUE": "Do not use more win-now emphasis than the existing contender anchor; favor future-value-preserving search coverage.",
    "RETOOL": "Use the existing retool anchor and favor future-value-preserving / roster-refresh opportunities.",
    "REBUILD": "Use the future-focused end of the existing governed curve and favor future-value accumulation.",
}

SEARCH_LANE_ORDERS = {
    "AUTO": (
        "focal_utility", "bilateral_utility", "negotiation_fit",
        "seller_motivation", "target_diversity", "outbound_future_value",
    ),
    "BALANCED_CONTENDER": (
        "focal_utility", "bilateral_utility", "negotiation_fit",
        "target_diversity", "seller_motivation", "outbound_future_value",
    ),
    "PUSH_CHIPS_IN": (
        "immediate_current_value", "focal_utility", "bilateral_utility",
        "negotiation_fit", "seller_motivation", "target_diversity",
        "outbound_future_value",
    ),
    "PRESERVE_FUTURE_VALUE": (
        "outbound_future_value", "future_value_preservation", "bilateral_utility",
        "focal_utility", "target_diversity", "negotiation_fit", "seller_motivation",
    ),
    "RETOOL": (
        "outbound_future_value", "future_value_preservation", "target_diversity",
        "bilateral_utility", "seller_motivation", "focal_utility", "negotiation_fit",
    ),
    "REBUILD": (
        "outbound_future_value", "future_value_preservation", "seller_motivation",
        "target_diversity", "bilateral_utility", "focal_utility", "negotiation_fit",
    ),
}


def normalize_selection(value):
    key = str(value or "AUTO").strip().upper().replace("-", "_").replace(" ", "_")
    if key not in ALIASES:
        raise ValueError(
            f"Unknown strategic posture {value!r}; expected one of {', '.join(POSTURES)}"
        )
    return ALIASES[key]


def _target_score(selection, weight_resolution, calibration):
    inputs = weight_resolution.get("inputs") or {}
    calculated = float(inputs.get("competitive_strength_score") or 0.0)
    thresholds = calibration.get("classification_thresholds") or {}
    anchors = calibration.get("anchor_points") or []
    scores = sorted(float(x.get("competitive_strength_score", x.get("contender_score", 0.0))) for x in anchors)
    lo = scores[0] if scores else 0.0
    hi = scores[-1] if scores else 1.0
    contender = float(thresholds.get("contender", 0.55))
    retool = float(thresholds.get("retool", 0.35))

    if selection == "PUSH_CHIPS_IN":
        return hi, "existing_maximum_competitive_anchor"
    if selection == "BALANCED_CONTENDER":
        return contender, "existing_contender_anchor"
    if selection == "PRESERVE_FUTURE_VALUE":
        return min(calculated, contender), "calculated_state_capped_at_existing_contender_anchor"
    if selection == "RETOOL":
        return retool, "existing_retool_anchor"
    if selection == "REBUILD":
        return lo, "existing_future_focused_anchor"
    return calculated, "model_derived_competitive_state"


def resolve(weight_resolution, selected, weighting_module, calibration=None):
    selection = normalize_selection(selected)
    cal = calibration or weighting_module.load_calibration()
    calculated_state = weight_resolution.get("state")
    calculated_weights = dict(weight_resolution.get("weights") or {})
    calculated_score = float((weight_resolution.get("inputs") or {}).get("competitive_strength_score") or 0.0)

    if selection == "AUTO":
        active_weights = calculated_weights
        target_score = calculated_score
        basis = "model_derived_competitive_state"
    else:
        target_score, basis = _target_score(selection, weight_resolution, cal)
        active_weights = weighting_module.interpolate(target_score, cal.get("anchor_points") or [])

    return {
        "model_version": MODEL_VERSION,
        "competitive_state": calculated_state,
        "competitive_strength_score": round(calculated_score, 6),
        "selected_posture": selection,
        "posture_source": "MODEL_DEFAULT" if selection == "AUTO" else "OWNER_OVERRIDE",
        "active_weights": {k: round(float(v), 6) for k, v in active_weights.items()},
        "calculated_state_weights": {k: round(float(v), 6) for k, v in calculated_weights.items()},
        "posture_curve_score": round(float(target_score), 6),
        "posture_weight_basis": basis,
        "description": DESCRIPTIONS[selection],
        "search_lane_order": list(SEARCH_LANE_ORDERS[selection]),
        "competitive_state_is_modified_by_owner_override": False,
        "new_valuation_coefficients_introduced": False,
        "uses_existing_governed_weight_curve": True,
    }
