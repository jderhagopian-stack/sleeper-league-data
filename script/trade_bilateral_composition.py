#!/usr/bin/env python3
"""Install canonical bilateral buyer gating by capability.

Replaces the historical v1.15 wrapper's buyer-rationality hard gate with the
version-neutral trade_bilateral_gate primitive. This Trade Decision component does not
name or import historical sweep versions; it wraps whichever retained module
exposes the buyer_rationality capability.

The gate affects counterparty feasibility/current-state viability only. It does
not alter focal trade valuation or final BETTER/MIXED/WORSE authority.
"""
from __future__ import annotations

MODEL_VERSION = "FSFFL-Bilateral-Buyer-Gate-Composition-1.0"


def install(root, bilateral_gate):
    def patch(mod):
        if hasattr(mod, "buyer_rationality") and not getattr(
            mod, "_shared_bilateral_gate_installed", False
        ):
            original = mod.buyer_rationality

            def buyer_rationality(row, dl):
                return bilateral_gate.apply(original(row, dl))

            mod.buyer_rationality = buyer_rationality
            mod._shared_bilateral_gate_installed = True
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

    patch(root)
    wrap_loader(root)
    return {
        "model_version": MODEL_VERSION,
        "bilateral_gate_model_version": bilateral_gate.MODEL_VERSION,
        "historical_v21_wrapper_required": False,
        "historical_versions_named_by_application_component": False,
    }


def apply_report_metadata(report, bilateral_gate, negotiation_family):
    top = report.get("top_5_alternatives") or []
    families = [negotiation_family.family_key(row) for row in top]

    report.setdefault("policy", {}).update({
        "bilateral_buyer_gate_model_version": bilateral_gate.MODEL_VERSION,
        "bilateral_buyer_gate_composition_model_version": MODEL_VERSION,
        "negotiation_family_model_version": negotiation_family.MODEL_VERSION,
        "acceptance_band_is_ranking_signal_not_eligibility_gate": True,
        "market_intelligence_can_veto_buyer_current_state_viability": True,
        "negotiation_family_deduplication": True,
        "swing_must_be_distinct_negotiation_family": True,
        "low_and_very_low_acceptance_fit_can_appear_in_normal_slots_if_bilaterally_rational": True,
        "trade_decision_bilateral_gate_internal_component": True,
        "trade_decision_negotiation_family_internal_component": True,
        "historical_v21_executed_in_current_path": False,
    })
    report.setdefault("candidate_counts", {})[
        "top_five_unique_negotiation_families"
    ] = len(set(families))
    report.setdefault("simulation", {})["execution_path"] = (
        str((report.get("simulation") or {}).get("execution_path") or "")
        + "_plus_shared_bilateral_market_intelligence_gate_plus_family_dedup"
    )
