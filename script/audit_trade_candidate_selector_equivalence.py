#!/usr/bin/env python3
"""Equivalence audit: shared candidate selector vs current v23 patch semantics.

Migration-safety test only. Proves the shared selector returns the same prepared
candidate metadata, ordering, family de-duplication, per-buyer limits, and swing
selection as the currently executed v23 selector patch.
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


def fixture_rows():
    rows = []
    cases = [
        ("u1", 1100, .72, .05, "rebuild", ["player:A"], ["player:X"]),
        ("u1", 950, .61, .04, "rebuild", ["player:A"], ["player:Y"]),
        ("u1", 900, .50, .03, "rebuild", ["player:B"], ["player:Z"]),
        ("u2", 1250, .44, -.01, "contender", ["player:C"], ["player:M"]),
        ("u2", 800, .30, -.04, "contender", ["player:D"], ["player:N"]),
        ("u3", 700, .25, -.05, "retool", ["player:E"], ["player:P"]),
        ("u4", -50, .80, .08, "rebuild", ["player:F"], ["player:Q"]),
    ]
    for idx,(uid,post,accept,adj,state,outgoing,returns) in enumerate(cases):
        row={
            "row_id": idx,
            "buyer_user_id": uid,
            "outgoing_assets": outgoing,
            "return_assets": returns,
            "post_sim_score": post,
            "simulation": {"strategic": {"objective_state": state}},
            "championship_equity_constraint": "PASS",
            "buyer_rationality": {
                "buyer_state": state,
                "heuristic_acceptance_fit_score": accept,
                "state_utility_acceptance_fit_score": accept,
                "owner_behavior": {"adjustment": adj},
            },
        }
        rows.append(row)
    # duplicate family differing only by pick sweetener to test family collapse
    rows.append({
        "row_id": 99,
        "buyer_user_id": "u1",
        "outgoing_assets": ["player:A"],
        "return_assets": ["player:X","pick:2028:R3:orig2"],
        "post_sim_score": 1080,
        "simulation": {"strategic": {"objective_state": "rebuild"}},
        "championship_equity_constraint": "PASS",
        "buyer_rationality": {
            "buyer_state": "rebuild",
            "heuristic_acceptance_fit_score": .68,
            "state_utility_acceptance_fit_score": .68,
            "owner_behavior": {"adjustment": .05},
        },
    })
    return rows


def main():
    old = load(SCRIPT / "run_trade_market_sweep_v23.py", "v23_selector_reference")
    v21 = load(SCRIPT / "run_trade_market_sweep_v21.py", "v21_selector_reference")
    state = load(SCRIPT / "trade_state_policy.py", "shared_trade_state_policy")
    selector = load(SCRIPT / "trade_candidate_selector.py", "shared_trade_candidate_selector")
    ranker = load(SCRIPT / "negotiation_ranking.py", "shared_negotiation_ranker")

    # Preserve the original v21 swing selector before v23 patches the module
    # in place. The shared selector must receive the inherited unpatched swing
    # rule; otherwise state preparation would be applied twice in the test.
    inherited_swing_selector = v21.select_swing_distinct
    patched = old.patch_v21_selectors(v21)

    rows_old = fixture_rows()
    rows_new = copy.deepcopy(rows_old)

    prepared_for_base = state.prepare_rows(copy.deepcopy(rows_new), ranker)
    old_base_swing = inherited_swing_selector(copy.deepcopy(prepared_for_base))
    new_base_swing = selector.base_swing_distinct(copy.deepcopy(prepared_for_base))
    assert_equal(old_base_swing, new_base_swing, "base_swing_rule")

    old_swing = patched.select_swing_distinct(copy.deepcopy(rows_old))
    new_swing = selector.select_swing(
        copy.deepcopy(rows_new),
        inherited_swing_selector,
        state,
        ranker,
    )
    assert_equal(old_swing, new_swing, "swing_selection")

    old_normal = patched.select_normal_four_strict(copy.deepcopy(rows_old), copy.deepcopy(old_swing))
    new_normal = selector.select_normal_four(
        copy.deepcopy(rows_new),
        copy.deepcopy(new_swing),
        v21.negotiation_family_key,
        state,
        ranker,
        v21.MAX_NORMAL_OPTIONS_PER_BUYER,
    )
    assert_equal(old_normal, new_normal, "normal_four_selection")

    # Verify exact selected row IDs/order for easier diagnosis if behavior drifts.
    old_ids = [r["row_id"] for r in old_normal]
    new_ids = [r["row_id"] for r in new_normal]
    assert_equal(old_ids, new_ids, "normal_four_ids")

    # Validate contender title-equity hard constraint is preserved by selector.
    constrained = fixture_rows()
    constrained[3]["championship_equity_constraint"] = "FAIL"
    old_c = patched.select_normal_four_strict(copy.deepcopy(constrained), None)
    new_c = selector.select_normal_four(
        copy.deepcopy(constrained),
        None,
        v21.negotiation_family_key,
        state,
        ranker,
        v21.MAX_NORMAL_OPTIONS_PER_BUYER,
    )
    assert_equal(old_c, new_c, "contender_title_constraint")

    print({
        "status": "PASS",
        "shared_selector_model_version": selector.MODEL_VERSION,
        "candidate_count": len(rows_old),
        "normal_selected": old_ids,
        "base_swing_row_id": None if old_base_swing is None else old_base_swing["row_id"],
        "swing_row_id": None if old_swing is None else old_swing["row_id"],
        "production_switched": False,
    })


if __name__ == "__main__":
    main()
