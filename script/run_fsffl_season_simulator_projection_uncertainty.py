#!/usr/bin/env python3
"""FSFFL Season Simulator with governed projection-source uncertainty.

This wrapper preserves the validated pre-production simulator and changes only how
multi-source projection uncertainty enters Monte Carlo scoring:

- weekly historical SD remains game-to-game outcome volatility;
- independent projection-source disagreement becomes uncertainty about a player's
  latent scoring mean;
- one source-derived mean multiplier is drawn per player/simulation and retained
  across weeks, preventing epistemic uncertainty from being re-drawn as if it were
  weekly randomness;
- as current-season actual performance gains weight in the existing dynamic updater,
  the pre-season source disagreement shrinks by the same already-existing weight.

No new source-weighting or uncertainty coefficient is introduced.
"""

from __future__ import annotations

from typing import Any, Dict, Tuple

import numpy as np

import build_fsffl_season_simulator as core
import run_fsffl_season_simulator_preproduction as engine
from projection_source_uncertainty import source_mean_multipliers


_ORIGINAL_CANDIDATE_ROWS = engine.candidate_rows
_ORIGINAL_RUN = engine.run_preproduction_simulation
_LATENT_MULTIPLIERS: Dict[Tuple[str, int], np.ndarray] = {}
_BASELINE_CACHE: Dict[str, Any] | None = None
_DIAGNOSTICS = {
    "players_with_multi_source_mean_uncertainty": set(),
    "players_without_multi_source_mean_uncertainty": set(),
}


def _load_baseline(season: str) -> Dict[str, Any]:
    global _BASELINE_CACHE
    if _BASELINE_CACHE is None:
        _BASELINE_CACHE = core.load_json(
            engine.SIM_ROOT / season / "sources" / "preseason_fsffl_points.json",
            {},
        ) or {}
    return _BASELINE_CACHE


def candidate_rows(
    roster: Dict[str, Any],
    week: int,
    players: Dict[str, Any],
    projections: Dict[str, Any],
):
    rows = _ORIGINAL_CANDIDATE_ROWS(roster, week, players, projections)
    season = str((projections or {}).get("season") or "")
    baseline_players = (_load_baseline(season).get("players") or {}) if season else {}
    projection_players = (projections or {}).get("players") or {}

    for row in rows:
        pid = str(row.get("player_id") or "")
        baseline_player = baseline_players.get(pid) or {}
        dynamic_update = (projection_players.get(pid) or {}).get("dynamic_update") or {}
        multipliers = source_mean_multipliers(baseline_player, dynamic_update)
        row["projection_mean_multipliers"] = multipliers
        if len(multipliers) >= 2 and max(multipliers) - min(multipliers) > 1e-12:
            _DIAGNOSTICS["players_with_multi_source_mean_uncertainty"].add(pid)
        else:
            _DIAGNOSTICS["players_without_multi_source_mean_uncertainty"].add(pid)
    return rows


def _latent_multiplier_draws(
    row: Dict[str, Any],
    n_sims: int,
    rng: np.random.Generator,
) -> np.ndarray:
    pid = str(row.get("player_id") or "")
    key = (pid, n_sims)
    cached = _LATENT_MULTIPLIERS.get(key)
    if cached is not None:
        return cached

    raw = row.get("projection_mean_multipliers") or [1.0]
    values = np.asarray([float(x) for x in raw], dtype=np.float32)
    if values.size < 2 or float(np.max(values) - np.min(values)) <= 1e-12:
        draws = np.ones(n_sims, dtype=np.float32)
    else:
        indexes = rng.integers(0, values.size, size=n_sims)
        draws = values[indexes]
    _LATENT_MULTIPLIERS[key] = draws
    return draws


def generate_player_draws(
    row: Dict[str, Any],
    week: int,
    n_sims: int,
    rng: np.random.Generator,
    team_shocks: Dict[Tuple[int, str], np.ndarray],
    adjustments: Dict[str, Any],
):
    if row.get("player_id") is None:
        return (
            np.zeros(n_sims, dtype=np.float32),
            np.zeros(n_sims, dtype=bool),
        )

    pos = row.get("position")
    team = row.get("nfl_team")
    base_mean = float(row["mean"]) * engine.matchup_multiplier(adjustments, week, team, pos)
    latent = _latent_multiplier_draws(row, n_sims, rng)
    mean = base_mean * latent
    sd = max(0.1, float(row["sd"]))
    active_p = max(0.0, min(1.0, float(row["active_probability"])))

    available = rng.random(n_sims) <= active_p

    rho = engine.TEAM_SHOCK_RHO.get(str(pos), 0.0)
    if team and rho > 0:
        key = (week, team)
        if key not in team_shocks:
            team_shocks[key] = rng.standard_normal(n_sims).astype(np.float32)
        shared = team_shocks[key]
        independent = rng.standard_normal(n_sims).astype(np.float32)
        z = rho * shared + np.sqrt(1.0 - rho * rho) * independent
    else:
        z = rng.standard_normal(n_sims).astype(np.float32)

    points = (mean + sd * z).astype(np.float32, copy=False)
    np.maximum(points, 0.0, out=points)
    points *= available
    return points, available


def run_preproduction_simulation(*args, **kwargs):
    global _BASELINE_CACHE
    _LATENT_MULTIPLIERS.clear()
    _BASELINE_CACHE = None
    for value in _DIAGNOSTICS.values():
        value.clear()

    result = _ORIGINAL_RUN(*args, **kwargs)
    result.setdefault("features", {})["projection_source_latent_mean_uncertainty"] = True
    result["projection_source_uncertainty"] = {
        "method": "equal_weight_discrete_source_mean_draw_per_player_per_simulation",
        "weekly_volatility_treatment": "kept_separate_from_projection_source_disagreement",
        "latent_mean_persistence": "same_player_multiplier_retained_across_simulated_weeks",
        "in_season_shrinkage": "existing_dynamic_actual_weight_only; no_new_coefficient",
        "players_with_multi_source_mean_uncertainty": len(
            _DIAGNOSTICS["players_with_multi_source_mean_uncertainty"]
        ),
        "players_without_multi_source_mean_uncertainty": len(
            _DIAGNOSTICS["players_without_multi_source_mean_uncertainty"]
        ),
    }
    return result


def main():
    engine.candidate_rows = candidate_rows
    engine.generate_player_draws = generate_player_draws
    engine.run_preproduction_simulation = run_preproduction_simulation
    engine.main()


if __name__ == "__main__":
    main()
