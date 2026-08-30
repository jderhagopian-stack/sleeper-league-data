#!/usr/bin/env python3
"""Install shared v23-equivalent state/selector behavior onto v22.

This composition layer replaces v23's historical monkey-patch wrapper with
version-neutral shared components that have already passed direct equivalence
tests:
- trade_state_policy.py
- trade_candidate_selector.py
- negotiation_ranking.py

It preserves the same loader interception points used by v23 so the retained
v22/v21/v20/v19/v18/v16 mechanics receive the same current state-conditioned
eligibility, buyer-behavior conditioning, candidate preparation/ranking,
normal-option selection, and swing-option selection.

This module does not own final option comparison or action authority.
"""
from __future__ import annotations

from pathlib import Path

MODEL_VERSION = "FSFFL-Trade-State-Selector-Composition-1.0"


def install(v22, state_policy, selector, ranker):
    """Patch v22's inherited loader chain with shared current state/selector logic."""
    V21_PATH = Path(v22.V21_PATH)
    # v22 itself does not expose the deeper paths, so derive them from its script dir.
    script = Path(v22.__file__).resolve().parent
    V20_PATH = script / "run_trade_market_sweep_v20.py"
    V19_PATH = script / "run_trade_market_sweep_v19.py"
    V18_PATH = script / "run_trade_market_sweep_v18.py"
    V16_PATH = script / "run_trade_market_sweep_v16.py"

    original_v22_loader = v22.load_module

    def patch_v18(mod):
        original = mod.adjusted_buyer_rationality

        def adjusted(base_mod, row, dl, beh, meta):
            return state_policy.state_condition_behavior(
                row, original(base_mod, row, dl, beh, meta)
            )

        mod.adjusted_buyer_rationality = adjusted
        return mod

    def patch_v16(mod):
        original = mod.focal_viable

        def focal_viable(row):
            ok = original(row)
            beneficial = state_policy.focal_state_beneficial(row)
            row["focal_current_state_beneficial"] = bool(beneficial)
            row["focal_current_state"] = state_policy.focal_current_state(row)
            return bool(ok and beneficial)

        mod.focal_viable = focal_viable
        return mod

    def patch_v21_selectors(mod):
        inherited_swing = mod.select_swing_distinct

        def normal(viable, swing):
            return selector.select_normal_four(
                viable,
                swing,
                mod.negotiation_family_key,
                state_policy,
                ranker,
                mod.MAX_NORMAL_OPTIONS_PER_BUYER,
            )

        def swing(viable):
            return selector.select_swing(
                viable,
                inherited_swing,
                state_policy,
                ranker,
            )

        mod.select_normal_four_strict = normal
        mod.select_swing_distinct = swing
        return mod

    def patched_v22_loader(path: Path, name: str):
        mod = original_v22_loader(path, name)
        if Path(path) == V21_PATH:
            mod = patch_v21_selectors(mod)
            original_v21_loader = mod.load_module

            def patched_v21_loader(p2: Path, n2: str):
                m2 = original_v21_loader(p2, n2)
                if Path(p2) == V20_PATH:
                    original_v20_loader = m2.load_module

                    def patched_v20_loader(p3: Path, n3: str):
                        m3 = original_v20_loader(p3, n3)
                        if Path(p3) == V19_PATH:
                            original_v19_loader = m3.load_module

                            def patched_v19_loader(p4: Path, n4: str):
                                m4 = original_v19_loader(p4, n4)
                                if Path(p4) == V18_PATH:
                                    m4 = patch_v18(m4)
                                elif Path(p4) == V16_PATH:
                                    m4 = patch_v16(m4)
                                return m4

                            m3.load_module = patched_v19_loader
                        return m3

                    m2.load_module = patched_v20_loader
                return m2

            mod.load_module = patched_v21_loader
        return mod

    v22.load_module = patched_v22_loader
    return {
        "model_version": MODEL_VERSION,
        "v23_equivalent_state_policy": True,
        "v23_equivalent_candidate_selector": True,
        "historical_v23_wrapper_required": False,
    }


def apply_report_metadata(report, inherited_action, state_policy):
    report.setdefault("policy", {}).update({
        "trade_state_selector_composition_model_version": MODEL_VERSION,
        "competitive_state_treated_as_time_varying": True,
        "normal_recommendations_require_positive_continuous_focal_objective": True,
        "descriptive_state_labels_create_focal_utility_cliffs": False,
        "owner_behavior_conditioned_on_current_competitive_state": True,
        "historical_behavior_can_override_current_state_utility": False,
        "acceptance_band_is_authoritative_candidate_gate": False,
        "acceptance_band_is_authoritative_action_gate": False,
        "acceptance_band_is_ranking_signal_not_eligibility_gate": True,
        "acceptance_fit_used_as_negotiation_ranking_signal": True,
        "accepted_rejected_opportunity_denominator_available": False,
        "historical_state_at_trade_reconstruction_complete": False,
        "unsupported_post_sim_score_distance_action_cliff_active": False,
        "upstream_action_is_provisional_pending_v31_outcome_comparison": True,
        "canonical_trade_state_policy_shared_component": True,
        "canonical_trade_candidate_selector_shared_component": True,
        "historical_v23_executed_in_current_path": False,
    })
    final_action = state_policy.recompute_action_without_acceptance_band_gate(report)
    report["acceptance_gate_action_audit"] = {
        "inherited_pre_override_action": inherited_action,
        "final_action_without_acceptance_band_gate": final_action,
    }
    report["recommended_next_action"] = final_action
    report.setdefault("simulation", {})["execution_path"] = (
        "GM3_state_aware_plus_dynamic_continuous_focal_gate_plus_"
        "state_conditioned_owner_behavior_plus_bilateral_market_intelligence_"
        "plus_family_dedup_plus_multi_asset_search"
    )
    return final_action
