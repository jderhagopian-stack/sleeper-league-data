#!/usr/bin/env python3
"""Fast regression tests for FSFFL projection ensemble governance."""

from __future__ import annotations

from copy import deepcopy

from build_fsffl_projection_ensemble import build_player_ensemble, dedupe_independence_families


def source(source_id: str, family: str, points: float):
    return {
        "source_id": source_id,
        "config": {"independence_family": family},
        "payload": {
            "players": {
                "p1": {
                    "player_name": "Test Player",
                    "team": "TST",
                    "position": "WR",
                    "season": "2026",
                    "fsffl_projected_points": points,
                    "fsffl_projected_ppg": points / 17.0,
                }
            }
        },
    }


def test_equal_weight_mean_and_disagreement():
    sources = [source("a", "a", 170.0), source("b", "b", 204.0)]
    players = build_player_ensemble(sources)
    player = players["p1"]
    assert player["fsffl_projected_points"] == 187.0
    assert player["source_count"] == 2
    assert player["source_ids"] == ["a", "b"]
    assert player["source_disagreement_sd_points"] > 0
    assert player["source_disagreement_cv"] > 0


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


def test_single_source_remains_observable_but_not_falsely_multi_source():
    players = build_player_ensemble([source("only", "only", 170.0)])
    player = players["p1"]
    assert player["source_count"] == 1
    assert player["source_disagreement_sd_points"] == 0.0
    assert player["source_disagreement_cv"] == 0.0


def main():
    test_equal_weight_mean_and_disagreement()
    test_duplicate_information_family_is_not_double_counted()
    test_single_source_remains_observable_but_not_falsely_multi_source()
    print("Projection ensemble regression tests passed.")


if __name__ == "__main__":
    main()
