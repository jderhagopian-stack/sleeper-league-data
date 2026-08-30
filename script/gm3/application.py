#!/usr/bin/env python3
"""GM3 application orchestration.

Owns current GM3 startup and application-specific reasoning composition.
The historical GM2.2 engine remains an internal mechanics provider only.
"""
from __future__ import annotations

import build_fsffl_gm30 as gm30
import gm30_nonprojection_governance as gm30_gov
import nonprojection_high_priority_overrides as high_priority
import package_curve_robustness as package_robustness
import run_fsffl_gm30_counterfactual as counterfactual

MODEL_VERSION = "FSFFL-GM3-Application-1.0"


def run():
    season = gm30.active_season()
    gm30.patch_gm22_runtime(season)
    high_priority.install(gm30.core)
    gm30_gov.install(gm30.core)
    package_robustness.install(gm30.core)

    def already_patched(active_season):
        if int(active_season) != int(season):
            raise RuntimeError("GM3 active season changed during governed startup")
        return None

    gm30.patch_gm22_runtime = already_patched
    counterfactual.install_counterfactual_trade_patch()
    gm30.main()


def architecture():
    return {
        "model_version": MODEL_VERSION,
        "application": "GM3",
        "legacy_mechanics_provider": "build_fsffl_gm_engine.py",
        "legacy_provider_has_current_application_authority": False,
        "application_specific_reasoning_owned_here": True,
        "governed_patch_order_preserved": True,
    }
