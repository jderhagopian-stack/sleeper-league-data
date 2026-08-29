#!/usr/bin/env python3
"""Audit the FSFFL market/value adjustment layer without changing outputs."""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "script" / "build_fsffl_gm_engine.py"
REGISTRY = ROOT / "data" / "model_parameter_registry.json"
OUT = ROOT / "data" / "audit" / "market_value_adjustments_audit.json"


def main() -> None:
    text = ENGINE.read_text(encoding="utf-8")
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    params = {p["id"]: p for p in registry.get("parameters", [])}

    runtime = {
        "external_market_anchor_present": "FantasyCalc current values" in text,
        "rank_curve_removed": all(
            marker not in text for marker in (
                "rank <= 24", "mult = 1.04", "rank <= 60", "mult = 1.02",
                "rank > 180", "mult = 0.90", "rank > 120", "mult = 0.95",
            )
        ) and "mult = 1.0" in text,
        "market_trend_is_same_source_signal": (
            '"trend_30_day": entry.get("trend30Day")' in text
            and "def market_momentum_adjustment" in text
        ),
        "market_trend_reapplied_to_anchor": (
            "mom_adj, mom_meta = market_momentum_adjustment(asset)" in text
            and "base * mult * (1.0 + perf_adj + football_adj)" in text
        ),
        "performance_overlay_present": "performance_adjustment(asset, performance, baselines)" in text,
        "usage_overlay_present": "usage_adjustment(asset, usage, snaps)" in text,
        "injury_overlay_present": "injury_adjustment(asset)" in text,
        "manual_overlay_present": "manual_intelligence_adjustment(asset, manual)" in text,
        "football_total_clamp_present": bool(
            re.search(r"total\s*=\s*clamp\(inj_adj \+ use_adj \+ mom_adj \+ man_adj,\s*-0\.22,\s*0\.22\)", text)
        ),
    }

    required_registry = {
        "CONSOLIDATION-PREMIUM-001",
        "GM22-CONFIG-001",
        "MARKET-MOMENTUM-001",
    }
    missing_registry = sorted(required_registry - set(params))

    market_momentum = params.get("MARKET-MOMENTUM-001", {})
    consolidation = params.get("CONSOLIDATION-PREMIUM-001", {})
    gm_config = params.get("GM22-CONFIG-001", {})

    findings = [
        {
            "id": "MARKET-ANCHOR-001",
            "status": "EVIDENCE_BASED_EXTERNAL_ANCHOR_ACTIVE",
            "evidence_tier": "EVIDENCE_BASED_EXTERNAL_ANCHOR",
            "observation": (
                "FantasyCalc current dynasty value, requested using the synced league format, is the runtime market anchor. "
                "It is observable market evidence, not an FSFFL-specific learned coefficient."
            ),
            "authoritative_incremental_adjustment_claim_allowed": True,
        },
        {
            "id": "MARKET-RANK-CURVE-001",
            "status": "STRUCTURALLY_DEDUPLICATED",
            "evidence_tier": consolidation.get("evidence_tier"),
            "observation": (
                "The prior rank-tier curve repriced the FantasyCalc market anchor using FantasyCalc's own "
                "overall rank. That same-source transformation has been removed. Rank can remain descriptive, "
                "but no second premium or discount is applied unless future residual out-of-sample evidence "
                "demonstrates a stable league-specific effect beyond the market anchor."
            ),
            "authoritative_incremental_adjustment_claim_allowed": False,
        },
        {
            "id": "MARKET-MOMENTUM-DOUBLE-COUNT-001",
            "status": "POTENTIAL_SAME_SOURCE_SIGNAL_REUSE",
            "evidence_tier": market_momentum.get("evidence_tier"),
            "observation": (
                "FantasyCalc trend30Day is derived from the same market source whose current value is already the anchor, "
                "then is normalized and reapplied through football_intelligence_adjustment. The current price can already "
                "embed the information that caused the move, so the trend overlay requires an incremental time-ordered "
                "test beyond current market value alone before it can be treated as evidence-improving."
            ),
            "authoritative_incremental_adjustment_claim_allowed": False,
        },
        {
            "id": "MARKET-OVERLAY-CORRELATION-001",
            "status": "ABLATION_REQUIRED",
            "evidence_tier": gm_config.get("evidence_tier"),
            "observation": (
                "Recent performance, usage/snap trend, injury status, manual news, and market momentum can describe "
                "overlapping information. A total clamp bounds leverage but does not establish independent predictive value. "
                "Family-by-family and grouped ablations are required before promotion."
            ),
            "authoritative_incremental_adjustment_claim_allowed": False,
        },
    ]

    report = {
        "schema_version": "1.0",
        "audit_family": "market/value adjustments",
        "production_behavior_changed": True,
        "policy": {
            "current_market_anchor_is_not_empirical_validation_of_overlays": True,
            "same_source_rank_repricing_is_removed": True,
            "same_source_market_trend_requires_incremental_validation": True,
            "bounded_adjustment_is_not_evidence_of_correctness": True,
            "correlated_overlay_families_require_ablation": True,
            "promotion_requires_temporal_holdout_improvement": True,
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
        missing = [k for k, v in runtime.items() if not v]
        raise SystemExit(f"market/value runtime changed; re-audit required: {missing}")
    if market_momentum.get("authoritative_use") is not False:
        raise SystemExit("market momentum cannot be authoritative before incremental validation")
    if consolidation.get("authoritative_use") is not False:
        raise SystemExit("consolidation rank curve cannot be authoritative before residual validation")

    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
