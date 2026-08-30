#!/usr/bin/env python3
"""Canonical governed GM 3.0 counterfactual entry point."""
from __future__ import annotations

from gm3 import application

MODEL_VERSION = "FSFFL-GM30-Governed-Entrypoint-1.1"


def main():
    application.run()


if __name__ == "__main__":
    main()
