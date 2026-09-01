#!/usr/bin/env python3
"""Regression tests for Opportunity Engine bilateral utility sign confirmation."""
from __future__ import annotations

from opportunity_engine import focal_utility_stability as stability

factory_calls=[]

class FakeEvaluator:
    def __init__(self, seed):
        self.seed=seed
    def evaluate(self, rows):
        row=rows[0]
        focal=float((row.get("scores") or {})[self.seed])
        counterparty=float((row.get("counterparty_scores") or {})[self.seed])
        return {
            "team_improvement_score": focal,
            "counterparty_shared_decision_utility_score": counterparty,
            "simulation": {
                "focus_delta": {
                    "expected_wins": focal/1000.0,
                    "championship_probability": focal/10000.0,
                }
            },
        }

def factory(focus_user_id, simulations, seed, strategic_posture):
    factory_calls.append((focus_user_id,simulations,seed,strategic_posture))
    return FakeEvaluator(seed)

rows=[
    {
        "description":"stable bilateral positive",
        "scores":{1:100,2:50,3:1},
        "counterparty_scores":{1:50,2:25,3:2},
    },
    {
        "description":"focal stable counterparty mixed",
        "scores":{1:100,2:50,3:20},
        "counterparty_scores":{1:10,2:-1,3:5},
    },
    {
        "description":"focal mixed counterparty stable",
        "scores":{1:100,2:-1,3:20},
        "counterparty_scores":{1:50,2:25,3:10},
    },
]
out=stability.evaluate(
    rows,
    "focus",
    simulations=500,
    seeds=[1,2,3],
    strategic_posture="AUTO",
    evaluator_factory=factory,
)
assert out["enabled"] is True
assert out["creates_new_utility"] is False
assert out["uses_fixed_utility_margin_threshold"] is False
assert out["changes_underlying_trade_utility"] is False
assert len(factory_calls)==3, factory_calls
assert [x["classification"] for x in out["rows"]]==[
    "STABLE_BILATERAL_POSITIVE","BILATERAL_SIGN_SENSITIVE","BILATERAL_SIGN_SENSITIVE"
]
assert [x["confirmed_for_headline_action"] for x in out["rows"]]==[
    True,False,False
]
assert out["rows"][0]["focal_classification"]=="STABLE_POSITIVE"
assert out["rows"][0]["counterparty_classification"]=="STABLE_POSITIVE"
assert out["rows"][1]["counterparty_classification"]=="SIGN_UNSTABLE"
assert out["rows"][2]["focal_classification"]=="SIGN_UNSTABLE"
assert out["rows"][0]["counterparty_score_min"]==2

disabled=stability.evaluate(rows,"focus",simulations=0,seeds=[1],evaluator_factory=factory)
assert disabled["enabled"] is False
assert disabled["creates_new_utility"] is False

print("Bilateral utility stability confirmation regressions passed")
