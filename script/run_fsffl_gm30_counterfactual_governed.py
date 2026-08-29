#!/usr/bin/env python3
"""Canonical governed GM 3.0 counterfactual entry point.

Order matters:
1. apply GM3's dynamic season/runtime patch;
2. apply validated non-projection structural de-duplication;
3. remove GM3 adapter reintroduction of future-pick uncertainty/control value;
4. install robust multi-curve package candidate discovery;
5. install counterfactual simulation so it captures that governed discovery path;
6. run GM3 without reapplying the dynamic patch over governance.
"""
from __future__ import annotations

import build_fsffl_gm30 as gm30
import gm30_nonprojection_governance as gm30_gov
import nonprojection_high_priority_overrides as high_priority
import package_curve_robustness as package_robustness
import run_fsffl_gm30_counterfactual as counterfactual

MODEL_VERSION = "FSFFL-GM30-Governed-Entrypoint-1.0"


def main():
    season = gm30.active_season()

    # Dynamic operating-season patch must run first because it replaces the
    # inherited pick-profile helper.
    gm30.patch_gm22_runtime(season)

    # Then govern the actual helpers that GM3 will execute.
    high_priority.install(gm30.core)
    gm30_gov.install(gm30.core)
    package_robustness.install(gm30.core)

    # gm30.main normally calls patch_gm22_runtime again. Make that second call a
    # guarded no-op so it cannot overwrite the governed helpers above.
    def already_patched(active_season):
        if int(active_season) != int(season):
            raise RuntimeError("GM3 active season changed during governed startup")
        return None

    gm30.patch_gm22_runtime = already_patched

    # Counterfactual simulation captures build_universal_trade_opportunities at
    # install time, so install it only after robust discovery is active.
    counterfactual.install_counterfactual_trade_patch()
    gm30.main()


if __name__ == "__main__":
    main()
