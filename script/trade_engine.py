#!/usr/bin/env python3
"""Stable production entry point for the current FSFFL trade engine.

Applications should call this module rather than a numbered historical
run_trade_market_sweep_vNN wrapper. The facade intentionally contains no trade
logic of its own; it delegates to the current authoritative engine and verifies
that the selected implementation advertises the expected model version.

This stable interface lets callers remain unchanged while the internal trade
architecture is progressively modularized.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent
CURRENT_ENGINE = SCRIPT / "run_trade_market_sweep_v31.py"
EXPECTED_MODEL_VERSION = "FSFFL-Counter-Market-Sweep-1.26"
FACADE_VERSION = "FSFFL-Trade-Engine-Facade-1.0"


def load_current_engine():
    spec = importlib.util.spec_from_file_location("fsffl_current_trade_engine", CURRENT_ENGINE)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    actual = str(getattr(mod, "MODEL_VERSION", ""))
    if actual != EXPECTED_MODEL_VERSION:
        raise RuntimeError(
            f"Trade engine facade expected {EXPECTED_MODEL_VERSION}, got {actual or 'UNKNOWN'}"
        )
    return mod


def main():
    load_current_engine().main()


if __name__ == "__main__":
    main()
