#!/usr/bin/env python3
"""Non-production challenger for residual package-concentration economics.

The production Shared Decision Utility remains unchanged.

This challenger asks one controlled question: what happens if the future-value
primitive uses a governed nonlinear package-effective dynasty delta instead of
raw additive dynasty delta?

Important de-duplication rule:
- package concentration REPLACES the raw additive future primitive;
- it is never added as a fifth utility channel or stacked on top of raw future;
- current-season lineup/simulator/forced-cut effects remain in the current block;
- same-source market rank is not used to reprice the base market value.

Therefore this challenger tests a residual aggregation hypothesis without
counting the same dynasty-value coordinate twice.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "script"

def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod

BASE = _load(SCRIPT / "decision_utility.py", "decision_utility_package_challenger_base")
PACKAGE = _load(SCRIPT / "package_concentration_sensitivity.py", "package_concentration_curves")

MODEL_VERSION = "FSFFL-Decision-Utility-Package-Challenger-1.0"


def sf(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _rows(strategic: Dict[str, Any], key: str):
    return [
        {
            "asset_id": str(row.get("asset_id") or ""),
            "name": row.get("name") or row.get("asset_id"),
            "market_dynasty": sf(row.get("market_dynasty")),
        }
        for row in (strategic.get(key) or [])
        if row.get("asset_id")
    ]


def package_future_value(strategic: Dict[str, Any], curve_name: str) -> Dict[str, Any]:
    if curve_name not in PACKAGE.CURVES:
        raise ValueError(f"unknown package curve: {curve_name}")

    sent = _rows(strategic, "sent")
    received = _rows(strategic, "received")
    curve = PACKAGE.CURVES[curve_name]

    effective_sent, sent_parts = PACKAGE.effective(sent, curve)
    effective_received, received_parts = PACKAGE.effective(received, curve)
    effective_delta = round(effective_received - effective_sent, 2)
    raw_delta = sf(strategic.get("market_dynasty_delta"))
    residual = round(effective_delta - raw_delta, 2)

    return {
        "curve_name": curve_name,
        "raw_additive_future_value": round(raw_delta, 2),
        "package_effective_future_value": effective_delta,
        "concentration_residual_vs_additive": residual,
        "sent_parts": sent_parts,
        "received_parts": received_parts,
        "replacement_not_additive": True,
        "same_source_rank_repricing_used": False,
        "forced_cut_or_lineup_adjustment_in_this_block": False,
    }


def score(sim: Dict[str, Any], curve_name: str) -> Dict[str, Any]:
    base = BASE.score(sim)
    package = package_future_value(sim.get("strategic") or {}, curve_name)

    weights = base["objective_weights"]
    primitives = dict(base["primitive_blocks"])

    # The challenger changes exactly one primitive: future. It does not create
    # a fifth channel or add a premium/penalty on top of raw market dynasty.
    primitives["future"] = package["package_effective_future_value"]

    components = {
        key: sf(weights.get(key)) * sf(primitives.get(key))
        for key in ("current", "future", "liquidity", "resilience")
    }
    total = sum(components.values())

    return {
        "score": round(total, 2),
        "components": {k: round(v, 2) for k, v in components.items()},
        "primitive_blocks": {k: round(sf(v), 2) for k, v in primitives.items()},
        "objective_weights": dict(weights),
        "base_production_score": base["score"],
        "score_delta_vs_production": round(total - sf(base["score"]), 2),
        "package_concentration": package,
        "model_version": MODEL_VERSION,
        "production_scoring_changed": False,
        "production_decision_utility_version": base["model_version"],
        "authority": "CHALLENGER_ONLY_NON_PRODUCTION",
        "double_count_policy": {
            "raw_future_replaced_not_summed": True,
            "new_utility_channel_created": False,
            "lineup_and_forced_cut_effects_left_in_current_block": True,
            "same_source_rank_repricing_forbidden": True,
        },
    }
