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


def _trade_asset_ids(sim: Dict[str, Any]):
    ids = set()
    for action in sim.get("trade_actions") or []:
        if str(action.get("type") or "").lower() != "trade":
            continue
        for pid in action.get("players") or []:
            ids.add(f"player:{pid}")
        for pick in action.get("picks") or []:
            ids.add(str(pick))
    return ids


def package_future_value(sim: Dict[str, Any], curve_name: str) -> Dict[str, Any]:
    if curve_name not in PACKAGE.CURVES:
        raise ValueError(f"unknown package curve: {curve_name}")

    strategic = sim.get("strategic") or {}
    sent = _rows(strategic, "sent")
    received = _rows(strategic, "received")

    # Strategic summaries can include automatic roster cuts. Those losses are
    # already modeled by roster legalization/current utility and must not also
    # be treated as pieces of the negotiated trade package.
    trade_ids = _trade_asset_ids(sim)
    trade_filter_applied = bool(trade_ids)
    if trade_filter_applied:
        sent = [x for x in sent if x["asset_id"] in trade_ids]
        received = [x for x in received if x["asset_id"] in trade_ids]

    curve = PACKAGE.CURVES[curve_name]

    effective_sent, sent_parts = PACKAGE.effective(sent, curve)
    effective_received, received_parts = PACKAGE.effective(received, curve)
    effective_trade_delta = round(effective_received - effective_sent, 2)
    raw_trade_delta = round(
        sum(x["market_dynasty"] for x in received)
        - sum(x["market_dynasty"] for x in sent),
        2,
    )
    raw_total_future = sf(strategic.get("market_dynasty_delta"))
    non_trade_future_delta = round(raw_total_future - raw_trade_delta, 2)
    effective_total_future = round(effective_trade_delta + non_trade_future_delta, 2)
    residual = round(effective_total_future - raw_total_future, 2)

    return {
        "curve_name": curve_name,
        "raw_additive_future_value": round(raw_total_future, 2),
        "raw_trade_package_future_value": raw_trade_delta,
        "non_trade_future_value_preserved": non_trade_future_delta,
        "package_effective_trade_future_value": effective_trade_delta,
        "package_effective_future_value": effective_total_future,
        "concentration_residual_vs_additive": residual,
        "sent_parts": sent_parts,
        "received_parts": received_parts,
        "replacement_not_additive": True,
        "same_source_rank_repricing_used": False,
        "forced_cut_or_lineup_adjustment_in_this_block": False,
        "trade_asset_filter_applied": trade_filter_applied,
        "automatic_cuts_excluded_from_package_concentration": trade_filter_applied,
        "non_trade_future_effects_preserved_exactly_once": True,
    }


def score(sim: Dict[str, Any], curve_name: str) -> Dict[str, Any]:
    base = BASE.score(sim)
    package = package_future_value(sim, curve_name)

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
