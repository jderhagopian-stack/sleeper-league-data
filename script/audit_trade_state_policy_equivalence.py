#!/usr/bin/env python3
"""Migration audit: preserve structural state-policy behavior while retiring legacy cliffs.

This is a safety test, not empirical validation. Unchanged primitives must remain
equivalent to historical v23 behavior, while categorical title-cap and
state-conditioned behavior differences are explicitly required.
"""
from __future__ import annotations

import copy
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "script"


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def assert_equal(a, b, label):
    if a != b:
        raise AssertionError(f"{label} mismatch:\nOLD={a!r}\nNEW={b!r}")


def main():
    old = load(SCRIPT / "run_trade_market_sweep_v23.py", "v23_state_policy_reference")
    new = load(SCRIPT / "trade_state_policy.py", "shared_trade_state_policy")
    ranker = load(SCRIPT / "negotiation_ranking.py", "shared_negotiation_ranker")

    focal_cases = [
        {"post_sim_score": 1, "simulation": {"strategic": {"objective_state": "rebuild"}}},
        {"post_sim_score": 0, "simulation": {"strategic": {"objective_state": "retool"}}},
        {"post_sim_score": 100, "simulation": {"strategic": {"objective_state": "contender"}}, "championship_equity_constraint": "PASS"},
        {"post_sim_score": 100, "simulation": {"strategic": {"objective_state": "elite_contender"}}, "championship_equity_constraint": "FAIL"},
        {"post_sim_score": -5, "focus_state": "unknown"},
    ]
    for i, row in enumerate(focal_cases):
        assert_equal(old.focal_current_state(copy.deepcopy(row)), new.focal_current_state(copy.deepcopy(row)), f"focal_state_{i}")
        old_b=old.focal_state_beneficial(copy.deepcopy(row))
        new_b=new.focal_state_beneficial(copy.deepcopy(row))
        if row.get("championship_equity_constraint") == "FAIL" and row.get("post_sim_score",0) > 0:
            assert old_b is False and new_b is True, (i,old_b,new_b)
        else:
            assert_equal(old_b,new_b,f"focal_beneficial_{i}")

    behavior_cases = []
    for state in ("elite_contender", "contender", "retool", "rebuild", "unknown"):
        for recv, send in (
            (["player:1"], ["player:2"]),
            (["pick:2028:R1:orig1"], ["player:2"]),
            (["player:1"], ["pick:2028:R1:orig1"]),
            (["pick:2028:R1:orig1"], ["pick:2029:R2:orig2"]),
        ):
            row = {"buyer_user_id": "u", "outgoing_assets": recv, "return_assets": send}
            br = {
                "buyer_state": state,
                "heuristic_acceptance_fit_score": .57,
                "state_utility_acceptance_fit_score": .53,
                "owner_behavior": {"adjustment": .08, "source": "fixture"},
            }
            behavior_cases.append((row, br))

    new_behavior_outputs=[]
    for i, (row, br) in enumerate(behavior_cases):
        old_br = old.state_condition_behavior(copy.deepcopy(row), copy.deepcopy(br))
        new_br = new.state_condition_behavior(copy.deepcopy(row), copy.deepcopy(br))
        sig=new_br["owner_behavior"]
        assert sig.get("categorical_state_conditioning_authorized") is False
        assert sig.get("state_compatibility_weight") is None
        assert abs(new_br["heuristic_acceptance_fit_score"] - .61) < 1e-9
        new_behavior_outputs.append(new_br["heuristic_acceptance_fit_score"])
        # Historical implementation may differ because its categorical state table
        # is precisely the legacy behavior being retired.
        assert old_br.get("acceptance_fit_basis") != new_br.get("acceptance_fit_basis")
    assert len(set(new_behavior_outputs)) == 1

    ranking_rows = [
        {
            "post_sim_score": score,
            "buyer_rationality": {
                "heuristic_acceptance_fit_score": acceptance,
                "owner_behavior": {"adjustment": behavior},
            },
        }
        for score, acceptance, behavior in (
            (0, .5, 0),
            (1000, .7, .04),
            (-2000, .25, -.08),
            (7000, .9, .12),
        )
    ]
    for i, row in enumerate(ranking_rows):
        old_rank = old.recompute_negotiation_ranking(copy.deepcopy(row))
        new_rank = new.recompute_negotiation_ranking(copy.deepcopy(row), ranker)
        assert_equal(old_rank, new_rank, f"negotiation_ranking_{i}")

    action_reports = [
        {"top_5_alternatives": []},
        {
            "current_offer_evaluation": {
                "post_sim_score": 100,
                "simulation": {"strategic": {"objective_state": "retool"}},
                "buyer_rationality": {"current_state_viable": True},
            },
            "top_5_alternatives": [{"post_sim_score": 200}],
        },
        {
            "current_offer_evaluation": {
                "post_sim_score": 300,
                "simulation": {"strategic": {"objective_state": "contender"}},
                "championship_equity_constraint": "PASS",
                "buyer_rationality": {"current_state_viable": True},
            },
            "top_5_alternatives": [{"post_sim_score": 200}],
        },
        {
            "current_offer_evaluation": {
                "post_sim_score": -10,
                "simulation": {"strategic": {"objective_state": "rebuild"}},
                "buyer_rationality": {"current_state_viable": False},
            },
            "top_5_alternatives": [{"post_sim_score": 100, "candidate_type": "SAME_PARTNER_COUNTER"}],
        },
    ]
    for i, report in enumerate(action_reports):
        assert_equal(
            old.recompute_action_without_acceptance_band_gate(copy.deepcopy(report)),
            new.recompute_action_without_acceptance_band_gate(copy.deepcopy(report)),
            f"provisional_action_{i}",
        )

    rows = []
    for score, state, adj in ((500, "rebuild", .05), (300, "contender", -.02), (900, "retool", .08)):
        rows.append({
            "post_sim_score": score,
            "buyer_user_id": str(score),
            "outgoing_assets": ["pick:2028:R1:orig1"],
            "return_assets": ["player:1"],
            "buyer_rationality": {
                "buyer_state": state,
                "heuristic_acceptance_fit_score": .55,
                "state_utility_acceptance_fit_score": .50,
                "owner_behavior": {"adjustment": adj},
            },
        })

    new_rows = new.prepare_rows(copy.deepcopy(rows), ranker)
    assert len(new_rows)==len(rows)
    assert all((r.get("buyer_rationality") or {}).get("owner_behavior",{}).get("categorical_state_conditioning_authorized") is False for r in new_rows)
    assert sorted(r["post_sim_score"] for r in new_rows)==sorted(r["post_sim_score"] for r in rows)

    print({
        "status": "PASS",
        "shared_model_version": new.MODEL_VERSION,
        "focal_cases": len(focal_cases),
        "behavior_cases": len(behavior_cases),
        "ranking_cases": len(ranking_rows),
        "action_cases": len(action_reports),
        "prepared_rows": len(rows),
        "legacy_state_cliffs_intentionally_retired": True,
    })


if __name__ == "__main__":
    main()
