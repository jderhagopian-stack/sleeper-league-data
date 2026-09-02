#!/usr/bin/env python3
from __future__ import annotations
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
POLICY=ROOT/"data/model_governance/coefficient_provenance_policy.json"
INVENTORY=ROOT/"data/audit/authoritative_parameter_inventory.json"

EXPECTED=[
 "RULE_DEFINED","HISTORICALLY_STATISTICALLY_ESTIMATED","EVIDENCE_BASED_EXTERNAL_ANCHOR",
 "SIMULATION_DERIVED_ESTIMATE","REGULARIZED_OR_SHRINKAGE_ESTIMATE",
 "EVIDENCE_SUPPORTED_PROVISIONAL_PRIOR","UNVALIDATED_EXPERT_PRIOR","LEGACY_ARBITRARY_HEURISTIC"
]
REQUIRED={
 "parameter_id","module","file_path","line","parameter_name","current_value_or_function",
 "runtime_authority","downstream_consumers","evidence_classification","provenance_source",
 "originally_hand_set","empirically_validated","simulation_derived","externally_anchored",
 "duplicated_elsewhere","uncertainty_status","sensitivity_level","estimated_decision_impact",
 "replacement_feasibility","identifiability_class","recommended_action",
 "evidence_needed_for_further_promotion","review_status"
}

def fail(msg): raise SystemExit("coefficient provenance validation failed: "+msg)

def main():
    policy=json.loads(POLICY.read_text())
    if policy.get("production_behavior_changed") is not False: fail("PR A must not change production behavior")
    if policy.get("evidence_hierarchy")!=EXPECTED: fail("evidence hierarchy mismatch")
    if policy["promotion_policy"].get("legacy_arbitrary_heuristic_has_no_special_incumbency_advantage") is not True:
        fail("arbitrary-incumbent promotion rule missing")
    if policy["calibration_policy"].get("hurts_so_good_is_regression_fixture_not_ground_truth") is not True:
        fail("Hurts So Good ground-truth guard missing")

    inv=json.loads(INVENTORY.read_text())
    if inv.get("authority")!="AUDIT_ONLY_NON_AUTHORITATIVE": fail("inventory must be non-authoritative")
    if inv.get("policy",{}).get("inventory_confers_promotion_authority") is not False:
        fail("inventory cannot confer promotion authority")
    rows=inv.get("parameters") or []
    if not rows: fail("candidate inventory is empty")
    if inv["summary"]["candidate_parameter_sites"]!=len(rows): fail("candidate count mismatch")
    if inv["summary"]["parse_errors"]!=0: fail("parse errors on governed production paths")
    ids=set()
    for row in rows:
        missing=REQUIRED-set(row)
        if missing: fail(f"{row.get('parameter_id')} missing {sorted(missing)}")
        if row["parameter_id"] in ids: fail("duplicate parameter id "+row["parameter_id"])
        ids.add(row["parameter_id"])
        if row["review_status"]!="CANDIDATE_NOT_YET_ADJUDICATED":
            fail("PR A scanner must not silently adjudicate provenance")
        if row["runtime_authority"]!="REVIEW_REQUIRED":
            fail("PR A scanner must leave authority for review")
    print(json.dumps(inv["summary"],indent=2))

if __name__=="__main__": main()
