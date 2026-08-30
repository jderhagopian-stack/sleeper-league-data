#!/usr/bin/env python3
"""What-If / Alternate History application family boundary.

Forward What-If and historical Alternate History share counterfactual state
mechanics but retain different information constraints. This module defines the
application boundary without duplicating the existing GM3 counterfactual engine
or historical-state provider.
"""
from __future__ import annotations

MODEL_VERSION = "FSFFL-Counterfactual-Application-1.0"


def forward_engine_class():
    from run_fsffl_gm30_counterfactual import CounterfactualEngine
    return CounterfactualEngine


def analyze_historical_trade(*args, **kwargs):
    from run_historical_trade_analysis import analyze
    return analyze(*args, **kwargs)


def architecture():
    return {
        "model_version": MODEL_VERSION,
        "application": "What-If / Alternate History",
        "modes": ["forward_what_if", "historical_alternate_history"],
        "shared_historical_fact_provider": "fsffl_historical_state_provider.py",
        "shared_simulation_consumed": True,
        "historical_mode_requires_timestamp_information_firewall": True,
        "forward_mode_uses_current_state": True,
        "full_alternate_history_production_on_main": False,
    }
