#!/usr/bin/env python3
"""Regression tests for leakage-safe historical pick reconstruction."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import MethodType, SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "script" / "historical_pick_coordinate.py"
spec = importlib.util.spec_from_file_location("historical_pick_coordinate_tested", PATH)
mod = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules["historical_pick_coordinate_tested"] = mod
spec.loader.exec_module(mod)


def provider_shell():
    obj = mod.HistoricalPickCoordinateProvider.__new__(mod.HistoricalPickCoordinateProvider)
    obj.conversion_index = []
    obj._draft_evidence_cache = {}
    obj._players = {}
    obj.history_provider = None
    return obj


def test_startup_excluded_without_touching_evidence():
    p = provider_shell()

    def fail(*args, **kwargs):
        raise AssertionError("Startup path attempted to read rookie-pick evidence")

    p.available_evidence = MethodType(fail, p)
    out = p.historical_pick_value(
        trade_timestamp_ms=1,
        trade_season=2022,
        pick_season=2023,
        rnd=1,
        original_roster_id=1,
    )
    assert out["calibration_suitability"] == "EXCLUDED"
    assert "STARTUP" in out["reason"]


def test_future_completed_draft_does_not_leak_backward():
    p = provider_shell()
    evidence = {
        2023: SimpleNamespace(season=2023, complete_ms=100),
        2024: SimpleNamespace(season=2024, complete_ms=300),
        2025: SimpleNamespace(season=2025, complete_ms=500),
    }
    p.conversion_index = [
        {"season": "2023"}, {"season": "2024"}, {"season": "2025"}
    ]

    def fake(self, season):
        return evidence.get(int(season))

    p.draft_evidence = MethodType(fake, p)
    seen = p.available_evidence(350)
    assert [x.season for x in seen] == [2023, 2024]
    assert 2025 not in [x.season for x in seen]


def test_exact_slot_requires_historical_knowledge_boundary():
    p = provider_shell()
    p.draft_rows = MethodType(
        lambda self, season: [{
            "round": 1,
            "original_roster_id": 4,
            "draft_slot": 7,
        }],
        p,
    )
    p.draft_evidence = MethodType(
        lambda self, season: SimpleNamespace(start_ms=1000),
        p,
    )
    before = p.exact_slot_known(
        trade_timestamp_ms=999,
        pick_season=2025,
        rnd=1,
        original_roster_id=4,
    )
    after = p.exact_slot_known(
        trade_timestamp_ms=1000,
        pick_season=2025,
        rnd=1,
        original_roster_id=4,
    )
    assert before["known"] is False
    assert after["known"] is True
    assert after["slot_in_round"] == 7


def test_unresolved_pick_retains_distribution():
    probs = mod._slot_distribution_from_strength(0.72, 0.65, 2)
    assert abs(sum(probs.values()) - 1.0) < 1e-9
    assert sum(1 for x in probs.values() if x > 0) > 1
    expected = sum(slot * prob for slot, prob in probs.items())
    assert 1.0 <= expected <= 12.0


def test_no_current_market_or_production_pick_truth_dependency():
    text = PATH.read_text(encoding="utf-8")
    forbidden = (
        "market_values_fantasycalc.json",
        "statsguy",
        "FantasyCalc",
        "current_pick_value(",
        "infer_fc_pick_values",
    )
    for needle in forbidden:
        assert needle not in text, needle


def test_horizon_is_explicit_sensitivity_not_claimed_calibration():
    assert mod.HORIZON_STATUS == "BOUNDED_RESEARCH_SENSITIVITY_NOT_EMPIRICALLY_IDENTIFIED"
    assert mod.HORIZON_ANNUAL_SENSITIVITY["upper"] == 1.0
    assert mod.HORIZON_ANNUAL_SENSITIVITY["lower"] < mod.HORIZON_ANNUAL_SENSITIVITY["upper"]


def main():
    tests = [
        test_startup_excluded_without_touching_evidence,
        test_future_completed_draft_does_not_leak_backward,
        test_exact_slot_requires_historical_knowledge_boundary,
        test_unresolved_pick_retains_distribution,
        test_no_current_market_or_production_pick_truth_dependency,
        test_horizon_is_explicit_sensitivity_not_claimed_calibration,
    ]
    for fn in tests:
        fn()
        print("PASS", fn.__name__)


if __name__ == "__main__":
    main()
