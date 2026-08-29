#!/usr/bin/env python3
"""Fail closed when projection-source governance violates the zero-cost default."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

DEFAULT_REGISTRY = Path("data/model_validation/projection_source_candidate_registry.json")


def audit_registry(registry: dict) -> list[str]:
    errors: list[str] = []
    budget = registry.get("default_external_projection_license_budget_usd")
    if budget != 0:
        errors.append("Default external projection license budget must remain zero unless explicitly re-governed.")

    candidates = registry.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        return errors + ["Candidate registry must contain at least one source."]

    seen = set()
    for candidate in candidates:
        source = candidate.get("source")
        if not source or source in seen:
            errors.append(f"Source names must be present and unique: {source!r}")
            continue
        seen.add(source)

        role = candidate.get("role")
        eligible = bool(candidate.get("production_default_eligible", False))
        cost = candidate.get("access_cost_status", "UNVERIFIED")
        rights = candidate.get("production_rights_status", "UNVERIFIED")

        if eligible and ("PAID" in cost or "RESTRICTED" in cost):
            errors.append(f"{source}: paid/restricted source cannot be default-production eligible under $0 budget.")
        if eligible and ("PENDING" in rights or "UNVERIFIED" in rights or "RESTRICTED" in rights):
            errors.append(f"{source}: intended-use rights are not verified for default production.")
        if role == "NATIVE_MODEL_INPUT" and candidate.get("counts_as_independent_projection_source", False):
            errors.append(f"{source}: underlying native-model data cannot count as an independent projection source.")
        if role in {"OPTIONAL_EXTERNAL_BENCHMARK", "ARCHIVE_CANDIDATE"} and eligible:
            errors.append(f"{source}: benchmark/archive-only source cannot be default-production eligible.")

    required = {"FFToday", "ESPN", "FantasyPros", "nflverse"}
    missing = required - seen
    if missing:
        errors.append(f"Expected governed candidates missing: {sorted(missing)}")
    return errors


def run_self_test() -> None:
    good = {
        "default_external_projection_license_budget_usd": 0,
        "candidates": [
            {"source": "FFToday", "role": "EXTERNAL_PROJECTION_CANDIDATE", "access_cost_status": "PUBLIC_FREE_ACCESS_OBSERVED", "production_rights_status": "PENDING_TERMS_REVIEW", "production_default_eligible": False},
            {"source": "ESPN", "role": "EXTERNAL_PROJECTION_CANDIDATE", "access_cost_status": "FREE_RETRIEVAL_MECHANISM_OBSERVED", "production_rights_status": "PENDING_TERMS_REVIEW", "production_default_eligible": False},
            {"source": "FantasyPros", "role": "OPTIONAL_EXTERNAL_BENCHMARK", "access_cost_status": "RESTRICTED_OR_PAID_FOR_FULL_USE", "production_rights_status": "RESTRICTED_REQUIRES_SEPARATE_REVIEW", "production_default_eligible": False},
            {"source": "nflverse", "role": "NATIVE_MODEL_INPUT", "access_cost_status": "PUBLIC_FREE_DATA_ECOSYSTEM", "production_rights_status": "VERIFY_PER_DATASET_LICENSE", "production_default_eligible": False, "counts_as_independent_projection_source": False},
        ],
    }
    assert not audit_registry(good), audit_registry(good)

    paid_promoted = json.loads(json.dumps(good))
    paid_promoted["candidates"][2]["production_default_eligible"] = True
    assert audit_registry(paid_promoted)

    native_double_counted = json.loads(json.dumps(good))
    native_double_counted["candidates"][3]["counts_as_independent_projection_source"] = True
    assert audit_registry(native_double_counted)

    nonzero_budget = json.loads(json.dumps(good))
    nonzero_budget["default_external_projection_license_budget_usd"] = 1
    assert audit_registry(nonzero_budget)

    print("projection source sustainability self-test: PASS")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        run_self_test()
        return 0

    registry = json.loads(args.registry.read_text())
    errors = audit_registry(registry)
    if errors:
        print("projection source sustainability audit: FAIL")
        for error in errors:
            print(f"- {error}")
        return 1
    print("projection source sustainability audit: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
