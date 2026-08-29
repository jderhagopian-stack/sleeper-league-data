#!/usr/bin/env python3
"""Apply validated non-projection governance after GM-3 runtime patching.

GM 3.0 dynamically patches parts of the inherited GM-2.2 core. Governance must
therefore run *after* those dynamic patches so old provisional economics cannot
be reintroduced by the operating-season adapter.
"""
from __future__ import annotations

MODEL_VERSION = "FSFFL-GM30-Nonprojection-Governance-1.0"


def install(core):
    original_pick_profile = core._u_pick_profile

    def governed_pick_profile(aid, uid, ctx):
        out = dict(original_pick_profile(aid, uid, ctx))
        rnd = int(out.get("round") or 3)
        qsignal = core.safe_float(out.get("quality_signal"), 0.5)

        # Reuse the existing quality/upside transform but remove the additional
        # positive reward for forecast uncertainty. Uncertainty remains exposed
        # as uncertainty; it is not evidence that the asset is worth more.
        if rnd == 1:
            quality_upside = core.clamp(0.48 + 0.42 * qsignal, 0.48, 0.95)
        elif rnd == 2:
            quality_upside = core.clamp(0.28 + 0.32 * qsignal, 0.28, 0.72)
        else:
            quality_upside = core.clamp(0.12 + 0.20 * qsignal, 0.12, 0.45)

        out["upside_optionality_pre_governance_diagnostic"] = out.get("upside_optionality")
        out["upside_optionality"] = round(quality_upside, 4)
        out["forecast_uncertainty_incremental_value_authorized"] = False

        prior_control = core.safe_float(out.get("own_pick_control_bonus"), 0.0)
        out["own_pick_control_bonus_pre_governance_diagnostic"] = prior_control
        out["own_pick_control_bonus"] = 0.0
        out["own_pick_control_incremental_value_authorized"] = False
        return out

    core._u_pick_profile = governed_pick_profile
    core.GM30_NONPROJECTION_GOVERNANCE = {
        "installed": True,
        "model_version": MODEL_VERSION,
        "forecast_uncertainty_incremental_pick_value_authorized": False,
        "own_pick_control_incremental_value_authorized": False,
        "new_coefficients_introduced": False,
    }
    return core
