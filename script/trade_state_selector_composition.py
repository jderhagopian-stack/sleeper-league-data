#!/usr/bin/env python3
"""Install shared v23-equivalent state/selector behavior onto the retained engine.

This composition layer replaces v23's historical monkey-patch wrapper with
version-neutral shared components that have already passed direct equivalence
tests:
- trade_state_policy.py
- trade_candidate_selector.py
- negotiation_ranking.py

The only explicit historical production pin belongs to the caller (v31 -> v22).
This module does not import or name deeper historical sweep versions. Instead it
augments inherited modules by capability as v22's existing loader chain reaches
them. That preserves the existing mechanics without creating new stale
dependencies from the shared toolbox.

This module does not own final option comparison or final action authority.
"""
from __future__ import annotations

MODEL_VERSION = "FSFFL-Trade-State-Selector-Composition-1.1"


def install(v22, state_policy, selector, ranker):
    """Compose shared state and selector policies into v22's inherited engine."""

    def patch_state_capabilities(mod):
        # The historical buyer-rationality layer exposing this capability gets
        # current state-conditioned Behavioral Intelligence.
        if hasattr(mod, "adjusted_buyer_rationality") and not getattr(
            mod, "_shared_state_behavior_installed", False
        ):
            original = mod.adjusted_buyer_rationality

            def adjusted(base_mod, row, dl, beh, meta):
                return state_policy.state_condition_behavior(
                    row, original(base_mod, row, dl, beh, meta)
                )

            mod.adjusted_buyer_rationality = adjusted
            mod._shared_state_behavior_installed = True

        # The historical focal-viability layer exposing this capability gets
        # continuous focal-state eligibility rather than descriptive label cliffs.
        if hasattr(mod, "focal_viable") and not getattr(
            mod, "_shared_focal_state_policy_installed", False
        ):
            original = mod.focal_viable

            def focal_viable(row):
                ok = original(row)
                beneficial = state_policy.focal_state_beneficial(row)
                row["focal_current_state_beneficial"] = bool(beneficial)
                row["focal_current_state"] = state_policy.focal_current_state(row)
                return bool(ok and beneficial)

            mod.focal_viable = focal_viable
            mod._shared_focal_state_policy_installed = True
        return mod

    def wrap_descendant_loader(mod):
        if not hasattr(mod, "load_module") or getattr(
            mod, "_shared_state_descendant_loader_wrapped", False
        ):
            return mod

        original_loader = mod.load_module

        def loader(path, name):
            child = original_loader(path, name)
            child = patch_state_capabilities(child)
            child = wrap_descendant_loader(child)
            return child

        mod.load_module = loader
        mod._shared_state_descendant_loader_wrapped = True
        return mod

    original_v22_loader = v22.load_module

    def patched_v22_loader(path, name):
        mod = original_v22_loader(path, name)

        # v22's direct child is the selector-owning layer. Patch by capability,
        # not by a historical filename/version.
        if (
            hasattr(mod, "select_normal_four_strict")
            and hasattr(mod, "select_swing_distinct")
            and hasattr(mod, "negotiation_family_key")
            and hasattr(mod, "MAX_NORMAL_OPTIONS_PER_BUYER")
            and not getattr(mod, "_shared_candidate_selector_installed", False)
        ):
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
            mod._shared_candidate_selector_installed = True

        mod = patch_state_capabilities(mod)
        mod = wrap_descendant_loader(mod)
        return mod

    v22.load_module = patched_v22_loader
    return {
        "model_version": MODEL_VERSION,
        "v23_equivalent_state_policy": True,
        "v23_equivalent_candidate_selector": True,
        "historical_v23_wrapper_required": False,
        "deeper_historical_versions_named_by_shared_component": False,
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
