#!/usr/bin/env python3
"""Test whether single-season raw-stat winners remain stable under position priors.

Research diagnostic only. Raw-stat evidence remains primary. Long-run (2014-2025)
and recent (2023-2025) position-level fantasy-point MAE are used only as
stabilizing priors and never converted directly into raw-stat authority.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

WEIGHTS = (0.25, 0.50, 0.75)
PRIOR_WINDOWS = ("historical_mae", "recent_2023_2025_mae")


def relative_scores(values: dict[str, float]) -> dict[str, float]:
    best = min(values.values())
    return {k: v / best for k, v in values.items()}


def evaluate_source_winner(category_mae: dict[str, float], raw_winner: str, position_priors: dict) -> dict:
    """Require the raw winner to survive both long-run and recent prior sensitivity."""
    scenario_winners: dict[str, str] = {}
    scenario_scores: dict[str, dict[str, float]] = {}
    coverage: dict[str, dict] = {}

    for prior_window in PRIOR_WINDOWS:
        prior_all = position_priors.get(prior_window) or {}
        common = sorted(set(category_mae) & set(prior_all))
        raw_winner_covered = raw_winner in common
        coverage[prior_window] = {
            "common_sources": common,
            "raw_winner_covered": raw_winner_covered,
        }
        if len(common) < 2 or not raw_winner_covered:
            return {
                "status": "INSUFFICIENT_PRIOR_COVERAGE",
                "coverage": coverage,
                "recommendation": "NEEDS_MORE_RAW_STAT_SEASONS",
                "stable_across_sensitivity": False,
                "stable_individual_winner": None,
                "winner_by_prior_window_and_weight": scenario_winners,
                "normalized_score_by_prior_window_and_weight": scenario_scores,
            }

        cat_rel = relative_scores({s: category_mae[s] for s in common})
        prior_rel = relative_scores({s: float(prior_all[s]) for s in common})
        for weight in WEIGHTS:
            scenario = f"{prior_window}|{weight}"
            blended = {
                source: (1 - weight) * cat_rel[source] + weight * prior_rel[source]
                for source in common
            }
            winner = min(blended, key=blended.get)
            scenario_winners[scenario] = winner
            scenario_scores[scenario] = blended

    stable = len(set(scenario_winners.values())) == 1
    stable_winner = next(iter(scenario_winners.values())) if stable else None
    raw_winner_survives = stable and stable_winner == raw_winner
    return {
        "status": "PASS",
        "coverage": coverage,
        "winner_by_prior_window_and_weight": scenario_winners,
        "stable_individual_winner": stable_winner,
        "stable_across_sensitivity": stable,
        "raw_winner_survives_all_priors": raw_winner_survives,
        "recommendation": (
            "STABLE_SINGLE_SOURCE_PRIOR"
            if raw_winner_survives
            else "UNSTABLE_NEEDS_MORE_RAW_STAT_SEASONS"
        ),
        "normalized_score_by_prior_window_and_weight": scenario_scores,
    }


def evaluate(scorecard: dict, priors: dict) -> dict:
    out = {}
    for category, row in scorecard["categories"].items():
        position = category.split("|", 1)[0]
        category_mae = {k: float(v) for k, v in row["mae_by_source"].items()}
        raw_winner = row["winner_in_2014_cross_section"]

        if raw_winner == "equal_weight":
            best_individual = min(category_mae.values())
            equal_mae = float(row["equal_weight_mae"])
            out[category] = {
                "status": "PASS",
                "common_players": row["common_players"],
                "raw_2014_winner": raw_winner,
                "equal_weight_mae": equal_mae,
                "best_individual_mae": best_individual,
                "equal_weight_relative_advantage_pct": 100.0 * (best_individual - equal_mae) / best_individual,
                "stable_across_sensitivity": False,
                "stable_individual_winner": None,
                "recommendation": "EQUAL_WEIGHT_PROVISIONAL_NEEDS_MORE_RAW_STAT_SEASONS",
                "structural_support": {
                    "ffa_average_head_to_head_win_rate_vs_individual_sources": priors.get("structural_findings", {}).get("ffa_average_head_to_head_win_rate_vs_individual_sources"),
                    "simple_average_win_rate_vs_weighted": priors.get("structural_findings", {}).get("simple_average_win_rate_vs_weighted"),
                    "note": "Long-run aggregation evidence supports keeping equal-weight as a mandatory challenger, but it is not the same ensemble as this exact four-source 2014 average and therefore cannot establish category stability by itself.",
                },
            }
            continue

        result = evaluate_source_winner(category_mae, raw_winner, priors["positions"][position])
        out[category] = {
            "common_players": row["common_players"],
            "raw_2014_winner": raw_winner,
            **result,
        }

    return {
        "schema_version": "1.1",
        "status": "RESEARCH_ONLY",
        "production_behavior_changed": False,
        "prior_weight_sensitivity": list(WEIGHTS),
        "prior_windows": ["2014-2025", "2023-2025"],
        "categories": out,
        "summary": {
            "equal_weight_provisional_needs_more_raw_stat_seasons": sum(
                v.get("recommendation") == "EQUAL_WEIGHT_PROVISIONAL_NEEDS_MORE_RAW_STAT_SEASONS"
                for v in out.values()
            ),
            "stable_single_source_prior": sum(
                v.get("recommendation") == "STABLE_SINGLE_SOURCE_PRIOR" for v in out.values()
            ),
            "unstable_needs_more_raw_stat_seasons": sum(
                v.get("recommendation") == "UNSTABLE_NEEDS_MORE_RAW_STAT_SEASONS"
                for v in out.values()
            ),
            "insufficient_prior_coverage": sum(
                v.get("status") == "INSUFFICIENT_PRIOR_COVERAGE" for v in out.values()
            ),
        },
        "governance": {
            "position_prior_is_stabilizer_only": True,
            "raw_stat_holdout_remains_primary": True,
            "recent_2023_2025_prior_explicitly_tested": True,
            "raw_winner_must_be_covered_by_each_prior_window": True,
            "equal_weight_not_assumed_stable_from_meta_aggregate": True,
            "single_shrinkage_coefficient_selected": False,
            "production_promotion_authority": False,
        },
    }


def self_test():
    score = {
        "categories": {
            "RB|carries": {
                "mae_by_source": {"A": 10, "B": 11},
                "equal_weight_mae": 10.4,
                "winner_in_2014_cross_section": "A",
                "common_players": 30,
            },
            "WR|yards": {
                "mae_by_source": {"A": 10, "B": 9},
                "equal_weight_mae": 8.8,
                "winner_in_2014_cross_section": "equal_weight",
                "common_players": 30,
            },
            "QB|tds": {
                "mae_by_source": {"A": 8, "B": 9},
                "equal_weight_mae": 8.3,
                "winner_in_2014_cross_section": "A",
                "common_players": 20,
            },
        }
    }
    pri = {
        "positions": {
            "RB": {
                "historical_mae": {"A": 20, "B": 30},
                "recent_2023_2025_mae": {"A": 21, "B": 29},
            },
            "WR": {
                "historical_mae": {"A": 20, "B": 19},
                "recent_2023_2025_mae": {"A": 20, "B": 19},
            },
            "QB": {
                "historical_mae": {"B": 20},
                "recent_2023_2025_mae": {"A": 19, "B": 20},
            },
        },
        "structural_findings": {
            "ffa_average_head_to_head_win_rate_vs_individual_sources": 0.69,
            "simple_average_win_rate_vs_weighted": 0.64,
        },
    }
    result = evaluate(score, pri)
    assert result["categories"]["RB|carries"]["recommendation"] == "STABLE_SINGLE_SOURCE_PRIOR"
    assert result["categories"]["WR|yards"]["recommendation"] == "EQUAL_WEIGHT_PROVISIONAL_NEEDS_MORE_RAW_STAT_SEASONS"
    assert result["categories"]["QB|tds"]["status"] == "INSUFFICIENT_PRIOR_COVERAGE"
    print("hierarchical projection stability self-test: PASS")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--scorecard",
        type=Path,
        default=Path("data/model_validation/projection_2014_multisource_raw_stat_scorecard.json"),
    )
    parser.add_argument(
        "--priors",
        type=Path,
        default=Path("data/model_validation/projection_position_accuracy_priors_2014_2025.json"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/model_validation/projection_championship_hierarchical_stability.json"),
    )
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    result = evaluate(
        json.loads(args.scorecard.read_text()),
        json.loads(args.priors.read_text()),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result["summary"], indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
