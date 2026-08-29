#!/usr/bin/env python3
"""Audit external-data commercial-use readiness without touching projection logic."""
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "data" / "external_data_provenance.json"
OUT = ROOT / "data" / "audit" / "external_data_commercial_readiness_audit.json"
OUT.parent.mkdir(parents=True, exist_ok=True)

RESTRICTED = {"LICENSE_REQUIRED", "BLOCKED_PENDING_REVIEW"}


def main() -> int:
    data = json.loads(REGISTRY.read_text(encoding="utf-8"))
    sources = data.get("sources", [])
    by_id = {s.get("id"): s for s in sources}

    findings = []
    for src in sources:
        sid = src.get("id")
        status = src.get("commercial_status")
        authorized = bool(src.get("commercial_production_authorized", False))
        if status in RESTRICTED and authorized:
            findings.append({
                "id": sid,
                "severity": "CRITICAL",
                "status": "INVALID_AUTHORIZATION",
                "observation": "Restricted source is incorrectly marked commercial-production authorized.",
            })
        if status == "COMMERCIAL_ALLOWED_WITH_CONDITIONS" and not authorized:
            findings.append({
                "id": sid,
                "severity": "MEDIUM",
                "status": "PERMISSIVE_SOURCE_NOT_ENABLED",
                "observation": "Terms permit commercial use subject to conditions, but source is not enabled.",
            })

    expected = {
        "FANTASYCALC-MARKET-001": "LICENSE_REQUIRED",
        "SLEEPER-API-001": "LICENSE_REQUIRED",
        "STATSGUY-MARKET-001": "COMMERCIAL_ALLOWED_WITH_CONDITIONS",
        "FSFFL-DERIVED-001": "CONDITIONAL_ON_INPUT_PROVENANCE",
    }
    registry_complete_for_known_sources = all(
        by_id.get(sid, {}).get("commercial_status") == status
        for sid, status in expected.items()
    )

    restricted_active_or_core = [
        {
            "id": s.get("id"),
            "name": s.get("name"),
            "status": s.get("commercial_status"),
            "uses": s.get("known_nonprojection_uses", []),
        }
        for s in sources
        if s.get("commercial_status") == "LICENSE_REQUIRED"
        and s.get("known_nonprojection_uses")
    ]

    commercial_mode = os.getenv("FSFFL_COMMERCIAL_MODE", "0").strip().lower() in {"1", "true", "yes"}
    commercial_gate_pass = not restricted_active_or_core

    payload = {
        "model_version": "FSFFL-External-Data-Commercial-Readiness-1.0",
        "projection_behavior_changed": False,
        "production_model_behavior_changed": False,
        "registry_complete_for_known_nonprojection_sources": registry_complete_for_known_sources,
        "commercial_mode_requested": commercial_mode,
        "commercial_gate_pass": commercial_gate_pass,
        "restricted_active_or_core_dependencies": restricted_active_or_core,
        "commercially_permissive_challenger_available": (
            by_id.get("STATSGUY-MARKET-001", {}).get("commercial_production_authorized") is True
        ),
        "policy": data.get("policy", {}),
        "findings": findings,
        "summary": {
            "fantasycalc_commercial_license_required": by_id.get("FANTASYCALC-MARKET-001", {}).get("commercial_status") == "LICENSE_REQUIRED",
            "sleeper_commercial_license_required": by_id.get("SLEEPER-API-001", {}).get("commercial_status") == "LICENSE_REQUIRED",
            "statsguy_commercially_permissive_with_conditions": by_id.get("STATSGUY-MARKET-001", {}).get("commercial_status") == "COMMERCIAL_ALLOWED_WITH_CONDITIONS",
            "derived_coefficients_require_input_provenance_review": by_id.get("FSFFL-DERIVED-001", {}).get("commercial_status") == "CONDITIONAL_ON_INPUT_PROVENANCE",
            "commercial_deployment_currently_blocked": not commercial_gate_pass,
        },
    }
    OUT.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    if not registry_complete_for_known_sources:
        raise SystemExit("Known external-source provenance registry is incomplete or inconsistent")
    if findings:
        bad = [f for f in findings if f["status"] == "INVALID_AUTHORIZATION"]
        if bad:
            raise SystemExit("Restricted external source incorrectly authorized for commercial production")
    if commercial_mode and not commercial_gate_pass:
        names = ", ".join(x["name"] for x in restricted_active_or_core)
        raise SystemExit(f"Commercial mode blocked by unlicensed dependencies: {names}")

    print(json.dumps(payload["summary"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
