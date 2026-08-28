#!/usr/bin/env python3
"""Fast regression tests for simulator projection-source mean uncertainty."""

import numpy as np

import run_fsffl_season_simulator_projection_uncertainty as layer


def test_latent_source_mean_draw_is_persistent_across_weeks():
    layer._LATENT_MULTIPLIERS.clear()
    rng = np.random.default_rng(12345)
    row = {
        "player_id": "p1",
        "projection_mean_multipliers": [0.9, 1.1],
    }
    first = layer._latent_multiplier_draws(row, 2000, rng)
    second = layer._latent_multiplier_draws(row, 2000, rng)
    assert np.array_equal(first, second)
    assert set(np.unique(first)).issubset({np.float32(0.9), np.float32(1.1)})


def test_no_multi_source_uncertainty_is_exactly_neutral():
    layer._LATENT_MULTIPLIERS.clear()
    rng = np.random.default_rng(54321)
    row = {"player_id": "p2", "projection_mean_multipliers": [1.0]}
    draws = layer._latent_multiplier_draws(row, 500, rng)
    assert np.array_equal(draws, np.ones(500, dtype=np.float32))


def main():
    test_latent_source_mean_draw_is_persistent_across_weeks()
    test_no_multi_source_uncertainty_is_exactly_neutral()
    print("Projection uncertainty simulator regression tests passed.")


if __name__ == "__main__":
    main()
