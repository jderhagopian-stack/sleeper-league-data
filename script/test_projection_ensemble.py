#!/usr/bin/env python3
"""Fast regression tests for FSFFL projection ensemble governance."""

from __future__ import annotations

from copy import deepcopy
import statistics

from build_fsffl_projection_ensemble import build_player_ensemble, dedupe_independence_families
from projection_source_uncertainty import source_mean_multipliers


def source(source_id: str, family: str, points: float, include_player: bool = True):
    players = {}
    if include_player:
        players["p1"] = {
            "player_name": "Test Player",
            "team": "TST",
            "position": "WR",
            "season": "2026",
            "fsffl_projected_points": points,
            "fsffl_projected_ppg": points / 17.0,
        }
    return {
        "source_id": source_id,
        "config": {"independence_family": family},
        "payload": {"players": players},
    }


def test_equal_weight_mean_and_disagreement():
    sources = [source("a", "a", 170.0), source("b", "b", 204.0)]
    player = build_player_ensemble(sources, minimum_sources=2)["p1"]
    assert player["fsffl_projected_points"] == 187.0
    assert player["source_count"] == 2
    assert player["source_ids"] == ["a", "b"]
    assert player["source_disagreement_sd_points"] > 0
    assert player["source_disagreement_cv"] > 0
    assert player["authoritative_projection_allowed"] is True
    assert player["authority_reason"] == "minimum_independent_sources_met"


def test_duplicate_information_family_is_not_double_counted():
    sources = [
        source("consensus", "shared", 180.0),
        source("component", "shared", 220.0),
        source("independent", "independent", 200.0),
    ]
    kept, rejected = dedupe_independence_families(deepcopy(sources))
    assert [x["source_id"] for x in kept] == ["consensus", "independent"]
    assert len(rejected) == 1
    assert rejected[0]["source_id"] == "component"
    assert rejected[0]["reason"] == "duplicate_independence_family"


def test_single_source_is_explicitly_non_authoritative():
    player = build_player_ensemble([source("only", "only", 170.0)], minimum_sources=2)["p1"]
    assert player["source_count"] == 1
    assert player["source_disagreement_sd_points"] == 0.0
    assert player["source_disagreement_cv"] == 0.0
    assert player["authoritative_projection_allowed"] is False
    assert player["authority_reason"] == "insufficient_player_level_independent_sources"


def test_global_source_presence_does_not_fake_player_level_authority():
    sources = [source("a", "a", 170.0), source("b", "b", 204.0, include_player=False)]
    player = build_player_ensemble(sources, minimum_sources=2)["p1"]
    assert len(sources) == 2
    assert player["source_count"] == 1
    assert player["authoritative_projection_allowed"] is False


def test_source_uncertainty_uses_observed_means_without_moving_ensemble_mean():
    player = build_player_ensemble(
        [source("a", "a", 170.0), source("b", "b", 204.0)],
        minimum_sources=2,
    )["p1"]
    multipliers = source_mean_multipliers(player)
    assert len(multipliers) == 2
    assert abs(statistics.fmean(multipliers) - 1.0) < 1e-12
    assert min(multipliers) < 1.0 < max(multipliers)


def test_actual_evidence_shrinks_preseason_source_disagreement():
    player = build_player_ensemble(
        [source("a", "a", 170.0), source("b", "b", 204.0)],
        minimum_sources=2,
    )["p1"]
    preseason = source_mean_multipliers(player, {"actual_weight": 0.0})
    midseason = source_mean_multipliers(player, {"actual_weight": 0.5})
    actual_dominated = source_mean_multipliers(player, {"actual_weight": 1.0})
    assert max(midseason) - min(midseason) < max(preseason) - min(preseason)
    assert actual_dominated == [1.0, 1.0]
    assert abs(statistics.fmean(midseason) - 1.0) < 1e-12


def test_non_authoritative_player_cannot_inject_source_uncertainty():
    player = build_player_ensemble([source("only", "only", 170.0)], minimum_sources=2)["p1"]
    assert source_mean_multipliers(player) == [1.0]


def main():
    test_equal_weight_mean_and_disagreement()
    test_duplicate_information_family_is_not_double_counted()
    test_single_source_is_explicitly_non_authoritative()
    test_global_source_presence_does_not_fake_player_level_authority()
    test_source_uncertainty_uses_observed_means_without_moving_ensemble_mean()
    test_actual_evidence_shrinks_preseason_source_disagreement()
    test_non_authoritative_player_cannot_inject_source_uncertainty()
    print("Projection ensemble regression tests passed.")


if __name__ == "__main__":
    main()
