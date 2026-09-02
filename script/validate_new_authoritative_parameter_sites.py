#!/usr/bin/env python3
"""Fail CI when a new likely model-parameter site lacks exact provenance registration."""
from __future__ import annotations
import argparse
import json
from pathlib import Path

ALLOWED_EVIDENCE = {
    "RULE_DEFINED",
    "HISTORICALLY_STATISTICALLY_ESTIMATED",
    "EVIDENCE_BASED_EXTERNAL_ANCHOR",
    "SIMULATION_DERIVED_ESTIMATE",
    "REGULARIZED_OR_SHRINKAGE_ESTIMATE",
    "EVIDENCE_SUPPORTED_PROVISIONAL_PRIOR",
    "UNVALIDATED_EXPERT_PRIOR",
    "LEGACY_ARBITRARY_HEURISTIC",
}
ALLOWED_IDENTIFIABILITY = {
    "DIRECTLY_ESTIMABLE",
    "SIMULATION_IDENTIFIABLE",
    "NORMATIVE_STRATEGIC",
    "UNIDENTIFIED_OR_DUPLICATE",
    "RULE_OR_RUNTIME_MECHANIC",
}
ALLOWED_ACTIONS = {
    "KEEP",
    "ELIMINATE",
    "RE_ESTIMATE",
    "SHRINK",
    "REPLACE_WITH_DATA_DERIVED_SCALE",
    "RETAIN_AS_GOVERNED_PRIOR",
    "DIAGNOSTIC_ONLY",
}
REQUIRED = {
    "site_signature",
    "parameter_id",
    "file_path",
    "parameter_name",
    "runtime_authority",
    "evidence_classification",
    "provenance",
    "identifiability_class",
    "recommended_action",
    "uncertainty_status",
    "downstream_consumers",
}

def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))

def likely(inv):
    return {
        row["site_signature"]: row
        for row in inv.get("parameters", [])
        if row.get("screening_class") == "LIKELY_MODEL_PARAMETER"
    }

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--head", required=True)
    ap.add_argument("--registry", required=True)
    args = ap.parse_args()

    base = likely(load(args.base))
    head = likely(load(args.head))
    registry = load(args.registry)
    entries = registry.get("entries") or []
    by_sig = {
        str(x.get("site_signature")): x
        for x in entries
        if x.get("site_signature")
    }

    errors = []
    for entry in entries:
        missing = REQUIRED - set(entry)
        if missing:
            errors.append(f"registry entry {entry.get('parameter_id')} missing {sorted(missing)}")
            continue
        if entry["evidence_classification"] not in ALLOWED_EVIDENCE:
            errors.append(f"{entry['parameter_id']}: invalid evidence class")
        if entry["identifiability_class"] not in ALLOWED_IDENTIFIABILITY:
            errors.append(f"{entry['parameter_id']}: invalid identifiability class")
        if entry["recommended_action"] not in ALLOWED_ACTIONS:
            errors.append(f"{entry['parameter_id']}: invalid recommended action")
        if not entry["downstream_consumers"]:
            errors.append(f"{entry['parameter_id']}: downstream consumers required")

    new = [row for sig, row in head.items() if sig not in base]
    unregistered = [row for row in new if row["site_signature"] not in by_sig]
    for row in unregistered:
        errors.append(
            "new likely model parameter lacks exact provenance registration: "
            f"{row['file_path']}:{row['line']} {row['parameter_name']}="
            f"{row['current_value_or_function']!r} signature={row['site_signature']}"
        )

    stale = [entry for sig, entry in by_sig.items() if sig not in head]
    report = {
        "base_likely_model_parameter_sites": len(base),
        "head_likely_model_parameter_sites": len(head),
        "new_likely_model_parameter_sites": len(new),
        "registered_new_sites": len(new) - len(unregistered),
        "unregistered_new_sites": len(unregistered),
        "stale_registry_entries": len(stale),
        "passed": not errors,
    }
    print(json.dumps(report, indent=2))
    if errors:
        raise SystemExit(
            "new authoritative parameter site gate failed:\n - "
            + "\n - ".join(errors)
        )

if __name__ == "__main__":
    main()
