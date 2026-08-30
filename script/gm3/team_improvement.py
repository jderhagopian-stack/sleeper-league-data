#!/usr/bin/env python3
"""Stable GM3 Team Improvement application-area entry point.

The current authoritative implementation is Team Improvement Lab 1.4. Historical
v11-v13 wrapper files remain for reproducibility; production callers use this
stable entry point so implementation filenames do not become application
authority.
"""
from __future__ import annotations

import run_team_improvement_lab_v13 as current

MODEL_VERSION = "FSFFL-GM-Team-Improvement-Application-1.0"
EXPECTED_IMPLEMENTATION_VERSION = "FSFFL-GM-Team-Improvement-Lab-1.4"


def main():
    if current.MODEL_VERSION != EXPECTED_IMPLEMENTATION_VERSION:
        raise RuntimeError(
            f"Unexpected Team Improvement implementation: {current.MODEL_VERSION}"
        )
    current.main()


if __name__ == "__main__":
    main()
