#!/usr/bin/env python3
"""Regression checks for the governed GM3 team-context publisher."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


SCRIPT = Path(__file__).resolve().parent
GM3 = SCRIPT / "gm3"
if str(GM3) not in sys.path:
    sys.path.insert(0, str(GM3))
spec = importlib.util.spec_from_file_location("gm3_team_context_test", GM3 / "team_context.py")
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

payload = module.build_team_context(
    "846634401482792960",
    data_dir=SCRIPT.parent / "data",
    simulations=50,
    seed=20260821,
    limit=2,
)
assert payload["authority"] == "GM3 Team Improvement"
assert payload["scenario_contract"]["zero_price_counterfactual"] is True
assert payload["scenario_contract"]["fair_price_estimate"] is False
assert payload["scenario_contract"]["trade_acceptance_probability"] is False
assert payload["scenario_contract"]["recommendation"] is False
assert payload["record_count"] == 2
assert payload["available_record_count"] > 0

available = next(row for row in payload["records"] if row["available"])
assert available["creates_trade_price"] is False
assert available["creates_acceptance_probability"] is False
assert available["recommendation"] is False
focal = available["focal_team_context"]
assert focal["shared_decision_utility"]["model_version"] == "FSFFL-Shared-Decision-Utility-2.0"
assert focal["decision_attribution"]["reconciles"] is True
assert focal["strategic_posture"]
assert focal["strategic_posture_source"]
assert focal["competitive_state"]
assert focal["active_objective_weights"]
assert focal["simulator_delta"]

print("GM3 team-context publisher regressions passed")
