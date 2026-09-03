#!/usr/bin/env python3
"""Governed package-concentration transform for FUTURE ASSET VALUE.

This module applies a bounded provisional prior to negotiated multi-asset trade
packages. It replaces raw additive package value inside the existing FUTURE
ASSET VALUE channel. It does not create a fifth channel.

Automatic roster cuts and other non-trade future effects are preserved exactly
once outside the package transform.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List

ROOT = Path(__file__).resolve().parents[1]
PRIOR_PATH = ROOT / "data/gm/package_concentration_prior.json"
MODEL_VERSION = "FSFFL-Package-Concentration-1.0"


def sf(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def load_prior():
    return json.loads(PRIOR_PATH.read_text(encoding="utf-8"))


def _rows(strategic: Dict[str, Any], key: str) -> List[Dict[str, Any]]:
    out = []
    for row in strategic.get(key) or []:
        aid = str(row.get("asset_id") or "")
        if not aid:
            continue
        out.append({
            "asset_id": aid,
            "name": row.get("name") or aid,
            "market_dynasty": sf(row.get("market_dynasty")),
        })
    return out


def trade_asset_ids(sim: Dict[str, Any]):
    ids = set()
    for action in sim.get("trade_actions") or []:
        if str(action.get("type") or "").lower() != "trade":
            continue
        for pid in action.get("players") or []:
            ids.add(f"player:{pid}")
        for pick in action.get("picks") or []:
            ids.add(str(pick))
    return ids


def tail_weight(prior: Dict[str, Any], curve_name: str, idx: int) -> float:
    curve = prior["curves"][curve_name]
    if idx < len(curve):
        return sf(curve[idx], 1.0)
    if curve_name == "mild":
        return 0.72
    if curve_name == "strong":
        return max(0.28, sf(curve[-1]) - 0.06 * (idx - len(curve) + 1))
    if curve_name == "center":
        return (tail_weight(prior, "mild", idx) + tail_weight(prior, "strong", idx)) / 2.0
    return 1.0


def effective(rows: Iterable[Dict[str, Any]], prior: Dict[str, Any], curve_name: str):
    ordered = sorted(rows, key=lambda x: sf(x.get("market_dynasty")), reverse=True)
    parts = []
    total = 0.0
    for idx, row in enumerate(ordered):
        raw = sf(row.get("market_dynasty"))
        weight = tail_weight(prior, curve_name, idx)
        eff = raw * weight
        total += eff
        parts.append({
            "asset_id": row.get("asset_id"),
            "name": row.get("name"),
            "raw_value": round(raw, 2),
            "weight": round(weight, 4),
            "effective_value": round(eff, 2),
        })
    return round(total, 2), parts


def transform_future_value(sim: Dict[str, Any], curve_name: str = "center") -> Dict[str, Any]:
    prior = load_prior()
    if curve_name not in prior.get("curves", {}):
        raise ValueError(f"unknown package concentration curve {curve_name!r}")

    strategic = sim.get("strategic") or {}
    all_sent = _rows(strategic, "sent")
    all_received = _rows(strategic, "received")

    negotiated_ids = trade_asset_ids(sim)
    trade_filter_applied = bool(negotiated_ids)
    if trade_filter_applied:
        sent = [x for x in all_sent if x["asset_id"] in negotiated_ids]
        received = [x for x in all_received if x["asset_id"] in negotiated_ids]
    else:
        # Compatibility path for synthetic/legacy callers without explicit
        # trade_actions. This preserves previous behavior but is exposed.
        sent = all_sent
        received = all_received

    raw_trade_delta = round(
        sum(sf(x["market_dynasty"]) for x in received)
        - sum(sf(x["market_dynasty"]) for x in sent),
        2,
    )
    raw_total_future = sf(strategic.get("market_dynasty_delta"))
    non_trade_future = round(raw_total_future - raw_trade_delta, 2)

    eff_sent, sent_parts = effective(sent, prior, curve_name)
    eff_received, received_parts = effective(received, prior, curve_name)
    effective_trade_delta = round(eff_received - eff_sent, 2)
    effective_total_future = round(effective_trade_delta + non_trade_future, 2)

    return {
        "model_version": MODEL_VERSION,
        "family_id": prior.get("family_id"),
        "authority_mode": prior.get("authority_mode"),
        "empirically_calibrated": bool(prior.get("empirically_calibrated")),
        "curve_name": curve_name,
        "raw_additive_future_value": round(raw_total_future, 2),
        "raw_trade_package_future_value": raw_trade_delta,
        "non_trade_future_value_preserved": non_trade_future,
        "package_effective_trade_future_value": effective_trade_delta,
        "package_effective_future_value": effective_total_future,
        "concentration_residual_vs_additive": round(effective_total_future - raw_total_future, 2),
        "sent_parts": sent_parts,
        "received_parts": received_parts,
        "trade_asset_filter_applied": trade_filter_applied,
        "automatic_cuts_excluded_from_package_concentration": trade_filter_applied,
        "non_trade_future_effects_preserved_exactly_once": True,
        "replacement_not_additive": True,
        "same_source_rank_repricing_used": False,
        "new_utility_channel_created": False,
        "commercial_provenance": prior.get("commercial_provenance") or {},
    }


def sensitivity(sim: Dict[str, Any]) -> Dict[str, Any]:
    rows = {
        name: transform_future_value(sim, name)
        for name in ("mild", "center", "strong")
    }
    return {
        "mild_future": rows["mild"]["package_effective_future_value"],
        "center_future": rows["center"]["package_effective_future_value"],
        "strong_future": rows["strong"]["package_effective_future_value"],
        "rows": rows,
    }
