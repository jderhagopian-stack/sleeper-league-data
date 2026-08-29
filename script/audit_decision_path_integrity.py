#!/usr/bin/env python3
"""Static governance audit of the production trade-decision path.

The goal is not to certify model quality from source-code shape. It records
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
MODEL_VERSION = "FSFFL-Decision-Path-Integrity-Audit-1.2"


def text(name: str) -> str:
    return (SCRIPT / name).read_text(encoding="utf-8")


def main():
    report = text("run_trade_report.py")
    v30 = text("run_trade_market_sweep_v30.py")
    v29 = text("run_trade_market_sweep_v29.py")
    v23 = text("run_trade_market_sweep_v23.py")
    v21 = text("run_trade_market_sweep_v21.py")
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
        or "heuristic_acceptance_fit_not_probability" in v16
    )
    # HIGH/MEDIUM bands may label and rank negotiation realism, but are no
    # longer allowed to decide whether an otherwise rational candidate is
    # eligible for a normal recommendation.
    acceptance_band_still_hard_gate = (
        'if row.get("acceptance_likelihood") not in {"HIGH", "MEDIUM"}' in v21
        and 'acceptance_band_is_ranking_signal_not_eligibility_gate' not in v21
    )
    explicit_ranking_only_policy = (
        'acceptance_band_is_ranking_signal_not_eligibility_gate' in v21
        or 'acceptance_band_is_ranking_signal_not_eligibility_gate' in v23
    )

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
    negotiation_plausibility_in_strategic = (
        '+ 1200.0 * plausibility' in v20
        or 'score -= 3000.0' in v20
        or 'score -= 6000.0' in v20
    )
    primitive_final_score = all(final_overlap_tokens[k] for k in (
        "primitive_dynasty_delta_in_final_score",
        "primitive_liquidity_delta_in_final_score",
        "primitive_optionality_delta_in_final_score",
        "primitive_resilience_delta_in_final_score",
    )) and not final_composite_overlap and not negotiation_plausibility_in_strategic

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
            "software_invariant": production_roster_aware,
        },
        {
            "id": "DECISION-PATH-VERSION-001",
            "severity": "INFO" if runtime_version_single_source else "HIGH",
            "status": "SINGLE_RUNTIME_SOURCE" if runtime_version_single_source else "DUPLICATE_VERSION_SOURCE",
            "software_invariant": runtime_version_single_source,
        },
        {
            "id": "POST-RANK-OVERLAY-001",
            "severity": "INFO" if post_overlay_ranking_refresh else "CRITICAL",
            "status": "RANKING_REFRESH_AND_AUTHORITY_QUALIFICATION_PRESENT" if post_overlay_ranking_refresh else "POST_RANKING_MUTATION_NOT_RECONCILED",
            "software_invariant": post_overlay_ranking_refresh,
        },
        {
            "id": "ACCEPTANCE-GATE-001",
            "severity": "INFO" if explicit_ranking_only_policy and not acceptance_band_still_hard_gate else "HIGH",
            "status": "RANKING_SIGNAL_NOT_ELIGIBILITY_GATE" if explicit_ranking_only_policy and not acceptance_band_still_hard_gate else "PROVISIONAL_HIGH_LEVERAGE_HEURISTIC",
            "observation": (
                "Acceptance fit remains a heuristic negotiation-realism signal, not a probability. The HIGH/MEDIUM bands may label and rank options but no longer eliminate otherwise rational candidates from normal recommendation eligibility. Hard legality and buyer-current-state rationality gates remain separate."
            ),
            "declared_not_probability": acceptance_declared_heuristic,
            "has_authoritative_decision_leverage": acceptance_band_still_hard_gate,
            "authoritative_empirical_claim_allowed": False,
        },
        {
            "id": "FINAL-SCORE-OVERLAP-001",
            "severity": "HIGH",
            "status": "UNRESOLVED_OVERLAP" if final_composite_overlap or negotiation_plausibility_in_strategic else "STRUCTURALLY_DEDUPLICATED",
            "observation": (
                "The state-aware focal strategic score uses primitive dynasty, optionality, liquidity and direct roster-replacement resilience channels. Strategic/break-glass composites remain explanatory only, and negotiation plausibility is handled separately in negotiation ranking."
            ),
            "detected_components": final_overlap_tokens,
            "authoritative_empirical_claim_allowed": False,
        },
        {
            "id": "BEHAVIOR-OOS-001",
            "severity": "HIGH",
            "status": "PREDICTIVE_HOLDOUT_PRESENT" if behavior_oos_predictive_test else "STRUCTURAL_VALIDATION_ONLY",
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
            "provisional_high_leverage_acceptance_gate": acceptance_band_still_hard_gate,
            "acceptance_band_ranking_only_policy_present": explicit_ranking_only_policy,
            "final_score_overlap_ablation_required": True,
            "final_score_overlap_currently_detected": final_composite_overlap or negotiation_plausibility_in_strategic,
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
