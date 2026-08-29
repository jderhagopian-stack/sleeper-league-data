#!/usr/bin/env python3
"""Canonical governed GM 3.0 counterfactual entry point."""
from __future__ import annotations

import build_fsffl_gm30 as gm30
import gm30_nonprojection_governance as gm30_gov
import nonprojection_high_priority_overrides as high_priority
import package_curve_robustness as package_robustness
import run_fsffl_gm30_counterfactual as counterfactual

MODEL_VERSION = "FSFFL-GM30-Governed-Entrypoint-1.0"


def main():
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


if __name__ == "__main__":
    main()
