#!/usr/bin/env python3
"""Install Trade Decision v23-equivalent state/selector behavior onto a retained engine.

This composition layer replaces historical state/selector wrappers with
version-neutral Trade Decision components that have passed direct equivalence tests:
- trade_state_policy.py
- trade_candidate_selector.py
- trade_negotiation_family.py
- negotiation_ranking.py

The explicit historical production pin belongs to the caller. This module does
not import or name historical sweep versions. It augments the supplied root and
descendants by capability, preserving current behavior without creating new
stale dependencies.

This module does not own final option comparison or final action authority.
"""
from __future__ import annotations

MODEL_VERSION = "FSFFL-Trade-State-Selector-Composition-1.3"


def install(root, state_policy, selector, ranker, negotiation_family=None):
    """Compose current state and selector policies into a retained engine tree."""

    def patch_state_capabilities(mod):
        if hasattr(mod, "adjusted_buyer_rationality") and not getattr(
            mod, "_trade_decision_state_behavior_installed", False
        ):
            original = mod.adjusted_buyer_rationality

            def adjusted(base_mod, row, dl, beh, meta):
                return state_policy.state_condition_behavior(
                    row, original(base_mod, row, dl, beh, meta)
                )

            mod.adjusted_buyer_rationality = adjusted
            mod._trade_decision_state_behavior_installed = True

        if hasattr(mod, "focal_viable") and not getattr(
            mod, "_trade_decision_focal_state_policy_installed", False
        ):
            original = mod.focal_viable

            def focal_viable(row):
                ok = original(row)
                beneficial = state_policy.focal_state_beneficial(row)
                row["focal_current_state_beneficial"] = bool(beneficial)
                row["focal_current_state"] = state_policy.focal_current_state(row)
                return bool(ok and beneficial)

            mod.focal_viable = focal_viable
            mod._trade_decision_focal_state_policy_installed = True
        return mod

    def family_key_for(mod):
        if negotiation_family is not None:
            return negotiation_family.family_key
        return getattr(mod, "negotiation_family_key", None)

    def patch_selector_capabilities(mod):
        if getattr(mod, "_trade_decision_candidate_selector_installed", False):
            return mod

        family_key = family_key_for(mod)

        # Newer inherited interface: selector functions live on the module
        # under their strict/distinct names.
        if (
            hasattr(mod, "select_normal_four_strict")
            and hasattr(mod, "select_swing_distinct")
            and family_key is not None
            and hasattr(mod, "MAX_NORMAL_OPTIONS_PER_BUYER")
        ):
            inherited_swing = mod.select_swing_distinct

            def normal(viable, swing):
                return selector.select_normal_four(
                    viable,
                    swing,
                    family_key,
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
            mod._trade_decision_candidate_selector_installed = True
            return mod

        # Older inherited interface: install the current v21/v23 semantics
        # directly over select_normal_four/select_swing. The shared base swing
        # rule replaces the older pre-v21 swing scoring.
        if (
            hasattr(mod, "select_normal_four")
            and hasattr(mod, "select_swing")
            and family_key is not None
            and hasattr(mod, "MAX_NORMAL_OPTIONS_PER_BUYER")
        ):
            def normal(viable, swing):
                return selector.select_normal_four(
                    viable,
                    swing,
                    family_key,
                    state_policy,
                    ranker,
                    mod.MAX_NORMAL_OPTIONS_PER_BUYER,
                )

            def swing(viable):
                return selector.select_swing(
                    viable,
                    selector.base_swing_distinct,
                    state_policy,
                    ranker,
                )

            mod.select_normal_four = normal
            mod.select_swing = swing
            mod._trade_decision_candidate_selector_installed = True

        return mod

    def patch_capabilities(mod):
        mod = patch_selector_capabilities(mod)
        mod = patch_state_capabilities(mod)
        return mod

    def wrap_descendant_loader(mod):
        if not hasattr(mod, "load_module") or getattr(
            mod, "_trade_decision_state_descendant_loader_wrapped", False
        ):
            return mod

        original_loader = mod.load_module

        def loader(path, name):
            child = original_loader(path, name)
            child = patch_capabilities(child)
            child = wrap_descendant_loader(child)
            return child

        mod.load_module = loader
        mod._trade_decision_state_descendant_loader_wrapped = True
        return mod

    patch_capabilities(root)
    wrap_descendant_loader(root)

    return {
        "model_version": MODEL_VERSION,
        "v23_equivalent_state_policy": True,
        "v23_equivalent_candidate_selector": True,
        "v21_equivalent_negotiation_family_supported": negotiation_family is not None,
        "historical_v23_wrapper_required": False,
        "deeper_historical_versions_named_by_application_component": False,
        "root_module_patched_by_capability": True,
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
        "trade_decision_state_policy_internal_component": True,
        "trade_decision_candidate_selector_internal_component": True,
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
