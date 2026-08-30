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

    # The bilateral gate intentionally no longer reproduces v21's categorical
    # state thresholds. Verify the new continuous-utility semantics directly.
    gate_cases = [
        ({"buyer_decision_utility_score": 250.0}, True),
        ({"buyer_decision_utility_score": 0.0}, True),
        ({"buyer_decision_utility_score": -0.01}, False),
        ({"buyer_decision_utility_score": None}, True),
        ({}, True),
    ]
    for i, (br, expected_pass) in enumerate(gate_cases):
        passes, reason = gate.evaluate(copy.deepcopy(br))
        if passes is not expected_pass:
            raise AssertionError(f"gate case {i}: expected {expected_pass}, got {passes}: {reason}")
        actual = gate.apply(copy.deepcopy(br))
        assert actual["market_intelligence_hard_gate_pass"] is expected_pass
        assert actual["categorical_state_thresholds_authoritative"] is False
        assert actual["missing_utility_defaults_to_retain_for_search"] is True
        if not expected_pass:
            assert actual["current_state_gate"] == "BUYER_UTILITY_NEGATIVE"

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
        "production_switched": True,\n        "legacy_categorical_gate_equivalence_required": False,
    })


if __name__ == "__main__":
    main()
