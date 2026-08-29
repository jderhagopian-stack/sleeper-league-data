#!/usr/bin/env python3
"""Evidence audit for FSFFL future-pick economics.

Scope is intentionally limited to future-pick economics. Projection means,
projection uncertainty, source weighting and position-specific projection logic
are not read or modified here.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OUT = DATA / "audit"
OUT.mkdir(parents=True, exist_ok=True)

ENGINE = ROOT / "script" / "build_fsffl_gm_engine.py"
OVERRIDES = ROOT / "script" / "nonprojection_high_priority_overrides.py"
DECISION_LAB = ROOT / "script" / "decision_lab_state_aware.py"
REGISTRY = DATA / "model_parameter_registry.json"
PICK_QUALITY = DATA / "pick_quality_model.json"
MARKET = DATA / "market_values_fantasycalc.json"
TRADES = DATA / "trade_ledger.json"
READINESS = OUT / "pick_outcome_readiness_audit.json"

MODEL_VERSION = "FSFFL-Future-Pick-Governance-1.2"


def load_json(path: Path, default=None):
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def marker(text: str, pattern: str) -> bool:
    return re.search(pattern, text, flags=re.MULTILINE | re.DOTALL) is not None


def median(xs):
    vals = sorted(float(x) for x in xs)
    if not vals:
        return None
    n = len(vals)
    return vals[n // 2] if n % 2 else 0.5 * (vals[n // 2 - 1] + vals[n // 2])


def observed_market_pick_cells(market):
    cells = {}
    for row in market.get("dynasty", []) if isinstance(market, dict) else []:
        if str(row.get("position") or "").upper() != "PICK":
            continue
        name = str(row.get("name") or "")
        ym = re.search(r"(20\d{2})", name)
        rm = re.search(r"\b([123])(?:st|nd|rd)\b", name.lower())
        if not ym or not rm:
            continue
        year = int(ym.group(1))
        rnd = int(rm.group(1))
        low = name.lower()
        explicit_tier = any(x in low for x in ("early", "mid", "late"))
        tier = "early" if "early" in low else "late" if "late" in low else "mid"
        value = float(row.get("value") or 0.0)
        key = (year, tier, rnd)
        if value > 0 and (key not in cells or explicit_tier):
            cells[key] = value
    return cells


def market_shape_evidence(cells):
    tier_ratios = {}
    for rnd in (1, 2, 3):
        out = {}
        for tier in ("early", "late"):
            ratios = []
            for (year, t, r), v in cells.items():
                if r == rnd and t == tier and (year, "mid", rnd) in cells:
                    ratios.append(v / cells[(year, "mid", rnd)])
            out[tier] = round(median(ratios), 4) if ratios else None
        tier_ratios[str(rnd)] = out

    annual = {}
    for rnd in (1, 2, 3):
        mids = sorted((y, v) for (y, t, r), v in cells.items() if r == rnd and t == "mid")
        factors = []
        transitions = []
        for (y0, v0), (y1, v1) in zip(mids, mids[1:]):
            gap = y1 - y0
            if gap > 0 and v0 > 0 and v1 > 0:
                factor = (v1 / v0) ** (1.0 / gap)
                factors.append(factor)
                transitions.append({"from": y0, "to": y1, "factor": round(factor, 4)})
        annual[str(rnd)] = {
            "median_annual_factor": round(median(factors), 4) if factors else None,
            "transitions": transitions,
        }
    return tier_ratios, annual


def pick_trade_frequency(trades):
    events = Counter()
    unique = {1: set(), 2: set(), 3: set()}
    for tr in trades if isinstance(trades, list) else []:
        if str(tr.get("status") or "").lower() != "complete":
            continue
        seen = set()
        for side in tr.get("sides") or []:
            for p in side.get("sent_picks") or []:
                try:
                    rnd = int(p.get("round"))
                    year = int(p.get("season"))
                    orig = int(p.get("original_roster_id"))
                except (TypeError, ValueError):
                    continue
                if rnd not in (1, 2, 3):
                    continue
                key = (year, rnd, orig)
                seen.add(key)
        for year, rnd, orig in seen:
            events[rnd] += 1
            unique[rnd].add((year, orig))
    return {
        "transfer_events": {str(r): events[r] for r in (1, 2, 3)},
        "unique_pick_assets_traded": {str(r): len(unique[r]) for r in (1, 2, 3)},
    }


def main():
    src = ENGINE.read_text(encoding="utf-8")
    overrides = OVERRIDES.read_text(encoding="utf-8") if OVERRIDES.exists() else ""
    decision_lab = DECISION_LAB.read_text(encoding="utf-8") if DECISION_LAB.exists() else ""
    registry = load_json(REGISTRY, {}) or {}
    pickq = load_json(PICK_QUALITY, {}) or {}
    readiness = load_json(READINESS, {}) or {}
    market = load_json(MARKET, {}) or {}
    trades = load_json(TRADES, []) or []

    params = {p.get("id"): p for p in registry.get("parameters", [])}
    governed = params.get("PICK-MODEL-001", {})

    # Governance cares about fallback ORDER, not merely whether a provisional
    # last-resort constant still exists. The market-derived path must be tried
    # first; bounded legacy constants may remain only when the market source
    # cannot identify the needed tier shape or time curve.
    detected = {
        "external_market_pick_detection": "infer_fc_pick_values" in src,
        "market_derived_tier_shape": "_observed_pick_tier_multiplier" in src,
        "market_derived_round_specific_time_curve": "_observed_pick_year_factor" in src,
        "market_derived_tier_shape_precedes_fixed_last_resort": all(
            x in src for x in (
                "tier_mult = _observed_pick_tier_multiplier(detected, tier, rnd)",
                "if tier_mult is not None:",
                "return base * tier_mult",
                'return base * {"early": 1.18, "mid": 1.0, "late": 0.84}[tier]',
            )
        ),
        "round_specific_market_time_curve_precedes_fixed_last_resort": all(
            x in src for x in (
                "year_factor = _observed_pick_year_factor(detected, rnd)",
                "if year_factor is not None and year_factor > 0:",
                "return v0 * (year_factor ** (year - y0))",
                "return v0 * (0.88 ** max(0, year - y0))",
            )
        ),
        "last_resort_provisional_fallback_retained": all(
            x in src for x in ("1: 5200.0", "2: 2350.0", "3: 1050.0", "year_discount = 0.88 ** years_out")
        ),
        "quality_strength_horizon_weights": "dynasty_weight = clamp(0.48 + 0.08 * (years_out - 1), 0.48, 0.68)" in src,
        "quality_collapse_mix": "(1.0 - strength) * 0.72 + fragility * 0.28" in src,
        "quality_early_late_transform": all(x in src for x in ("0.10 + 0.58 * collapse_risk", "0.10 + 0.58 * strength")),
        "uncertainty_kept_diagnostic_not_value": all(
            x in src for x in (
                "option = clamp(upside, 0.0, 1.0)",
                '"uncertainty_adds_optionality": False',
            )
        ),
        "pick_incremental_market_overlap_removed": (
            "market_anchor_only_until_residual_incremental_validation" in overrides
            and '"specific_pick_quality": 0.0' in overrides
            and '"optionality": 0.0' in overrides
            and '"liquidity": 0.0' in overrides
            and '"round": 0.0' in overrides
        ),
        "own_pick_control_incremental_value_removed": '"own_pick_control": 0.0' in overrides,
        "pick_liquidity_neutralized_in_package_weighting": (
            'row["liquidity_score_diagnostic"]' in overrides
            and 'row["liquidity_score"] = 0.5' in overrides
        ),
        "pick_liquidity_excluded_from_final_primitive_channel": (
            'pp.get("liquidity_incremental_value_authorized") is False' in decision_lab
        ),
        "pick_quality_optionality_excluded_from_final_primitive_channel": (
            'pp.get("quality_optionality_incremental_value_authorized") is False' in decision_lab
        ),
    }

    cells = observed_market_pick_cells(market)
    tier_ratios, annual_factors = market_shape_evidence(cells)
    liquidity_frequency = pick_trade_frequency(trades)

    picks = pickq.get("picks", []) if isinstance(pickq, dict) else []
    horizon_groups = {}
    for p in picks:
        if not isinstance(p, dict):
            continue
        key = (p.get("original_roster_id"), p.get("round"))
        horizon_groups.setdefault(key, []).append(p)

    invariant_groups = 0
    comparable_groups = 0
    for rows in horizon_groups.values():
        rows = [r for r in rows if r.get("horizon_seasons") in (1, 2, 3)]
        if len({r.get("horizon_seasons") for r in rows}) < 2:
            continue
        comparable_groups += 1
        triples = {
            (
                round(float(r.get("early_scenario_weight") or 0), 3),
                round(float(r.get("mid_scenario_weight") or 0), 3),
                round(float(r.get("late_scenario_weight") or 0), 3),
            )
            for r in rows
        }
        if len(triples) == 1:
            invariant_groups += 1

    readiness_allowed = bool(
        readiness.get("finding", {}).get("authoritative_empirical_claim_allowed", False)
    )

    findings = [
        {
            "id": "PICK-ANCHOR-FALLBACK-001",
            "severity": "HIGH",
            "status": "MARKET_DERIVED_FALLBACK_ACTIVE_WITH_LAST_RESORT_PROVISIONAL",
            "observation": (
                "Direct FantasyCalc cells remain the first choice. Missing early/late cells now inherit "
                "round-specific shape from observed same-round market rows, and missing years inherit a "
                "round-specific observed time curve. Hand-set anchors/discounts remain only as a last-resort "
                "functionality fallback when the external market has insufficient structure."
            ),
            "evidence_tier": (
                "EVIDENCE_BASED_EXTERNAL_ANCHOR for market-derived cells and transforms; "
                "ASSUMPTION_SENSITIVE_PROVISIONAL only for last-resort fallback"
            ),
            "authoritative_incremental_adjustment_claim_allowed": False,
        },
        {
            "id": "PICK-QUALITY-SCENARIO-001",
            "severity": "HIGH",
            "status": "HEURISTIC_SCENARIO_TRANSFORM_ACTIVE",
            "observation": (
                "The team-strength/fragility transforms that map a future pick to early/mid/late scenarios "
                "still lack frozen at-time forecasts joined to realized draft slots across independent seasons. "
                "They remain scenarios, not probabilities, and were not re-tuned from current outcomes."
            ),
            "authoritative_probability_claim_allowed": False,
            "replacement_made": False,
        },
        {
            "id": "PICK-HORIZON-UNCERTAINTY-001",
            "severity": "MEDIUM",
            "status": "HORIZON_UNCERTAINTY_NOT_EMPIRICALLY_IDENTIFIED",
            "observation": (
                f"The current artifact has {invariant_groups} invariant multi-horizon groups out of "
                f"{comparable_groups} comparable groups. No historical frozen forecast-error series exists "
                "to calibrate how pick-slot uncertainty should widen with horizon."
            ),
            "authoritative_uncertainty_claim_allowed": False,
        },
        {
            "id": "PICK-OPTIONALITY-001",
            "severity": "HIGH",
            "status": "FORECAST_UNCERTAINTY_VALUE_BONUS_REMOVED",
            "observation": (
                "Forecast uncertainty remains diagnostic and no longer creates positive option value merely "
                "because the pick is hard to forecast."
            ),
            "authoritative_optionality_claim_allowed": False,
        },
        {
            "id": "PICK-LIQUIDITY-CONTROL-001",
            "severity": "HIGH",
            "status": "INCREMENTAL_PREMIUM_REMOVED_PENDING_VALIDATION",
            "observation": (
                "League trade history does not support the prior monotone 1st>2nd>3rd liquidity constants: "
                "later-round picks appear at least as often in completed trades. Trade inclusion frequency is "
                "not a clean market-depth measure, so it is used to reject the old directional claim, not to "
                "fit new liquidity coefficients. Incremental liquidity/control premiums are therefore disabled "
                "while their diagnostics remain available."
            ),
            "trade_frequency_evidence": liquidity_frequency,
            "authoritative_incremental_adjustment_claim_allowed": False,
        },
        {
            "id": "PICK-DOUBLE-COUNT-001",
            "severity": "HIGH",
            "status": "STRUCTURALLY_DEDUPLICATED",
            "observation": (
                "The external pick anchor already varies by round/year and, where observed or inferred, pick "
                "quality. The strategic layer previously added round, quality, quality-derived optionality and "
                "liquidity premiums again, and the same pick quality/liquidity signals could flow into "
                "downstream package and final-score channels. Those pick-specific positive paths are now "
                "diagnostic-only until residual incremental validation demonstrates value beyond the anchor."
            ),
            "authoritative_incremental_adjustment_claim_allowed": False,
        },
        {
            "id": "PICK-EMPIRICAL-READINESS-001",
            "severity": "HIGH",
            "status": "READY" if readiness_allowed else "NOT_READY_FOR_AUTHORITATIVE_CALIBRATION",
            "observation": (
                "League-specific realized-outcome calibration remains gated by the existing temporal-cohort "
                "readiness check. Current-market evidence can calibrate market shape, but not turn the "
                "team-to-slot scenario transform into validated probabilities."
            ),
            "authoritative_empirical_claim_allowed": readiness_allowed,
        },
    ]

    required_markers = all(detected.values())
    registry_consistent = (
        governed.get("evidence_tier") == "ASSUMPTION_SENSITIVE_PROVISIONAL"
        and governed.get("authoritative_use") is False
        and bool(governed.get("bounds_required"))
    )

    payload = {
        "model_version": MODEL_VERSION,
        "purpose": "Investigate, calibrate where supported, and de-duplicate future-pick economics.",
        # The audit itself is read-only; production state differs because of the
        # governed structural fixes recorded separately below. Keep the legacy
        # field for shared-workflow compatibility without obscuring that fact.
        "production_behavior_changed": False,
        "production_state_changed_by_governed_fix": True,
        "projection_behavior_changed": False,
        "policy": {
            "external_market_pick_values_are_anchor_not_training_labels": True,
            "market_shape_can_replace_weaker_fixed_fallbacks": True,
            "last_resort_fallbacks_remain_explicitly_provisional": True,
            "scenario_weights_are_not_probabilities_without_temporal_validation": True,
            "forecast_uncertainty_value_bonus_removed": True,
            "forecast_uncertainty_is_not_automatically_positive_option_value": True,
            "market_liquidity_and_outcome_optionality_must_be_separated": True,
            "pick_market_anchor_not_repriced_by_duplicate_round_quality_optionality_liquidity_premiums": True,
            "promotion_requires_out_of_sample_improvement": True,
        },
        "market_evidence": {
            "source": market.get("source"),
            "fetched_at_utc": market.get("fetched_at_utc"),
            "settings": market.get("settings", {}).get("dynasty"),
            "observed_pick_cells": len(cells),
            "round_specific_tier_multipliers": tier_ratios,
            "round_specific_time_factors": annual_factors,
        },
        "liquidity_evidence": liquidity_frequency,
        "runtime_markers": detected,
        "summary": {
            "all_expected_runtime_markers_detected": required_markers,
            "registry_consistent": registry_consistent,
            "pick_outcome_readiness_allows_authoritative_fit": readiness_allowed,
            "pick_quality_rows": len(picks),
            "comparable_multi_horizon_groups": comparable_groups,
            "exactly_invariant_multi_horizon_groups": invariant_groups,
            "scenario_formula_replaced": False,
            "market_tier_time_fallback_improved": True,
            "duplicate_pick_premiums_removed": True,
            "downstream_pick_liquidity_optionality_overlap_removed": True,
            "new_liquidity_coefficients_fitted": False,
        },
        "findings": findings,
    }

    (OUT / "future_pick_economics_audit.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )
    print(json.dumps(payload["market_evidence"], indent=2))
    print(json.dumps(payload["liquidity_evidence"], indent=2))
    print(json.dumps(payload["summary"], indent=2))

    if not required_markers:
        missing = [k for k, v in detected.items() if not v]
        raise SystemExit(f"Future-pick runtime governance markers missing: {missing}")
    if not registry_consistent:
        raise SystemExit("PICK-MODEL-001 registry classification is inconsistent with runtime governance")


if __name__ == "__main__":
    main()
