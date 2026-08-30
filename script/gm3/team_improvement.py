#!/usr/bin/env python3
"""Stable GM3 Team Improvement application-area entry point.

The current authoritative implementation is Team Improvement Lab 1.4. Historical
v11-v13 wrapper files remain for reproducibility; production callers use this
stable entry point so implementation filenames do not become application
authority.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

MODEL_VERSION = "FSFFL-GM-Team-Improvement-Application-1.0"
EXPECTED_IMPLEMENTATION_VERSION = "FSFFL-GM-Team-Improvement-Lab-1.4"
SCRIPT = Path(__file__).resolve().parent.parent
IMPLEMENTATION = SCRIPT / "run_team_improvement_lab_v13.py"


def _load_current():
    spec = importlib.util.spec_from_file_location(
        "fsffl_gm3_team_improvement_current", IMPLEMENTATION
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to import Team Improvement implementation: {IMPLEMENTATION}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    current = _load_current()
    if current.MODEL_VERSION != EXPECTED_IMPLEMENTATION_VERSION:
        raise RuntimeError(
            f"Unexpected Team Improvement implementation: {current.MODEL_VERSION}"
        )
    current.main()


if __name__ == "__main__":
    main()
