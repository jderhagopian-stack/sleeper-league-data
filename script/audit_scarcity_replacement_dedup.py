#!/usr/bin/env python3
"""Govern market-tier scarcity separately from roster replacement evidence."""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "script" / "build_fsffl_gm_engine.py"
REGISTRY = ROOT / "data" / "model_parameter_registry.json"
OUT = ROOT / "data" / "audit" / "scarcity_replacement_dedup_audit.json"


def section(text: str, start: str, end: str) -> str:
    a = text.index(start)
    b = text.index(end, a)
    return text[a:b]


def main() -> None:
    src = ENGINE.read_text(encoding="utf-8")
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    params = {p["id"]: p for p in registry.get("parameters", [])}
    governed = params.get("MARKET-TIER-SCARCITY-001", {})

    tier = section(src, "def _u_position_tier_features", "def _u_player_distribution_features")
    liquidity = section(src, "def _u_player_liquidity", "def _u_pick_profile")
    profiles = section(src, "def build_strategic_asset_profiles_for_team", "# Backward-compatible alias")
    fragility = section(src, "def build_roster_fragility_index", "def build_pick_quality_model")

    runtime = {
        "market_tier_signal_is_market_derived": (
            'market_dynasty' in tier
            and 'percentile_rank(dyn, peers)' in tier
            and 'scarcity_score' in tier
        ),
        "market_tier_removed_from_liquidity_blend": "scarcity_score" not in liquidity,
        "market_tier_removed_from_future_utility": bool(
            re.search(r'future_utility\s*=\s*clamp\(dist\["upside_optionality"\]', profiles)
        ),
        "market_tier_incremental_premium_zero": "scarcity_premium = 0.0" in profiles,
        "market_tier_removed_from_trade_elasticity": (
            '0.20 * (1.0 - scarcity["scarcity_score"])' not in profiles
            and '(0.40 / 0.55 * 0.75)' in profiles
            and '(0.15 / 0.55 * 0.75)' in profiles
        ),
        "market_tier_retained_as_diagnostic": (
            '"source": "external_market_tier_diagnostic"' in profiles
            and '"incremental_premium_authorized": False' in profiles
        ),
        "replacement_uses_lineup_reoptimization": (
            "remove that player and re-optimize the legal" in fragility
            and "replacement = optimize_lineup(" in fragility
            and '"lineup_value_drop_if_unavailable"' in fragility
        ),
        "replacement_flows_to_team_specific_dependency": (
            'single_drop = safe_float(f.get("lineup_value_drop_if_unavailable"))' in profiles
            and 'dependency = clamp(single_drop / base_lineup * 4.5' in profiles
            and 'resilience = clamp(0.62 * dependency + 0.38 * depth_insurance' in profiles
        ),
    }

    findings = [
        {
            "id": "SCARCITY-MARKET-REUSE-001",
            "severity": "HIGH",
            "status": "STRUCTURALLY_DEDUPLICATED",
            "observation": (
                "The prior GM layer derived a scarcity score from FantasyCalc dynasty values and then "
                "reused that derived market signal in liquidity, future utility, an explicit hold premium, "
                "and trade elasticity. Because the base franchise value is already market-anchored, this "
                "was a repeated positive path for substantially the same evidence."
            ),
            "evidence_tier": "EVIDENCE_BASED_EXTERNAL_ANCHOR for descriptive tier; no validated incremental premium",
            "authoritative_incremental_premium_allowed": False,
        },
        {
            "id": "SCARCITY-REPLACEMENT-001",
            "severity": "INFO",
            "status": "DIRECT_ROSTER_EVIDENCE_PRESERVED",
            "observation": (
                "Roster replacement sensitivity is not inferred from a generic positional constant. The model "
                "removes each optimized starter, re-optimizes the legal lineup from that team's actual roster, "
                "and measures the resulting lineup-value drop. This team-specific signal remains active."
            ),
            "evidence_tier": "EVIDENCE_BASED_EXTERNAL_ANCHOR plus RULE_DEFINED roster/lineup constraints",
            "injury_probability_claim_allowed": False,
            "long_run_war_claim_allowed": False,
        },
        {
            "id": "SCARCITY-REMAINING-PROXY-001",
            "severity": "MEDIUM",
            "status": "QUALIFIED_PROVISIONAL",
            "observation": (
                "Player liquidity, optionality, resilience scaling and hold-premium transforms still contain "
                "hand-set coefficients. This change removes the same-source market-tier duplication only; it "
                "does not claim those remaining coefficients are empirically calibrated."
            ),
            "authoritative_coefficient_claim_allowed": False,
        },
    ]

    report = {
        "schema_version": "1.0",
        "audit_family": "scarcity / replacement-value de-duplication",
        "production_projection_behavior_changed": False,
        "production_gm_behavior_changed": True,
        "policy": {
            "external_market_tier_may_be_diagnostic": True,
            "external_market_tier_requires_incremental_validation_for_second_premium": True,
            "roster_replacement_value_should_use_actual_legal_roster_when_available": True,
            "replacement_sensitivity_is_not_injury_probability": True,
            "structural_dedup_is_not_coefficient_calibration": True,
        },
        "runtime_markers": runtime,
        "registry_consistent": (
            governed.get("authoritative_use") is False
            and governed.get("evidence_tier") == "ASSUMPTION_SENSITIVE_PROVISIONAL"
            and governed.get("status") == "MARKET_TIER_DIAGNOSTIC_ONLY_REPLACEMENT_EVIDENCE_ACTIVE"
        ),
        "findings": findings,
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")

    missing = [k for k, v in runtime.items() if not v]
    if missing:
        raise SystemExit(f"scarcity/replacement governance markers failed: {missing}")
    if not report["registry_consistent"]:
        raise SystemExit("MARKET-TIER-SCARCITY-001 registry governance drifted")
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
