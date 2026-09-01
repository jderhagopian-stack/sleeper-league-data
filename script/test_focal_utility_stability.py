#!/usr/bin/env python3
"""Regression tests for Opportunity Engine focal utility sign confirmation."""
from __future__ import annotations

from opportunity_engine import focal_utility_stability as stability

factory_calls=[]

class FakeEvaluator:
    def __init__(self, seed):
        self.seed=seed
    def evaluate(self, rows):
        row=rows[0]
        score=float((row.get("scores") or {})[self.seed])
        return {
            "team_improvement_score": score,
            "simulation": {
                "focus_delta": {
                    "expected_wins": score/1000.0,
                    "championship_probability": score/10000.0,
                }
            },
        }

def factory(focus_user_id, simulations, seed, strategic_posture):
    factory_calls.append((focus_user_id,simulations,seed,strategic_posture))
    return FakeEvaluator(seed)

rows=[
    {"description":"stable positive","scores":{1:100,2:50,3:1}},
    {"description":"mixed sign","scores":{1:100,2:-1,3:20}},
    {"description":"stable nonpositive","scores":{1:-100,2:0,3:-20}},
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
    "STABLE_POSITIVE","SIGN_UNSTABLE","STABLE_NON_POSITIVE"
]
assert [x["confirmed_for_headline_action"] for x in out["rows"]]==[
    True,False,False
]
assert out["rows"][0]["score_min"]==1
assert out["rows"][1]["score_min"]==-1
assert out["rows"][1]["score_max"]==100

disabled=stability.evaluate(rows,"focus",simulations=0,seeds=[1],evaluator_factory=factory)
assert disabled["enabled"] is False
assert disabled["creates_new_utility"] is False

print("Focal utility stability confirmation regressions passed")
