#!/usr/bin/env python3
"""
Run the FSFFL simulator with an environment-configurable simulation count.

Usage:
  FSFFL_SIMULATIONS=3000 python script/run_fsffl_season_simulator.py
  FSFFL_SIMULATIONS=50000 python script/run_fsffl_season_simulator.py
"""

import os
import build_fsffl_season_simulator as simulator

count = int(os.getenv("FSFFL_SIMULATIONS", "5000"))
if count < 100:
    raise SystemExit("FSFFL_SIMULATIONS must be at least 100.")

simulator.DEFAULT_SIMS = count
simulator.main()
