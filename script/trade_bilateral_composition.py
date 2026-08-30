#!/usr/bin/env python3
"""Install shared v21-equivalent bilateral/family selector behavior.

This composition layer replaces the historical v21 wrapper by capability using:
- trade_bilateral_gate.py
- trade_negotiation_family.py
- trade_candidate_selector.py

It patches the retained v20 tree without naming deeper historical sweep versions.
Final trade quality remains owned downstream by shared option governance.
"""
from __future__ import annotations

MODEL_VERSION = "FSFFL-Trade-Bilateral-Composition-1.0"
MAX_NORMAL_OPTIONS_PER_BUYER = 2


def install(root, bilateral_gate, negotiation_family, selector):
    def patch_buyer_rationality(mod):
        if hasattr(mod, "buyer_rationality") and not getattr(
            mod, "_shared_bilateral_gate_installed", False
        ):
            original = mod.buyer_rationality

            def buyer_rationality(row, dl):
                br = original(row, dl)
                return bilateral_gate.apply(br)

            mod.buyer_rationality = buyer_rationality
            mod._shared_bilateral_gate_installed = True
        return mod

    def patch_selector_surface(mod):
        if getattr(mod, "_shared_v21_selector_surface_installed", False):
            return mod

        # v21 introduced these capabilities on the retained v19 surface.
        if hasattr(mod, "select_swing") and hasattr(mod, "select_normal_four"):
            mod.negotiation_family_key = negotiation_family.family_key
            mod.MAX_NORMAL_OPTIONS_PER_BUYER = MAX_NORMAL_OPTIONS_PER_BUYER
            mod.select_swing_distinct = selector.base_swing_distinct

            def normal(viable, swing):
                selected = []
                counts = {}
                used_families = set()
                swing_family = (
                    negotiation_family.family_key(swing) if swing else None
                )
                for row in viable:
                    fam = negotiation_family.family_key(row)
                    if swing_family and fam == swing_family:
                        continue
                    if fam in used_families:
                        continue
                    uid = str(row.get("buyer_user_id") or "")
                    if counts.get(uid, 0) >= MAX_NORMAL_OPTIONS_PER_BUYER:
                        continue
                    selected.append(row)
                    used_families.add(fam)
                    counts[uid] = counts.get(uid, 0) + 1
                    if len(selected) == 4:
                        break
                return selected

            mod.select_normal_four_strict = normal
            # v20/v19 call the generic names.
            mod.select_swing = mod.select_swing_distinct
            mod.select_normal_four = mod.select_normal_four_strict
            mod._shared_v21_selector_surface_installed = True
        return mod

    def patch(mod):
        mod = patch_buyer_rationality(mod)
        mod = patch_selector_surface(mod)
        return mod

    def wrap_loader(mod):
        if not hasattr(mod, "load_module") or getattr(
            mod, "_shared_bilateral_descendant_loader_wrapped", False
        ):
            return mod
        original_loader = mod.load_module

        def loader(path, name):
            child = original_loader(path, name)
            child = patch(child)
            child = wrap_loader(child)
            return child

        mod.load_module = loader
        mod._shared_bilateral_descendant_loader_wrapped = True
        return mod

    root = patch(root)
    root = wrap_loader(root)
    return {
        "model_version": MODEL_VERSION,
        "historical_v21_wrapper_required": False,
        "shared_bilateral_gate_model_version": bilateral_gate.MODEL_VERSION,
        "shared_negotiation_family_model_version": negotiation_family.MODEL_VERSION,
        "shared_swing_selector_model_version": selector.MODEL_VERSION,
        "deeper_historical_versions_named_by_shared_component": False,
    }


def apply_report_metadata(report, negotiation_family):
    top = report.get("top_5_alternatives") or []
    families = [negotiation_family.family_key(row) for row in top]
    report.setdefault("policy", {}).update({
        "trade_bilateral_composition_model_version": MODEL_VERSION,
        "acceptance_band_is_ranking_signal_not_eligibility_gate": True,
        "market_intelligence_can_veto_buyer_current_state_viability": True,
        "negotiation_family_deduplication": True,
        "swing_must_be_distinct_negotiation_family": True,
        "low_and_very_low_acceptance_fit_can_appear_in_normal_slots_if_bilaterally_rational": True,
        "canonical_bilateral_buyer_gate_shared_component": True,
        "canonical_negotiation_family_shared_component": True,
        "historical_v21_executed_in_current_path": False,
    })
    report.setdefault("candidate_counts", {})[
        "top_five_unique_negotiation_families"
    ] = len(set(families))
