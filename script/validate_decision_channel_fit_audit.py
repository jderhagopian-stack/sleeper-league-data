#!/usr/bin/env python3
"""Validate that the four-channel architecture can account for the full governed factor inventory.

This gate does not assert that four is metaphysically optimal. It asserts that,
given the CURRENT material-factor inventory, no legitimate economic factor
requires a fifth additive utility channel once transforms, meta-layers,
de-duplication, and horizon distinctions are handled explicitly.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
AUDIT=ROOT/"data/model_governance/decision_channel_fit_audit.json"
REG=ROOT/"data/model_parameter_registry.json"

VALUE_ROLES={
    "CURRENT_TEAM_IMPACT",
    "FUTURE_ASSET_VALUE",
    "LIQUIDITY_REVERSIBILITY",
    "ROSTER_RESILIENCE",
}
ALLOWED_NONVALUE={
    "PACKAGE_CONCENTRATION_TRANSFORM",
    "META_STRUCTURAL_RULES",
    "META_UNCERTAINTY_CONFIDENCE",
    "META_OBJECTIVE_WEIGHTS",
    "META_DECISION_POLICY",
    "META_RESEARCH",
    "NON_ECONOMIC_SEARCH",
    "NON_ECONOMIC_NEGOTIATION",
    "DEPRECATED_DUPLICATIVE",
    "SPLIT_REQUIRED",
}

def main():
    a=json.loads(AUDIT.read_text(encoding="utf-8"))
    r=json.loads(REG.read_text(encoding="utf-8"))
    reg_ids={x["id"] for x in r.get("parameters") or []}
    fmap=a.get("registry_family_map") or {}
    errors=[]

    missing=sorted(reg_ids-set(fmap))
    extra=sorted(set(fmap)-reg_ids)
    if missing:
        errors.append("unmapped registry families: "+", ".join(missing))
    if extra:
        errors.append("audit maps unknown registry families: "+", ".join(extra))

    allowed=VALUE_ROLES|ALLOWED_NONVALUE
    for fid,roles in fmap.items():
        if not roles:
            errors.append(f"{fid}: no roles assigned")
        for role in roles:
            if role not in allowed:
                errors.append(f"{fid}: unknown role {role}")

    factors=a.get("granular_factor_map") or []
    if len(factors) < 30:
        errors.append("granular factor inventory unexpectedly small")
    for row in factors:
        role=row.get("role")
        if role not in allowed:
            errors.append(f"factor {row.get('factor')}: unknown role {role}")

    conclusion=a.get("conclusion") or {}
    if conclusion.get("new_economic_channel_required") is not False:
        errors.append("audit unexpectedly requires a fifth economic channel")
    if conclusion.get("recommended_final_additive_channel_count") != 4:
        errors.append("recommended additive channel count must be four")
    if conclusion.get("important_non_value_dimension") != "META_UNCERTAINTY_CONFIDENCE":
        errors.append("uncertainty/confidence must remain explicit as non-value dimension")
    if conclusion.get("important_transform") != "PACKAGE_CONCENTRATION_TRANSFORM":
        errors.append("package concentration must remain a transform, not a fifth channel")

    candidates=a.get("candidate_fifth_channels_examined") or []
    names={x.get("candidate") for x in candidates}
    required_candidates={
        "SCARCITY",
        "OPTIONALITY",
        "RISK_OR_UNCERTAINTY",
        "ROSTER_NEED",
        "COUNTERPARTY_OR_MARKET_FEASIBILITY",
        "STRATEGIC_FLEXIBILITY",
        "OPPONENT_STRENGTH_EXTERNALITY",
    }
    if not required_candidates.issubset(names):
        errors.append("candidate fifth-channel challenge set incomplete")

    mixed=a.get("mixed_family_decomposition") or {}
    for fid in ("GM22-CONFIG-001","MARKET-TIER-SCARCITY-001"):
        if fid not in mixed:
            errors.append(f"{fid}: mixed family decomposition required")

    hierarchy=json.loads((ROOT/"data/model_governance/decision_channel_hierarchy.json").read_text(encoding="utf-8"))
    hid=[x.get("id") for x in hierarchy.get("authoritative_outputs") or []]
    if hid != [
        "CURRENT_TEAM_IMPACT",
        "FUTURE_ASSET_VALUE",
        "LIQUIDITY_REVERSIBILITY",
        "ROSTER_RESILIENCE",
    ]:
        errors.append("channel-fit audit disagrees with governed hierarchy")

    out={
        "schema_version":"1.0",
        "audit_family":"four-channel fit validation",
        "passed":not errors,
        "production_behavior_changed":False,
        "registry_family_count":len(reg_ids),
        "mapped_registry_family_count":len(fmap),
        "granular_factor_count":len(factors),
        "candidate_fifth_channels_tested":len(candidates),
        "new_economic_channel_required":conclusion.get("new_economic_channel_required"),
        "errors":errors,
    }
    print(json.dumps(out,indent=2))
    if errors:
        raise SystemExit("four-channel fit validation failed")

if __name__=="__main__":
    main()
