#!/usr/bin/env python3
"""Validate simplified FSFFL decision-channel hierarchy.

This is a governance/architecture gate. It prevents detailed diagnostics from
quietly becoming new utility knobs and preserves the "many inputs, few outputs"
design.
"""
from __future__ import annotations
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
H=ROOT/"data/model_governance/decision_channel_hierarchy.json"

def main():
    h=json.loads(H.read_text(encoding="utf-8"))
    errors=[]
    outs=h.get("authoritative_outputs") or []
    ids=[x.get("id") for x in outs]
    expected=[
        "CURRENT_TEAM_IMPACT",
        "FUTURE_ASSET_VALUE",
        "LIQUIDITY_REVERSIBILITY",
        "ROSTER_RESILIENCE",
    ]
    if ids!=expected:
        errors.append(f"authoritative outputs must be exactly {expected}, got {ids}")

    hard=h.get("hard_rules") or {}
    if hard.get("maximum_final_additive_utility_channels")!=4:
        errors.append("final additive utility must remain limited to four channels")
    for key in (
        "package_concentration_is_not_an_additive_channel",
        "behavior_is_not_economic_value",
        "same_signal_cannot_feed_multiple_authoritative_channels_without_explicit_residualization",
        "legacy_broad_composite_scores_cannot_regain_final_authority",
    ):
        if hard.get(key) is not True:
            errors.append(f"missing/false hard rule: {key}")

    pkg=h.get("package_substitution_rule") or {}
    if pkg.get("residual_family")!="PACKAGE-CONCENTRATION-RESIDUAL-001":
        errors.append("package concentration must map to residual package family")
    if pkg.get("must_replace_not_stack") is not True:
        errors.append("package concentration must replace additivity, never stack")

    layers=h.get("non_economic_layers") or []
    for row in layers:
        if row.get("economic_authority") is not False:
            errors.append(f"{row.get('id')}: non-economic layer cannot have economic authority")

    # Structural runtime guardrails against channel proliferation.
    util=(ROOT/"script/decision_utility.py").read_text(encoding="utf-8")
    if 'required = ("current", "future", "liquidity", "resilience")' not in util:
        errors.append("Shared Decision Utility four-channel contract changed")
    if '"optionality_incremental_value_authorized": False' not in util:
        errors.append("optionality unexpectedly gained direct authority")
    if '"composite_strategic_and_break_glass_incremental_weight": 0.0' not in util:
        errors.append("legacy composite strategic/break-glass score unexpectedly regained authority")

    out={
        "schema_version":"1.0",
        "audit_family":"decision channel hierarchy",
        "passed":not errors,
        "production_behavior_changed":False,
        "authoritative_output_count":len(outs),
        "package_concentration_additive_channel":False,
        "errors":errors,
    }
    print(json.dumps(out,indent=2))
    if errors:
        raise SystemExit("decision channel hierarchy validation failed")

if __name__=="__main__":
    main()
