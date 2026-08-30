#!/usr/bin/env python3
"""Canonical roster-resolution provenance and policy governance.

Extracted from historical Counter Market Sweep v1.23. The actual roster
legalization happens upstream in the simulation path; this component verifies
that the runtime resolver version is present and consistent, then publishes the
rule/provenance policy used by current trade applications.

No candidate generation, simulation, valuation, or option-comparison logic lives
here.
"""
from __future__ import annotations

MODEL_VERSION = "FSFFL-Roster-Resolution-Governance-1.0"


def runtime_roster_model(report):
    rows = [report.get("current_offer_evaluation") or {}]
    for key in ("ranked_finalists", "top_5_alternatives", "realistic_counter_alternatives"):
        rows.extend(report.get(key) or [])

    versions = []
    for row in rows:
        version = str(
            ((row.get("simulation") or {}).get("roster_resolution_model_version")) or ""
        ).strip()
        if version and version not in versions:
            versions.append(version)

    if len(versions) > 1:
        raise RuntimeError(
            f"Inconsistent roster-resolution versions in one report: {versions}"
        )
    return versions[0] if versions else None


def apply_to_report(report):
    roster_model = runtime_roster_model(report)
    if not roster_model:
        raise RuntimeError(
            "Roster-aware production report is missing runtime roster_resolution_model_version"
        )

    report.setdefault("policy", {}).update({
        "roster_aware_trade_resolution": True,
        "roster_resolution_model_version": roster_model,
        "roster_resolution_version_source": "simulation.roster_resolution_model_version",
        "roster_resolution_governance_model_version": MODEL_VERSION,
        "post_trade_active_roster_limit_enforced": True,
        "forced_cuts_included_in_lineup_simulation": True,
        "forced_cuts_included_in_strategic_valuation": True,
        "forced_cuts_included_in_buyer_acceptance_analysis": True,
        "taxi_and_reserve_excluded_from_active_roster_count": True,
        "automatic_taxi_or_reserve_reassignment": False,
    })
    report.setdefault("simulation", {})["execution_path"] = (
        str((report.get("simulation") or {}).get("execution_path") or "")
        + "_plus_roster_aware_trade_resolution"
    )
    return roster_model
