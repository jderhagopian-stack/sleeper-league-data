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

MODEL_VERSION = "FSFFL-Decision-Path-Integrity-Audit-1.3"


def text(name: str) -> str:
    return (SCRIPT / name).read_text(encoding="utf-8")

def text_path(*parts: str) -> str:
    return SCRIPT.joinpath(*parts).read_text(encoding="utf-8")


def main():
    report = text("run_trade_report.py")
    trade_engine = text("trade_engine.py")
    v31 = text("run_trade_market_sweep_v31.py")
    option_governance = text("trade_option_governance.py")
    roster_overlay = text("roster_interaction_overlay.py")
    roster_resolution = text("roster_resolution_governance.py")
    candidate_pools = text("trade_candidate_pools.py")
    trade_behavior = text_path("trade_decision", "behavior_integration.py")
    historical_behavior = text_path("trade_decision", "historical_behavior_policy.py")
    state_policy = text("trade_state_policy.py")
    candidate_selector = text("trade_candidate_selector.py")
    state_selector_composition = text("trade_state_selector_composition.py")
    multi_asset_packages = text("trade_multi_asset_packages.py")
    multi_asset_composition = text("trade_multi_asset_composition.py")
    negotiation_family = text("trade_negotiation_family.py")
    bilateral_gate = text("trade_bilateral_gate.py")
    bilateral_composition = text("trade_bilateral_composition.py")
    v30 = text("run_trade_market_sweep_v30.py")
    v29 = text("run_trade_market_sweep_v29.py")
    v23 = text("run_trade_market_sweep_v23.py")
    v20 = text("run_trade_market_sweep_v20.py")
    utility = text("decision_utility.py")
    v13 = text("run_trade_market_sweep_v13.py")
    v16 = text("run_trade_market_sweep_v16.py")
    state = text("decision_lab_state_aware.py")
    behavior_prod_test = (ROOT / ".github" / "workflows" / "test-behavioral-intelligence-v3-production.yml").read_text(encoding="utf-8")

    production_roster_aware = (
        "trade_engine.py" in report
        and "run_trade_market_sweep_v31.py" in trade_engine
        and "run_trade_market_sweep_v20.py" in v31
        and "trade_negotiation_family.py" in v31
        and "trade_bilateral_gate.py" in v31
        and "trade_bilateral_composition.py" in v31
        and "bilateral_composition.install(v20, bilateral_gate)" in v31
        and "trade_multi_asset_packages.py" in v31
        and "trade_multi_asset_composition.py" in v31
        and "multi_asset_composition.install(v20, multi_asset_packages)" in v31
        and "trade_state_policy.py" in v31
        and "trade_candidate_selector.py" in v31
        and "trade_state_selector_composition.py" in v31
        and "state_selector_composition.install(" in v31
        and 'SCRIPT / "trade_decision" / "historical_behavior_policy.py"' in v31
        and "historical_behavior.install_historical_state_conditioning(" in v31
        and 'SCRIPT / "trade_decision" / "behavior_integration.py"' in v31
        and "trade_behavior.install(historical_behavior, bi2, bi3_cache, bi3_cache_status)" in v31
        and "trade_candidate_pools.py" in v31
        and "candidate_pools.apply_to_report(report)" in v31
        and "roster_resolution_governance.py" in v31
        and "roster_interaction_overlay.py" in v31
        and "run_trade_market_sweep_v21.py" not in v31
        and "run_trade_market_sweep_v22.py" not in v31
        and "run_trade_market_sweep_v23.py" not in v31
        and "run_trade_market_sweep_v24.py" not in v31
        and "run_trade_market_sweep_v26.py" not in v31
        and "run_trade_market_sweep_v27.py" not in v31
        and "run_trade_market_sweep_v28.py" not in v31
        and "run_trade_market_sweep_v29.py" not in v31
        and "run_trade_market_sweep_v30.py" not in v31
        and "legalize_trade_rosters" in v13
        and "forced_cut" in v13
    )
    runtime_version_single_source = (
        "simulation.roster_resolution_model_version" in roster_resolution
        and "ROSTER_MODEL=" not in roster_resolution
        and "roster_resolution.apply_to_report(report)" in v31
    )

    acceptance_declared_heuristic = (
        "not a calibrated" in v16.lower()
        or "not a calibrated\nprobability" in v16.lower()
        or "heuristic_acceptance_fit_not_probability" in v16
    )
    acceptance_separate_from_trade_value = (
        "trade_option_governance.py" in v31
        and all(x in v31 for x in (
            "acceptance_fit_affects_trade_valuation",
            "acceptance_fit_reported_as_separate_behavioral_intelligence",
            "acceptance_fit_hard_gate_on_trade_quality",
            "behavioral_intelligence_informs_counterparty_feasibility_not_trade_value",
        ))
        and '"affects_trade_valuation": False' in option_governance
        and '"BEHAVIORAL_INTELLIGENCE"' in option_governance
        and '"OBSERVED_CURRENT_OFFER_PLUS_BEHAVIORAL_DIAGNOSTIC"' in option_governance
        and '"counter_acceptance_itself_observed": False' in option_governance
    )
    acceptance_band_ranking_only = (
        all(x in state_selector_composition for x in (
            '"acceptance_band_is_authoritative_candidate_gate": False',
            '"acceptance_fit_used_as_negotiation_ranking_signal": True',
        ))
        and "trade_candidate_selector.py" in v31
    )
    acceptance_has_authoritative_gate = (
        not acceptance_separate_from_trade_value
        and not acceptance_band_ranking_only
        and 'in {"HIGH", "MEDIUM"}' in v16
        and "recommended_next_action" in v16
        and "realistic" in v16
    )

    # The final score must use Shared Decision Utility 2.0. Current-season
    # outcomes are normalized against the canonical league baseline and combined
    # without hand-set cross-metric coefficients; future, liquidity and
    # resilience remain distinct value-denominated channels.
    final_overlap_tokens = {
        "shared_utility_called": 'resolved = utility.score(sim)' in v20,
        "league_relative_current_signal": (
            "statistics.median(values.values())" in utility
            and 'ref = sim.get("league_reference") or {}' in utility
        ),
        "market_dynasty_delta_in_final_score": 'future_value = sf(s.get("market_dynasty_delta"))' in utility,
        "liquidity_delta_in_final_score": 'liquidity_value = sf(s.get("liquidity_value_delta"))' in utility,
        "resilience_delta_in_final_score": 'resilience_value = sf(s.get("resilience_value_delta"))' in utility,
        "optionality_diagnostic_only": '"optionality_incremental_value_authorized": False' in utility,
        "strategic_composite_built_upstream_for_diagnostics": "strategic_value_delta" in state and "strategic_score" in state,
        "break_glass_built_upstream_for_diagnostics": "break_glass_delta" in state,
    }
    final_composite_overlap = (
        's.get("strategic_value_delta")' in utility
        or 's.get("break_glass_delta")' in utility
        or '0.30 * break_glass' in utility
        or '0.15 * strategic' in utility
    )
    fixed_unit_conversion_constants = any(x in utility for x in (
        "CURRENT_TITLE_SCALE", "CURRENT_PLAYOFF_SCALE", "CURRENT_WINS_SCALE",
        "CURRENT_POINTS_SCALE", "FUTURE_OPTIONALITY_SCALE", "LIQUIDITY_SCALE",
        "RESILIENCE_SCALE", "OPPONENT_EXTERNALITY_SCALE",
    ))
    primitive_final_score = all(final_overlap_tokens[k] for k in (
        "shared_utility_called",
        "league_relative_current_signal",
        "market_dynasty_delta_in_final_score",
        "liquidity_delta_in_final_score",
        "resilience_delta_in_final_score",
        "optionality_diagnostic_only",
    )) and not final_composite_overlap and not fixed_unit_conversion_constants

    behavior_oos_predictive_test = any(
        token in behavior_prod_test.lower()
        for token in (
            "holdout acceptance", "held-out acceptance", "future acceptance",
            "out-of-sample acceptance", "predictive log loss", "brier score",
        )
    )

    post_overlay_ranking_refresh = (
        "def refresh_negotiation_ranking" in roster_overlay
        and "ranker.recompute_from_row" in roster_overlay
        and "recommended_next_action_empirically_authoritative" in roster_overlay
        and "overlay.apply_to_report(report, interaction, ranker)" in v31
    )

    threshold_free_option_governance = (
        "trade_option_governance.py" in v31
        and "unsupported_numeric_score_cutoff_used_for_better_worse" in v31
        and "DIAGNOSTIC_ONLY_NOT_CATEGORICAL_DECISION_RULE" in option_governance
        and "relation_from_deltas" in option_governance
        and "score_delta>750" not in option_governance.replace(" ", "")
        and "score_delta<-750" not in option_governance.replace(" ", "")
        and "abs(deltas[0]-750)" not in report.replace(" ", "")
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
            "status": "SEPARATE_BEHAVIORAL_FEASIBILITY_NO_TRADE_VALUE_GATE" if acceptance_separate_from_trade_value else ("RANKING_SIGNAL_ONLY_NO_AUTHORITATIVE_BAND_GATE" if acceptance_band_ranking_only else ("PROVISIONAL_HIGH_LEVERAGE_HEURISTIC" if acceptance_has_authoritative_gate else "NO_AUTHORITATIVE_HEURISTIC_GATE_DETECTED")),
            "observation": (
                "Acceptance fit is explicitly separated from trade quality in the production v31 governance layer and remains Behavioral Intelligence about counterparty feasibility rather than a valuation input."
                if acceptance_separate_from_trade_value else
                "Human acceptance is explicitly described as heuristic rather than probabilistic, but hand-set fit bands may still have decision leverage and require sensitivity qualification."
            ),
            "declared_not_probability": acceptance_declared_heuristic,
            "has_authoritative_decision_leverage": acceptance_has_authoritative_gate,
            "acceptance_band_ranking_only": acceptance_band_ranking_only,
            "acceptance_separate_from_trade_value": acceptance_separate_from_trade_value,
            "authoritative_empirical_claim_allowed": False,
        },
        {
            "id": "OPTION-COMPARISON-THRESHOLD-001",
            "severity": "INFO" if threshold_free_option_governance else "CRITICAL",
            "status": "UNSUPPORTED_SCORE_CLIFF_REMOVED" if threshold_free_option_governance else "UNSUPPORTED_SCORE_CLIFF_DETECTED",
            "observation": "Categorical BETTER/WORSE option comparison must not depend on an uncalibrated composite-score distance threshold; composite score may remain diagnostic only.",
            "software_invariant": threshold_free_option_governance,
        },
        {
            "id": "FINAL-SCORE-OVERLAP-001",
            "severity": "HIGH",
            "status": "UNRESOLVED_OVERLAP" if final_composite_overlap else "STRUCTURALLY_DEDUPLICATED",
            "observation": (
                "The final score uses Shared Decision Utility 2.0: league-relative Simulator current outcomes plus market-dynasty future value and direct liquidity/replacement-resilience channels. Optionality, strategic and break-glass composites remain diagnostic only, and fixed cross-unit conversion constants are absent."
            ),
            "detected_components": final_overlap_tokens,
            "authoritative_empirical_claim_allowed": False,
        },
        {
            "id": "BEHAVIOR-OOS-001",
            "severity": "HIGH",
            "status": "PREDICTIVE_HOLDOUT_PRESENT" if behavior_oos_predictive_test else "STRUCTURAL_VALIDATION_ONLY",
            "observation": (
                "Behavioral Intelligence 3 has strong leakage/boundedness/sample-confidence tests, but its production workflow does not demonstrate held-out prediction of future manager acceptance/actions. Its hand-set blend weights and adjustment caps therefore remain bounded secondary evidence rather than statistically estimated acceptance coefficients."
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
            "provisional_high_leverage_acceptance_gate": not acceptance_separate_from_trade_value,
            "acceptance_band_authoritative_gate_active": acceptance_has_authoritative_gate,
            "acceptance_band_ranking_only_policy_present": acceptance_band_ranking_only,
            "acceptance_separate_from_trade_value": acceptance_separate_from_trade_value,
            "threshold_free_option_governance": threshold_free_option_governance,
            "final_score_overlap_ablation_required": True,
            "final_score_overlap_currently_detected": final_composite_overlap,
            "primitive_final_score_active": primitive_final_score,
            "fixed_unit_conversion_constants_detected": fixed_unit_conversion_constants,
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
    if not threshold_free_option_governance:
        raise SystemExit("Production option comparison still contains an unsupported composite-score cliff")


if __name__ == "__main__":
    main()
