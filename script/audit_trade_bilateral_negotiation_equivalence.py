#!/usr/bin/env python3
"""Equivalence audit for shared v21 bilateral/negotiation primitives."""
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


def main():
    old = load(SCRIPT / "run_trade_market_sweep_v21.py", "v21_bilateral_reference")
    family = load(SCRIPT / "trade_negotiation_family.py", "shared_negotiation_family")
    gate = load(SCRIPT / "trade_bilateral_gate.py", "shared_bilateral_gate")

    family_rows = [
        {"buyer_user_id": "u1", "outgoing_assets": ["player:1"], "return_assets": ["player:2"]},
        {"buyer_user_id": "u1", "outgoing_assets": ["player:1"], "return_assets": ["player:2", "pick:2028:R3:orig2"]},
        {"buyer_user_id": "u1", "outgoing_assets": ["player:1"], "return_assets": ["pick:2028:R1:orig2"]},
        {"buyer_user_id": "u1", "outgoing_assets": ["player:1"], "return_assets": ["pick:2028:R2:orig2"]},
        {"buyer_user_id": "u2", "outgoing_assets": ["pick:2029:R1:orig2", "player:4"], "return_assets": ["player:8", "player:9"]},
    ]
    for i, row in enumerate(family_rows):
        a = old.negotiation_family_key(copy.deepcopy(row))
        b = family.family_key(copy.deepcopy(row))
        if a != b:
            raise AssertionError(f"family case {i}: old={a!r} new={b!r}")

    gate_cases = [
        {"buyer_state": "elite_contender", "buyer_title_delta": -0.031, "buyer_market_dynasty_delta": -1, "buyer_market_redraft_delta": 0, "buyer_break_glass_delta": -1, "heuristic_acceptance_fit_score": .8},
        {"buyer_state": "contender", "buyer_title_delta": -0.039, "buyer_market_dynasty_delta": -500, "buyer_market_redraft_delta": -200, "buyer_break_glass_delta": -500, "heuristic_acceptance_fit_score": .7},
        {"buyer_state": "contender", "buyer_title_delta": -0.041, "buyer_market_dynasty_delta": -1, "buyer_market_redraft_delta": 0, "buyer_break_glass_delta": -1, "heuristic_acceptance_fit_score": .6},
        {"buyer_state": "retool", "buyer_title_delta": 0, "buyer_market_dynasty_delta": -1200, "buyer_market_redraft_delta": 0, "buyer_break_glass_delta": -1200, "heuristic_acceptance_fit_score": .55},
        {"buyer_state": "rebuild", "buyer_title_delta": 0, "buyer_market_dynasty_delta": -900, "buyer_market_redraft_delta": 0, "buyer_break_glass_delta": -900, "heuristic_acceptance_fit_score": .45},
        {"buyer_state": "unknown", "buyer_title_delta": 0, "buyer_market_dynasty_delta": -1400, "buyer_market_redraft_delta": -1800, "buyer_break_glass_delta": -1200, "heuristic_acceptance_fit_score": .5},
        {"buyer_state": "rebuild", "buyer_title_delta": .01, "buyer_market_dynasty_delta": 500, "buyer_market_redraft_delta": 300, "buyer_break_glass_delta": 600, "heuristic_acceptance_fit_score": .4},
    ]

    for i, br in enumerate(gate_cases):
        old_eval = old._buyer_hard_gate(copy.deepcopy(br))
        new_eval = gate.evaluate(copy.deepcopy(br))
        if old_eval != new_eval:
            raise AssertionError(f"gate evaluate case {i}: old={old_eval!r} new={new_eval!r}")

        expected = copy.deepcopy(br)
        passes, reason = old_eval
        expected["market_intelligence_hard_gate_pass"] = bool(passes)
        expected["market_intelligence_hard_gate_reason"] = (
            reason or "buyer current-state utility clears bilateral hard gate"
        )
        if not passes:
            expected["current_state_viable"] = False
            expected["current_state_gate"] = "BUYER_IRRATIONAL"
            expected["reason"] = reason
            expected["heuristic_acceptance_fit_score"] = min(
                old.sf(expected.get("heuristic_acceptance_fit_score")), 0.27
            )
            expected["heuristic_acceptance_fit"] = "VERY_LOW"

        actual = gate.apply(copy.deepcopy(br))
        if expected != actual:
            raise AssertionError(f"gate apply case {i}: old={expected!r} new={actual!r}")

    # Explicit family semantics: player + sweetener remains same family.
    assert family.family_key(family_rows[0]) == family.family_key(family_rows[1])
    # Pick-only packages remain distinct.
    assert family.family_key(family_rows[2]) != family.family_key(family_rows[3])

    print({
        "status": "PASS",
        "negotiation_family_model_version": family.MODEL_VERSION,
        "bilateral_gate_model_version": gate.MODEL_VERSION,
        "family_cases": len(family_rows),
        "gate_cases": len(gate_cases),
        "production_switched": False,
    })


if __name__ == "__main__":
    main()
