#!/usr/bin/env python3
"""Stable FSFFL Simulator application entry point.

Delegates to the current validated vectorized Simulator implementation while
preserving the environment-configurable simulation count contract. Historical
and experimental runners remain available for reproducibility/benchmarking.
"""
from __future__ import annotations

import run_fsffl_season_simulator_preproduction as current

MODEL_VERSION = "FSFFL-Simulator-Application-1.0"


def main():
    current.main()


if __name__ == "__main__":
    main()
