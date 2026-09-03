#!/usr/bin/env python3
"""Shared FSFFL decision-utility primitives.

Trade Decision and GM3 Team Improvement consume the same primitive utility.

Version 2.1 retains the evidence-governed four-channel objective while repairing
current-season evidence reconciliation:
- Simulator outcome changes are still normalized league-relatively and converted
  to value units using the focal roster's observed market-redraft scale;
- that Simulator-derived current value is then combined by an unweighted median
  with any directly observed transaction market-redraft delta and optimized
  starter-redraft delta available from the same hypothetical;
- the median ensemble avoids double-counting correlated current-season signals,
  introduces no fitted exchange coefficient, and prevents a small simulation
  gain from automatically overriding two contradictory roster/value signals;
- future value remains the observed market-dynasty delta;
- liquidity and resilience retain their existing value-denominated deltas;
- optionality remains diagnostic only because residual incremental value has not
  been independently demonstrated;
- opponent title externality is folded directly into the championship outcome
  before normalization rather than receiving a separate coefficient.

The only cross-channel weights are the governed continuous objective weights.
No categorical state fallback or fixed current/future exchange-rate coefficient
is used here.
"""
from __future__ import annotations

import statistics
import importlib.util
from pathlib import Path
from typing import Any, Dict

SCRIPT = Path(__file__).resolve().parent

def _load_package_concentration():
    path = SCRIPT / "package_concentration.py"
    spec = importlib.util.spec_from_file_location("fsffl_package_concentration", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


PACKAGE_CONCENTRATION = _load_package_concentration()

MODEL_VERSION = "FSFFL-Shared-Decision-Utility-2.2"


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


def _current_value_evidence(sim, simulator_value):
    """Return same-unit current-season evidence without inventing coefficients.

    Each included observation is already denominated in market-redraft value
    units. Missing sources are omitted rather than silently treated as zero.
    """
    evidence = {"simulator_outcome_value": sf(simulator_value)}
    strategic = sim.get("strategic") or {}

    if strategic.get("market_redraft_delta") is not None:
        evidence["transaction_market_redraft_delta"] = sf(
            strategic.get("market_redraft_delta")
        )

    diagnosis = sim.get("roster_diagnosis") or {}
    before = diagnosis.get("before") or {}
    after = diagnosis.get("after") or {}
    if (
        before.get("starter_redraft_value") is not None
        and after.get("starter_redraft_value") is not None
    ):
        evidence["optimized_starter_redraft_delta"] = (
            sf(after.get("starter_redraft_value"))
            - sf(before.get("starter_redraft_value"))
        )

    return evidence


def primitive_blocks(sim: Dict[str, Any]) -> Dict[str, Any]:
    s = sim.get("strategic") or {}
    current_signal, normalized_outcomes = _relative_current_outcomes(sim)
    redraft_scale, redraft_scale_source = _market_redraft_scale(s)

    simulator_current_value = current_signal * redraft_scale
    current_evidence = _current_value_evidence(sim, simulator_current_value)
    current_value = statistics.median(current_evidence.values())

    package_center = PACKAGE_CONCENTRATION.transform_future_value(sim, "center")
    future_value = sf(package_center.get("package_effective_future_value"))
    package_sensitivity = PACKAGE_CONCENTRATION.sensitivity(sim)
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
            "current_value_evidence": {
                k: round(v, 2) for k, v in current_evidence.items()
            },
            "current_value_evidence_count": len(current_evidence),
            "current_value_aggregation": "UNWEIGHTED_MEDIAN_SAME_UNIT_EVIDENCE",
            "current_value_double_counting_avoided": True,
            "optionality_value_delta_diagnostic": sf(s.get("optionality_value_delta")),
            "optionality_incremental_value_authorized": False,
            "opponent_title_externality_has_separate_coefficient": False,
            "fixed_unit_conversion_coefficients_used": False,
            "package_concentration": package_center,
            "package_concentration_sensitivity_future_primitives": {
                "mild": round(sf(package_sensitivity.get("mild_future")), 2),
                "center": round(sf(package_sensitivity.get("center_future")), 2),
                "strong": round(sf(package_sensitivity.get("strong_future")), 2),
            },
            "package_concentration_authority": "ACTIVE_BOUNDED_PROVISIONAL_PRIOR",
            "package_concentration_empirically_calibrated": False,
            "package_concentration_replaces_future_additivity": True,
            "package_concentration_new_channel_created": False,
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
    authorization = s.get("incremental_channel_authorization") or {}
    active = {
        "current": True,
        "future": True,
        "liquidity": bool(authorization.get("liquidity", True)),
        "resilience": bool(authorization.get("resilience", True)),
    }
    raw_weights = {k: max(0.0, sf(weights.get(k))) for k in required}
    suppressed_weight = {
        k: raw_weights[k] if not active[k] else 0.0
        for k in required
    }
    w = {
        k: (raw_weights[k] if active[k] else 0.0)
        for k in required
    }
    total_weight = sum(w.values())
    if total_weight <= 0:
        raise RuntimeError("Shared decision utility received non-positive authorized objective weights")
    # Structural de-duplication rule: a channel explicitly disabled by the
    # governing strategic profile cannot consume objective-weight mass. The
    # remaining governed weights are renormalized; no new coefficient is fit.
    w = {k: v / total_weight for k, v in w.items()}

    blocks = primitive_blocks(sim)
    components = {k: w[k] * sf(blocks[k]) for k in required}
    total = sum(components.values())

    package_diag = (blocks.get("diagnostics") or {}).get("package_concentration_sensitivity_future_primitives") or {}
    prior_scores = {}
    for prior_name in ("mild", "center", "strong"):
        alt_components = dict(components)
        if prior_name in package_diag:
            alt_components["future"] = w["future"] * sf(package_diag[prior_name])
        prior_scores[prior_name] = round(sum(alt_components.values()), 2)
    signs = {"positive" if v > 0 else "negative" if v < 0 else "zero" for v in prior_scores.values()}
    if signs == {"positive"}:
        prior_robustness = "ROBUST_POSITIVE_ACROSS_PRIOR_RANGE"
    elif signs == {"negative"}:
        prior_robustness = "ROBUST_NEGATIVE_ACROSS_PRIOR_RANGE"
    else:
        prior_robustness = "SENSITIVE_TO_PRIOR_RANGE"

    return {
        "score": round(total, 2),
        "components": {k: round(v, 2) for k, v in components.items()},
        "primitive_blocks": {k: round(sf(blocks[k]), 2) for k in required},
        "diagnostics": blocks["diagnostics"],
        "objective_weights": {k: round(v, 6) for k, v in w.items()},
        "objective_weights_before_channel_authorization": {
            k: round(v, 6) for k, v in raw_weights.items()
        },
        "incremental_channel_authorization": active,
        "suppressed_unauthorized_objective_weight": {
            k: round(v, 6) for k, v in suppressed_weight.items() if v > 0
        },
        "model_version": MODEL_VERSION,
        "scale_status": "DATA_DERIVED_LEAGUE_RELATIVE_NO_FIXED_UNIT_CONVERSION_COEFFICIENTS",
        "negotiation_plausibility_incremental_weight": 0.0,
        "package_concentration_prior_scores": prior_scores,
        "package_concentration_prior_range_decision_robustness": prior_robustness,
        "composite_strategic_and_break_glass_incremental_weight": 0.0,
    }
