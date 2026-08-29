#!/usr/bin/env python3
"""Static governance audit of the production trade-decision path.

The goal is not to certify model quality from source-code shape.  It records
which path is production-authoritative, which heuristic gates have decision
leverage, and where correlated value families are reused in final ranking.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "script"
OUT = ROOT / "data" / "audit"
OUT.mkdir(parents=True, exist_ok=True)

MODEL_VERSION = "FSFFL-Decision-Path-Integrity-Audit-1.1"


def text(name: str) -> str:
    return (SCRIPT / name).read_text(encoding="utf-8")


def main():
    report = text("run_trade_report.py")
    v30 = text("run_trade_market_sweep_v30.py")
    v29 = text("run_trade_market_sweep_v29.py")
    v20 = text("run_trade_market_sweep_v20.py")
    v13 = text("run_trade_market_sweep_v13.py")
    v16 = text("run_trade_market_sweep_v16.py")
    state = text("decision_lab_state_aware.py")
    behavior_prod_test = (ROOT / ".github" / "workflows" / "test-behavioral-intelligence-v3-production.yml").read_text(encoding="utf-8")

    production_roster_aware = (
        "run_trade_market_sweep_v30.py" in report
        and "run_trade_market_sweep_v29.py" in v30
        and "legalize_trade_rosters" in v13
        and "forced_cut" in v13
    )
    runtime_version_single_source = (
        "simulation.roster_resolution_model_version" in v29
        and "ROSTER_MODEL=" not in v29.replace("runtime_roster_model", "")
    )

    acceptance_declared_heuristic = (
        "not a calibrated" in v16.lower()
        or "not a calibrated\nprobability" in v16.lower()
        or "heuristic_acceptance_fit_not_probability" in v16
    )
    acceptance_has_authoritative_gate = (
        'in {"HIGH", "MEDIUM"}' in v16
        and "recommended_next_action" in v16
        and "realistic" in v16
    )

    # The final score must use primitive channels only. Composite strategic
    # and break-glass summaries remain available upstream for explanation but
    # receive no incremental final-score weight.
    final_overlap_tokens = {
        "primitive_dynasty_delta_in_final_score": "market_dynasty_delta" in v20 and "future_block" in v20,
        "primitive_liquidity_delta_in_final_score": "liquidity_value_delta" in v20 and "liquidity_block" in v20,
        "primitive_optionality_delta_in_final_score": "optionality_value_delta" in v20 and "future_block" in v20,
        "primitive_resilience_delta_in_final_score": "resilience_value_delta" in v20 and "resilience_block" in v20,
        "strategic_composite_built_upstream_for_diagnostics": "strategic_value_delta" in state and "strategic_score" in state,
        "break_glass_built_upstream_for_diagnostics": "break_glass_delta" in state,
    }
    final_composite_overlap = (
        'strategic = sf(s.get("strategic_value_delta"))' in v20
        or 'break_glass = sf(s.get("break_glass_delta"))' in v20
        or '0.30 * break_glass' in v20
        or '0.15 * strategic' in v20
    )
    primitive_final_score = all(final_overlap_tokens[k] for k in (
        "primitive_dynasty_delta_in_final_score",
        "primitive_liquidity_delta_in_final_score",
        "primitive_optionality_delta_in_final_score",
        "primitive_resilience_delta_in_final_score",
    )) and not final_composite_overlap

    behavior_oos_predictive_test = any(
        token in behavior_prod_test.lower()
        for token in (
            "holdout acceptance", "held-out acceptance", "future acceptance",
            "out-of-sample acceptance", "predictive log loss", "brier score",
        )
    )

    post_overlay_ranking_refresh = (
        "refresh_negotiation_ranking" in v30
        and "recompute_negotiation_ranking" in v30
        and "recommended_next_action_empirically_authoritative" in v30
    )

    findings = [
        {
            "id": "DECISION-PATH-ROSTER-001",
            "severity": "INFO" if production_roster_aware else "CRITICAL",
            "status": "PRODUCTION_PATH_ROSTER_AWARE" if production_roster_aware else "PRODUCTION_PATH_INTEGRITY_FAILURE",
            "observation": (
                "The production report chain reaches the roster-aware v1.3 simulation path, which legalizes post-trade active rosters before simulation and carries forced cuts into effective actions."
                if production_roster_aware else
                "The production report chain could not be statically verified as roster-aware."
            ),
            "software_invariant": production_roster_aware,
        },
        {
            "id": "DECISION-PATH-VERSION-001",
            "severity": "INFO" if runtime_version_single_source else "HIGH",
            "status": "SINGLE_RUNTIME_SOURCE" if runtime_version_single_source else "DUPLICATE_VERSION_SOURCE",
            "observation": "Production policy metadata must report the resolver version emitted by the simulation that actually ran; no second hard-coded resolver version is authoritative.",
            "software_invariant": runtime_version_single_source,
        },
        {
            "id": "POST-RANK-OVERLAY-001",
            "severity": "INFO" if post_overlay_ranking_refresh else "CRITICAL",
            "status": "RANKING_REFRESH_AND_AUTHORITY_QUALIFICATION_PRESENT" if post_overlay_ranking_refresh else "POST_RANKING_MUTATION_NOT_RECONCILED",
            "observation": "Any wrapper that changes post-simulation score or acceptance fit after candidate selection must refresh exposed rankings and qualify the inherited action when the complete candidate universe is unavailable.",
            "software_invariant": post_overlay_ranking_refresh,
        },
        {
            "id": "ACCEPTANCE-GATE-001",
            "severity": "HIGH",
            "status": "PROVISIONAL_HIGH_LEVERAGE_HEURISTIC" if acceptance_has_authoritative_gate else "NO_AUTHORITATIVE_HEURISTIC_GATE_DETECTED",
            "observation": (
                "Human acceptance is explicitly described as heuristic rather than probabilistic, but MEDIUM/HIGH hand-set fit bands still gate realistic Top-5 inclusion and can drive the recommended negotiation action. "
                "Until held-out choice/acceptance prediction is available, these thresholds must remain provisional and sensitivity-qualified."
            ),
            "declared_not_probability": acceptance_declared_heuristic,
            "has_authoritative_decision_leverage": acceptance_has_authoritative_gate,
            "authoritative_empirical_claim_allowed": False,
        },
        {
            "id": "FINAL-SCORE-OVERLAP-001",
            "severity": "HIGH",
            "status": "UNRESOLVED_OVERLAP" if final_composite_overlap else "STRUCTURALLY_DEDUPLICATED",
            "observation": (
                "The state-aware final score now uses primitive dynasty, optionality, liquidity and direct roster-replacement resilience channels. "
                "Strategic and break-glass composites remain available for explanation but no longer receive separate final-score weight."
            ),
            "detected_components": final_overlap_tokens,
            "authoritative_empirical_claim_allowed": False,
        },
        {
            "id": "BEHAVIOR-OOS-001",
            "severity": "HIGH",
            "status": "PREDICTIVE_HOLDOUT_PRESENT" if behavior_oos_predictive_test else "STRUCTURAL_VALIDATION_ONLY",
            "observation": (
                "Behavioral Intelligence 3 has strong leakage/boundedness/sample-confidence tests, but its production workflow does not demonstrate held-out prediction of future manager acceptance/actions. "
                "Its hand-set blend weights and adjustment caps therefore remain bounded secondary evidence rather than statistically estimated acceptance coefficients."
            ),
            "holdout_predictive_acceptance_test_detected": behavior_oos_predictive_test,
            "authoritative_empirical_claim_allowed": behavior_oos_predictive_test,
        },
    ]

    payload = {
        "model_version": MODEL_VERSION,
        "purpose": "Separate production-path software invariants from empirical decision-model validation.",
        "summary": {
            "production_roster_aware": production_roster_aware,
            "runtime_roster_version_single_source": runtime_version_single_source,
            "post_overlay_ranking_refresh_present": post_overlay_ranking_refresh,
            "provisional_high_leverage_acceptance_gate": acceptance_has_authoritative_gate,
            "final_score_overlap_ablation_required": True,
            "final_score_overlap_currently_detected": final_composite_overlap,
            "primitive_final_score_active": primitive_final_score,
            "behavioral_predictive_holdout_detected": behavior_oos_predictive_test,
        },
        "findings": findings,
    }
    (OUT / "decision_path_integrity_audit.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload["summary"], indent=2))

    if not production_roster_aware:
        raise SystemExit("Production trade path failed roster-awareness invariant")
    if not runtime_version_single_source:
        raise SystemExit("Production trade path has a duplicate/stale roster resolver version source")
    if not post_overlay_ranking_refresh:
        raise SystemExit("Post-ranking roster interaction is not reconciled with exposed rankings/authority")


if __name__ == "__main__":
    main()
