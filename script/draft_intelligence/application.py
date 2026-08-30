#!/usr/bin/env python3
"""Draft Intelligence application orchestration.

Owns prospect-input assembly, feature enrichment, prospect evaluation and the
published rookie/prospect board. GM3 consumes this application output; GM3 does
not own the prospect model.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import build_gm30_prospect_inputs as inputs
import build_gm30_prospect_features as features
import build_gm30_prospect_engine as engine

MODEL_VERSION = "FSFFL-Draft-Intelligence-Application-1.0"
OUT = Path("data/gm")


def run():
    inputs.main()
    features.main()
    engine.main()
    radar = OUT / "gm30_prospect_radar.json"
    board = OUT / "prospect_board.json"
    if radar.exists():
        shutil.copyfile(radar, board)


def architecture():
    return {
        "model_version": MODEL_VERSION,
        "application": "Draft Intelligence",
        "prospect_model_owned_by_gm3": False,
        "gm3_consumes_draft_intelligence_output": True,
        "market_rank_is_comparison_not_prospect_feature": True,
    }


if __name__ == "__main__":
    run()
