#!/usr/bin/env python3
"""Install the Trade Decision multi-asset package generator by capability.

This composition module lets current production retain deeper candidate-search
mechanics without depending on the historical v22 wrapper. It never imports or
names historical sweep versions. Instead, as the retained engine loads
descendants, any module exposing the base candidate_packages capability is
rebound to the Trade Decision trade_multi_asset_packages.candidate_packages function.

No pruning, simulation, buyer-rationality, or decision logic is changed.
"""
from __future__ import annotations

MODEL_VERSION = "FSFFL-Multi-Asset-Package-Composition-1.0"


def install(root, package_generator):
    def patch(mod):
        if hasattr(mod, "candidate_packages") and not getattr(
            mod, "_trade_decision_multi_asset_packages_installed", False
        ):
            mod.candidate_packages = package_generator.candidate_packages
            mod._trade_decision_multi_asset_packages_installed = True
        return mod

    def wrap_loader(mod):
        if not hasattr(mod, "load_module") or getattr(
            mod, "_trade_decision_multi_asset_descendant_loader_wrapped", False
        ):
            return mod

        original_loader = mod.load_module

        def loader(path, name):
            child = original_loader(path, name)
            child = patch(child)
            child = wrap_loader(child)
            return child

        mod.load_module = loader
        mod._trade_decision_multi_asset_descendant_loader_wrapped = True
        return mod

    patch(root)
    wrap_loader(root)
    return {
        "model_version": MODEL_VERSION,
        "trade_decision_package_generator_model_version": package_generator.MODEL_VERSION,
        "historical_v22_wrapper_required": False,
        "historical_versions_named_by_application_component": False,
    }


def apply_report_metadata(report, package_generator):
    report.setdefault("policy", {}).update(package_generator.policy())
    report["policy"].update({
        "multi_asset_package_composition_model_version": MODEL_VERSION,
        "historical_v22_executed_in_current_path": False,
    })
    report.setdefault("simulation", {})["execution_path"] = (
        str((report.get("simulation") or {}).get("execution_path") or "")
        + "_plus_trade_decision_multi_asset_candidate_search"
    )
