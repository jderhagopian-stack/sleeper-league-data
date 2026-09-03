#!/usr/bin/env python3
"""Validate residual evidence contracts and promotion firewalls."""
from __future__ import annotations
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
CONTRACTS=ROOT/"data/model_governance/residual_evidence_contracts.json"
REGISTRY=ROOT/"data/model_parameter_registry.json"

REQUIRED_FAMILIES={
    "PACKAGE-CONCENTRATION-RESIDUAL-001",
    "OPTIONALITY-RESIDUAL-001",
    "LIQUIDITY-RESIDUAL-001",
    "RESILIENCE-RESIDUAL-001",
}

def main():
    c=json.loads(CONTRACTS.read_text(encoding="utf-8"))
    r=json.loads(REGISTRY.read_text(encoding="utf-8"))
    reg={x["id"]:x for x in r.get("parameters") or []}
    rows={x["family_id"]:x for x in c.get("contracts") or []}

    errors=[]
    missing=sorted(REQUIRED_FAMILIES-set(rows))
    if missing:
        errors.append("missing residual evidence contracts: "+", ".join(missing))

    for fid in REQUIRED_FAMILIES:
        if fid not in reg:
            errors.append(f"{fid}: missing from parameter registry")
            continue
        if reg[fid].get("authoritative_use") is not False:
            errors.append(f"{fid}: must remain production-disabled during evidence-contract phase")
        row=rows.get(fid) or {}
        if not row.get("minimum_promotion_evidence"):
            errors.append(f"{fid}: minimum promotion evidence required")
        if not row.get("forbidden_now"):
            errors.append(f"{fid}: current forbidden uses required")
        if not str(row.get("promotion_test") or "").strip():
            errors.append(f"{fid}: promotion test required")

    p=c.get("principles") or {}
    for key in (
        "bounded_prior_may_precede_full_empirical_identification",
        "legacy_formula_is_not_automatically_the_prior",
        "double_count_residualization_required_before_promotion",
        "time_ordered_validation_required_where_historical_targets_exist",
        "uncertainty_and_sensitivity_must_be_reported",
        "challenger_before_production_promotion",
    ):
        if p.get(key) is not True:
            errors.append(f"missing/false contract principle: {key}")

    out={
        "schema_version":"1.0",
        "audit_family":"residual evidence contracts",
        "passed":not errors,
        "production_behavior_changed":False,
        "contract_count":len(rows),
        "errors":errors,
    }
    print(json.dumps(out,indent=2))
    if errors:
        raise SystemExit("residual evidence contract validation failed")

if __name__=="__main__":
    main()
