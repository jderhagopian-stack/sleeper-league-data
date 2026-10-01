#!/usr/bin/env python3
"""Diagnose why historical trades remain outside primary pick calibration.

Research-only reporting helper. Reads generated recoverability output and emits a
compact reason/season/topology breakdown so recoverable gaps can be separated from
true historical evidence limits. It does not assign values or alter calibration.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "data" / "audit" / "historical_pick_coordinate_recoverability.json"
OUT = ROOT / "data" / "audit" / "historical_pick_recovery_gap_analysis.json"


def main() -> None:
    payload = json.loads(PATH.read_text(encoding="utf-8"))
    records = payload.get("records") or []
    non_primary = [
        r for r in records
        if r.get("recovery_class") not in {
            "RECOVERABLE_PLAYER_ONLY",
            "RECOVERABLE_WITH_HISTORICAL_PICK_COORDINATE",
            "EXCLUDED_2022_STARTUP_NONCOMPARABLE",
        }
    ]

    by_class = Counter(str(r.get("recovery_class")) for r in non_primary)
    by_season = Counter(str(r.get("season")) for r in non_primary)
    by_family = Counter(str(r.get("asset_family")) for r in non_primary)
    by_topology = Counter(str(r.get("topology")) for r in non_primary)

    class_by_season = defaultdict(Counter)
    pick_basis = Counter()
    pick_quality = Counter()
    pick_suitability = Counter()
    no_prior_draft_evidence = 0
    exact_slot_unknown = 0

    for r in non_primary:
        cls = str(r.get("recovery_class"))
        season = str(r.get("season"))
        class_by_season[cls][season] += 1
        for c in r.get("pick_coordinates") or []:
            pick_quality[str(c.get("evidence_quality"))] += 1
            pick_suitability[str(c.get("calibration_suitability"))] += 1
            basis = c.get("evidence_basis")
            if isinstance(basis, list):
                for item in basis:
                    pick_basis[str(item)] += 1
            elif basis is not None:
                pick_basis[str(basis)] += 1
            provenance = c.get("provenance") or {}
            prior_draft_seasons = provenance.get("draft_evidence_seasons_available_before_trade") or []
            if len(prior_draft_seasons) == 0:
                no_prior_draft_evidence += 1
            if not c.get("exact_slot_known_at_trade_time"):
                exact_slot_unknown += 1

    result = {
        "research_only": True,
        "production_authority": False,
        "non_primary_non_startup_count": len(non_primary),
        "by_recovery_class": dict(by_class),
        "by_season": dict(by_season),
        "by_asset_family": dict(by_family),
        "by_topology": dict(by_topology),
        "recovery_class_by_season": {k: dict(v) for k, v in class_by_season.items()},
        "pick_evidence_quality_counts": dict(pick_quality),
        "pick_calibration_suitability_counts": dict(pick_suitability),
        "pick_evidence_basis_counts": dict(pick_basis),
        "pick_assets_with_zero_prior_completed_draft_evidence": no_prior_draft_evidence,
        "pick_assets_without_exact_slot_knowledge": exact_slot_unknown,
        "interpretation_guardrail": (
            "Counts identify evidence gaps only. They do not justify inventing a historical value, "
            "using future information, or promoting any coefficient to production."
        ),
    }
    OUT.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
