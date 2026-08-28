#!/usr/bin/env python3
"""Utilities for projection-source uncertainty in FSFFL simulations.

The projection ensemble already treats independent source forecasts as equal-weight
estimates of a player's expected scoring level. This module converts those actual
source forecasts into centered multiplicative mean scenarios for Monte Carlo use.

Important distinction:
- historical weekly volatility describes game-to-game outcome dispersion;
- source disagreement describes epistemic uncertainty about the player's latent
  scoring mean.

We therefore do not add source disagreement to weekly standard deviation. Instead,
a simulation may select one of the observed independent source means and retain that
mean shift across the simulated season. As in-season actual evidence gains weight,
the pre-season source disagreement is shrunk by the same existing actual-weight
coefficient already used by the dynamic projection updater. No new tuning coefficient
is introduced here.
"""

from __future__ import annotations

import math
import statistics
from typing import Any, Dict, List


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def source_mean_multipliers(
    baseline_player: Dict[str, Any],
    dynamic_update: Dict[str, Any] | None = None,
) -> List[float]:
    """Return equal-weight latent-mean multipliers centered at exactly 1.0.

    Falls back to [1.0] unless the player has an authoritative multi-source
    ensemble estimate. The returned list contains one multiplier per usable
    independent source forecast.
    """
    if not baseline_player or not baseline_player.get("authoritative_projection_allowed", False):
        return [1.0]

    try:
        source_count = int(baseline_player.get("source_count") or 0)
    except (TypeError, ValueError):
        source_count = 0
    if source_count < 2:
        return [1.0]

    source_ppg = baseline_player.get("source_ppg") or {}
    values = [float(v) for v in source_ppg.values() if _finite(v) and float(v) >= 0.0]
    if len(values) < 2:
        return [1.0]

    ensemble_ppg = baseline_player.get("fsffl_projected_ppg")
    if not _finite(ensemble_ppg) or float(ensemble_ppg) <= 0.25:
        ensemble_ppg = statistics.fmean(values)
    ensemble_ppg = float(ensemble_ppg)
    if ensemble_ppg <= 0.25:
        return [1.0]

    dynamic_update = dynamic_update or {}
    actual_weight = dynamic_update.get("actual_weight", 0.0)
    try:
        actual_weight = max(0.0, min(1.0, float(actual_weight or 0.0)))
    except (TypeError, ValueError):
        actual_weight = 0.0

    remaining_prior_weight = 1.0 - actual_weight
    multipliers = [
        1.0 + remaining_prior_weight * ((value / ensemble_ppg) - 1.0)
        for value in values
    ]

    # Rounding in normalized source artifacts can make the arithmetic mean differ
    # from 1 by a few basis points. Re-center so adding epistemic uncertainty does
    # not change the ensemble's expected mean.
    center = statistics.fmean(multipliers)
    if not math.isfinite(center) or center <= 0.0:
        return [1.0]
    return [m / center for m in multipliers]


def source_uncertainty_summary(
    baseline_player: Dict[str, Any],
    dynamic_update: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    multipliers = source_mean_multipliers(baseline_player, dynamic_update)
    return {
        "scenario_count": len(multipliers),
        "active": len(multipliers) >= 2 and max(multipliers) - min(multipliers) > 1e-12,
        "multipliers": [round(x, 8) for x in multipliers],
        "mean_multiplier": round(statistics.fmean(multipliers), 8),
    }
