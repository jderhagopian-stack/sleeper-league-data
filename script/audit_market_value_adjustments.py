#!/usr/bin/env python3
"""Audit the FSFFL market/value adjustment layer and governed de-duplication."""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "script" / "build_fsffl_gm_engine.py"
OVERRIDES = ROOT / "script" / "nonprojection_high_priority_overrides.py"
REGISTRY = ROOT / "data" / "model_parameter_registry.json"
OUT = ROOT / "data" / "audit" / "market_value_adjustments_audit.json"


def main() -> None:
    text = ENGINE.read_text(encoding="utf-8")
    override_text = OVERRIDES.read_text(encoding="utf-8")
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    params = {p["id"]: p for p in registry.get("parameters", [])}

    runtime = {
        "external_market_anchor_present": "FantasyCalc current values" in text,
        "rank_curve_removed": all(marker not in text for marker in (
            "rank <= 24", "mult = 1.04", "rank <= 60", "mult = 1.02",
            "rank > 180", "mult = 0.90", "rank > 120", "mult = 0.95",
        )) and "mult = 1.0" in text,
        "market_trend_is_same_source_signal": ('"trend_30_day": entry.get("trend30Day")' in text and "def market_momentum_adjustment" in text),
        "native_market_trend_repricing_path_present": ("mom_adj, mom_meta = market_momentum_adjustment(asset)" in text and "base * mult * (1.0 + perf_adj + football_adj)" in text),
        "governed_market_trend_incremental_value_removed": ("diagnostic_only_market_momentum" in override_text and 'meta["incremental_adjustment_authorized"] = False' in override_text and "return 0.0, meta" in override_text),
        "market_trend_counterfactual_preserved_as_diagnostic": ('meta["proposed_incremental_adjustment_diagnostic"]' in override_text),
        "performance_overlay_present": "performance_adjustment(asset, performance, baselines)" in text,
        "usage_overlay_present": "usage_adjustment(asset, usage, snaps)" in text,
        "injury_overlay_present": "injury_adjustment(asset)" in text,
        "manual_overlay_present": "manual_intelligence_adjustment(asset, manual)" in text,
        "football_total_clamp_present": bool(re.search(r"total\s*=\s*clamp\(inj_adj \+ use_adj \+ mom_adj \+ man_adj,\s*-0\.22,\s*0\.22\)", text)),
    }

    required_registry = {"CONSOLIDATION-PREMIUM-001", "GM22-CONFIG-001", "MARKET-MOMENTUM-001"}
    missing_registry = sorted(required_registry - set(params))
    market_momentum = params.get("MARKET-MOMENTUM-001", {})
    consolidation = params.get("CONSOLIDATION-PREMIUM-001", {})
    gm_config = params.get("GM22-CONFIG-001", {})

    findings = [
        {
            "id": "MARKET-ANCHOR-001",
            "status": "RESEARCH_EXTERNAL_ANCHOR_REPLACEABLE_FOR_COMMERCIAL_USE",
            "evidence_tier": "EVIDENCE_BASED_EXTERNAL_ANCHOR",
            "observation": "FantasyCalc current dynasty value is the current research/private-runtime market anchor, but its current terms restrict commercial use without express written permission, so it is not authorized as an irreplaceable commercial production dependency.",
            "authoritative_incremental_adjustment_claim_allowed": True,
            "commercial_production_dependency_authorized_without_separate_permission": False,
        },
        {
            "id": "MARKET-RANK-CURVE-001",
            "status": "STRUCTURALLY_DEDUPLICATED",
            "evidence_tier": consolidation.get("evidence_tier"),
            "observation": "The prior same-source FantasyCalc rank-tier repricing remains inactive.",
            "authoritative_incremental_adjustment_claim_allowed": False,
        },
        {
            "id": "MARKET-MOMENTUM-DOUBLE-COUNT-001",
            "status": "STRUCTURALLY_DEDUPLICATED_DIAGNOSTIC_ONLY",
            "evidence_tier": market_momentum.get("evidence_tier"),
            "observation": "FantasyCalc trend30Day comes from the same market source whose current value is already the anchor. The governed runtime retains the trend and former proposed adjustment as diagnostics but gives momentum zero incremental valuation weight until temporal residual evidence supports it.",
            "authoritative_incremental_adjustment_claim_allowed": False,
        },
        {
            "id": "MARKET-OVERLAY-CORRELATION-001",
            "status": "DUPLICATIVE_REPRICING_REMOVED_DIAGNOSTICS_PRESERVED",
            "evidence_tier": gm_config.get("evidence_tier"),
            "observation": "Recent performance, usage/snap trend, injury status and manual news remain available as football context, but their former hand-set percentage adjustments no longer reprice the current dynasty-market anchor. Current production impact belongs in projections/Simulator; market residual repricing can return only with held-out incremental evidence.",
            "authoritative_incremental_adjustment_claim_allowed": False,
        },
    ]

    report = {
        "schema_version": "1.2",
        "audit_family": "market/value adjustments",
        "production_behavior_changed": False,
        "production_state_changed_by_governed_fix": True,
        "policy": {
            "current_market_anchor_is_not_empirical_validation_of_overlays": True,
            "external_market_source_must_be_replaceable": True,
            "fantasycalc_permitted_as_research_private_runtime_anchor_subject_to_terms": True,
            "fantasycalc_required_commercial_dependency_authorized_without_separate_permission": False,
            "commercial_market_source_requires_appropriate_usage_rights": True,
            "commercial_source_swap_must_not_require_decision_architecture_change": True,
            "same_source_rank_repricing_is_removed": True,
            "same_source_market_trend_requires_incremental_validation": True,
            "same_source_market_trend_incremental_value_is_removed": True,
            "market_trend_remains_available_as_diagnostic": True,
            "market_trend_reintroduction_requires_temporal_holdout_improvement": True,
            "bounded_adjustment_is_not_evidence_of_correctness": True,
            "correlated_overlay_families_require_ablation_before_reintroduction": True,
            "performance_usage_injury_news_incremental_market_repricing_removed": True,
            "promotion_requires_temporal_holdout_improvement": True,
            "new_coefficient_introduced": False,
        },
        "runtime_markers": runtime,
        "registry_missing_required_families": missing_registry,
        "findings": findings,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    if missing_registry:
        raise SystemExit(f"market audit registry coverage missing: {missing_registry}")
    if not all(runtime.values()):
        raise SystemExit(f"market/value runtime changed; re-audit required: {[k for k,v in runtime.items() if not v]}")
    if market_momentum.get("authoritative_use") is not False:
        raise SystemExit("market momentum cannot be authoritative before incremental validation")
    if consolidation.get("authoritative_use") is not False:
        raise SystemExit("consolidation rank curve cannot be authoritative before residual validation")
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
