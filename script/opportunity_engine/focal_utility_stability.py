#!/usr/bin/env python3
"""Repeated-seed focal utility confirmation for Opportunity Engine routing.

This module creates no utility and fits no threshold. It asks only whether the
existing GM3/Shared Decision Utility remains positive across a configured family
of Simulator seeds. Opportunity Engine may use that confirmation to avoid
presenting simulation-sensitive trades as confident headline actions.
"""
from __future__ import annotations

import statistics

from gm3 import team_improvement as gm3

MODEL_VERSION = "FSFFL-OE-Focal-Utility-Stability-1.0"


def _classification(scores):
    if scores and all(float(x) > 0 for x in scores):
        return "STABLE_POSITIVE"
    if scores and all(float(x) <= 0 for x in scores):
        return "STABLE_NON_POSITIVE"
    return "SIGN_UNSTABLE"


def evaluate(rows, focus_user_id, simulations=500, seeds=None, strategic_posture="AUTO",
             evaluator_factory=None):
    rows = list(rows or [])
    seeds = [int(x) for x in (seeds or [])]
    sims = int(simulations)
    factory = evaluator_factory or gm3.portfolio_evaluator
    if not rows or not seeds or sims <= 0:
        return {
            "enabled": False,
            "model_version": MODEL_VERSION,
            "creates_new_utility": False,
            "uses_fixed_utility_margin_threshold": False,
            "confirmation_rule": "all configured seed utilities must remain > 0",
            "rows": [],
        }

    evaluators = {
        seed: factory(
            str(focus_user_id),
            simulations=sims,
            seed=seed,
            strategic_posture=strategic_posture,
        )
        for seed in seeds
    }

    results = []
    for ordinal, row in enumerate(rows):
        samples = []
        for seed in seeds:
            result = evaluators[seed].evaluate([row])
            sim = result.get("simulation") or {}
            focus = sim.get("focus_delta") or {}
            samples.append({
                "seed": seed,
                "team_improvement_score": float(result.get("team_improvement_score") or 0.0),
                "expected_wins_delta": float(focus.get("expected_wins") or 0.0),
                "championship_probability_delta": float(focus.get("championship_probability") or 0.0),
            })
        scores = [x["team_improvement_score"] for x in samples]
        classification = _classification(scores)
        results.append({
            "ordinal": ordinal,
            "description": row.get("description"),
            "classification": classification,
            "confirmed_for_headline_action": classification == "STABLE_POSITIVE",
            "samples": samples,
            "score_min": min(scores),
            "score_median": statistics.median(scores),
            "score_max": max(scores),
            "score_population_stddev": statistics.pstdev(scores) if len(scores) > 1 else 0.0,
        })

    return {
        "enabled": True,
        "model_version": MODEL_VERSION,
        "authority": "GM3 Team Improvement / Shared Decision Utility",
        "simulation_count_per_seed": sims,
        "seeds": seeds,
        "strategic_posture": strategic_posture,
        "confirmation_rule": "all configured seed utilities must remain > 0",
        "uses_fixed_utility_margin_threshold": False,
        "creates_new_utility": False,
        "changes_underlying_trade_utility": False,
        "rows": results,
    }
