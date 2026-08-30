#!/usr/bin/env python3
"""Governance audit for roster legality, automatic cuts, and roster interactions.

This audit does not alter production scoring. It separates rule-defined roster
legality from provisional cut-selection and correlated-roster value heuristics,
and requires incremental validation before those overlays can be treated as
authoritative.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OUT = DATA / "audit"
OUT.mkdir(parents=True, exist_ok=True)
ROSTER = ROOT / "script" / "roster_aware_trade.py"
INTERACTION = ROOT / "script" / "roster_interaction.py"
CUT_SENS = ROOT / "script" / "audit_roster_cut_sensitivity.py"
REGISTRY = DATA / "model_parameter_registry.json"
MODEL_VERSION = "FSFFL-Roster-Interaction-Cut-Governance-1.1"


def load(path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def main():
    roster = ROSTER.read_text(encoding="utf-8")
    interaction = INTERACTION.read_text(encoding="utf-8")
    cut_sens = CUT_SENS.read_text(encoding="utf-8") if CUT_SENS.exists() else ""
    registry = load(REGISTRY, {}) or {}
    params = {str(x.get("id")): x for x in (registry.get("parameters") or [])}

    rule_defined_limit = 'return len(league.get("roster_positions") or [])' in roster
    taxi_reserve_exempt = 'exempt = set(roster.get("taxi") or []) | set(roster.get("reserve") or [])' in roster
    acquired_protected = 'newly_acquired = set(pre_cut_ids) - set(before_ids)' in roster
    baseline_aware = 'effective_limit = max(nominal_limit, active_before)' in roster

    cut_markers = all(x in roster for x in [
        'CUT_SHORTLIST_SIZE = 3',
        'base + .12 * break_glass + .06 * depth + .04 * market_dynasty * liquidity',
        'cost *= 1.75',
        '"franchise_cornerstone": 2.00',
        '"core_high_hold": 1.70',
        '"core_pick": 1.35',
        '"liquid_asset": 1.12',
        '"cut_selection_method": "retention_cost_prescreen_pending_final_plan_optimization"',
    ])
    downstream_cut_sensitivity = all(x in cut_sens for x in [
        'default_matches_best_downstream_plan',
        'default_score_regret',
        'uses_exact_lineup_reoptimization',
        'coefficient_tuning": False',
    ])

    interaction_markers = all(x in interaction for x in [
        'MAX_PAIR_INSURANCE_PCT = 0.12',
        'PAIR_CAPTURE_SCALE = 0.30',
        'MAX_PORTFOLIO_ADJUSTMENT = 600.0',
        'MAX_ACCEPTANCE_FIT_SHIFT = 0.0',
        '0.45 * downside +',
        '0.30 * max(injury_now, availability_uncertainty) +',
        '0.25 * role_uncertainty',
        '"acceptance_fit_shift": 0.0',
        '"acceptance_shift_enabled": False',
    ])

    cut_reg = params.get("ROSTER-CUT-001") or {}
    int_reg = params.get("ROSTER-INTERACTION-001") or {}
    registry_consistent = (
        cut_reg.get("evidence_tier") == "ASSUMPTION_SENSITIVE_PROVISIONAL"
        and cut_reg.get("authoritative_use") is False
        and int_reg.get("evidence_tier") == "ASSUMPTION_SENSITIVE_PROVISIONAL"
        and int_reg.get("authoritative_use") is False
    )

    findings = [
        {
            "id": "ROSTER-LEGALITY-001",
            "severity": "INFO" if rule_defined_limit and taxi_reserve_exempt else "CRITICAL",
            "status": "RULE_DEFINED_CORE" if rule_defined_limit and taxi_reserve_exempt else "RULE_ENCODING_INCONSISTENT",
            "observation": "Active-roster capacity is derived from league roster construction; taxi/reserve exemptions are encoded separately from heuristic cut choice.",
            "evidence_tier": "RULE_DEFINED",
            "authoritative_rule_claim_allowed": bool(rule_defined_limit and taxi_reserve_exempt),
        },
        {
            "id": "ROSTER-CUT-PRESCREEN-001",
            "severity": "HIGH",
            "status": "FINAL_TRACTABLE_SEARCH_WITH_PROVISIONAL_FALLBACK" if cut_markers else "IMPLEMENTATION_DRIFT",
            "observation": "Retention cost is a search accelerator/fallback. Final focal candidates enumerate tractable legal cut plans through downstream Trade Score; large plan spaces and non-focal choices remain provisional.",
            "authoritative_incremental_adjustment_claim_allowed": False,
        },
        {
            "id": "ROSTER-INTERACTION-DOUBLE-COUNT-001",
            "severity": "HIGH",
            "status": "BOUNDED_PROVISIONAL_OVERLAY" if interaction_markers else "IMPLEMENTATION_DRIFT",
            "observation": "Same-team/position insurance is bounded, but its downside/injury/role inputs overlap information already present in projections, GM uncertainty and lineup simulation. Incremental value must be shown by grouped ablation versus the simulation-only baseline.",
            "authoritative_incremental_adjustment_claim_allowed": False,
        },
        {
            "id": "ROSTER-ACCEPTANCE-OVERLAP-001",
            "severity": "HIGH",
            "status": "DUPLICATE_ACCEPTANCE_PATH_DISABLED" if '"acceptance_shift_enabled": False' in interaction else "IMPLEMENTATION_DRIFT",
            "observation": "The former roster-interaction-to-acceptance conversion is disabled. Roster interaction remains a bounded strategic/resilience signal and no longer receives a second uncalibrated path into counterparty plausibility.",
            "authoritative_incremental_adjustment_claim_allowed": False,
        },
    ]

    payload = {
        "model_version": MODEL_VERSION,
        "production_behavior_changed": False,
        "policy": {
            "roster_legality_is_rule_defined": True,
            "cut_selection_is_not_rule_defined": True,
            "cut_prescreen_must_not_have_final_authority_when_tractable": True,
            "roster_interaction_must_show_incremental_value_over_lineup_simulation": True,
            "uncalibrated_duplicate_acceptance_shift_disabled": True,
            "boundedness_is_not_empirical_validation": True,
        },
        "summary": {
            "rule_defined_limit_detected": rule_defined_limit,
            "taxi_reserve_exemption_detected": taxi_reserve_exempt,
            "baseline_aware_incremental_legalization_detected": baseline_aware,
            "newly_acquired_cut_protection_detected": acquired_protected,
            "cut_prescreen_markers_detected": cut_markers,
            "downstream_cut_sensitivity_tool_detected": downstream_cut_sensitivity,
            "roster_interaction_markers_detected": interaction_markers,
            "registry_consistent": registry_consistent,
        },
        "findings": findings,
    }
    (OUT / "roster_interaction_cut_governance_audit.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload["summary"], indent=2))

    if not (rule_defined_limit and taxi_reserve_exempt and baseline_aware and acquired_protected):
        raise SystemExit("Rule-defined/baseline-aware roster legalization markers are incomplete")
    if not cut_markers or not downstream_cut_sensitivity:
        raise SystemExit("Automatic-cut governance/sensitivity coverage is incomplete")
    if not interaction_markers:
        raise SystemExit("Roster-interaction governance markers are incomplete")
    if not registry_consistent:
        raise SystemExit("Roster governance registry classification drifted")


if __name__ == "__main__":
    main()
