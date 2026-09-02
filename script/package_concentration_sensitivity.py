#!/usr/bin/env python3
"""Package concentration sensitivity diagnostic.

This is NOT production scoring and NOT empirical calibration. It measures how
multi-asset package economics change under a bounded uncertainty set defined by
two already-existing FSFFL package curves:
- legacy mild curve from CONFIG.package_effective_value_weights
- GM2.2 stronger curve from GM22.package_weights

The midpoint is arithmetic interpolation between those inherited curves, not a
new fitted coefficient. Additive value is included only as a zero-concentration
benchmark.

Forced-cut and lineup effects are intentionally excluded here because they are
modeled elsewhere. This diagnostic isolates the residual package-concentration
question and therefore helps prevent double counting.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "data" / "fsffl_asset_values.json"

MODEL_VERSION = "FSFFL-Package-Concentration-Sensitivity-1.0"

LEGACY_MILD = [1.0, 0.92, 0.84, 0.78, 0.72]
GM22_STRONG = [1.0, 0.78, 0.62, 0.50, 0.42]
MIDPOINT = [round((a+b)/2.0, 6) for a,b in zip(LEGACY_MILD, GM22_STRONG)]
ADDITIVE = [1.0] * 5

CURVES = {
    "additive_benchmark": ADDITIVE,
    "legacy_mild_bound": LEGACY_MILD,
    "inherited_curve_midpoint": MIDPOINT,
    "gm22_strong_bound": GM22_STRONG,
}


def sf(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def load_values():
    raw = json.loads(ASSETS.read_text(encoding="utf-8"))
    out = {}
    for p in raw.get("players") or []:
        aid = f"player:{p.get('player_id')}"
        out[aid] = {
            "asset_id": aid,
            "name": p.get("name") or aid,
            "market_dynasty": sf(p.get("market_dynasty") or p.get("fsffl_value")),
        }
    for p in raw.get("picks") or []:
        aid = str(p.get("asset_id") or "")
        if aid:
            out[aid] = {
                "asset_id": aid,
                "name": p.get("name") or aid,
                "market_dynasty": sf(p.get("market_dynasty") or p.get("fsffl_value")),
            }
    return out


def tail_weight(curve, idx):
    if idx < len(curve):
        return curve[idx]
    # Preserve each inherited curve's historical floor behavior rather than
    # inventing a new extrapolation.
    if curve == LEGACY_MILD:
        return 0.72
    if curve == GM22_STRONG:
        return max(0.28, GM22_STRONG[-1] - 0.06 * (idx - len(GM22_STRONG) + 1))
    if curve == MIDPOINT:
        return (tail_weight(LEGACY_MILD, idx) + tail_weight(GM22_STRONG, idx)) / 2.0
    return 1.0


def effective(rows, curve):
    rows = sorted(rows, key=lambda x: x["market_dynasty"], reverse=True)
    parts = []
    total = 0.0
    for idx, row in enumerate(rows):
        w = tail_weight(curve, idx)
        val = row["market_dynasty"]
        eff = val * w
        total += eff
        parts.append({
            "asset_id": row["asset_id"],
            "name": row["name"],
            "raw_value": round(val, 2),
            "weight": round(w, 4),
            "effective_value": round(eff, 2),
        })
    return round(total, 2), parts


def evaluate_case(case, values):
    sent = [values[x] for x in case.get("sent_assets") or []]
    recv = [values[x] for x in case.get("received_assets") or []]
    raw_sent = sum(x["market_dynasty"] for x in sent)
    raw_recv = sum(x["market_dynasty"] for x in recv)
    raw_delta = raw_recv - raw_sent

    curves = {}
    for name, curve in CURVES.items():
        out_eff, out_parts = effective(sent, curve)
        in_eff, in_parts = effective(recv, curve)
        curves[name] = {
            "effective_sent": out_eff,
            "effective_received": in_eff,
            "effective_delta": round(in_eff - out_eff, 2),
            "concentration_residual_vs_additive": round((in_eff - out_eff) - raw_delta, 2),
            "sent_parts": out_parts,
            "received_parts": in_parts,
        }

    nonadditive = [curves[k]["effective_delta"] for k in curves if k != "additive_benchmark"]
    signs = {"positive" if x > 0 else "negative" if x < 0 else "zero" for x in nonadditive}
    return {
        "case_id": case.get("case_id"),
        "label": case.get("label"),
        "sent_assets": sent,
        "received_assets": recv,
        "raw_additive_sent": round(raw_sent, 2),
        "raw_additive_received": round(raw_recv, 2),
        "raw_additive_delta": round(raw_delta, 2),
        "curves": curves,
        "robust_nonadditive_sign": list(signs)[0].upper() if len(signs) == 1 else "SENSITIVE_TO_PRIOR_RANGE",
        "forced_cut_and_lineup_effects_included": False,
        "interpretation": (
            "This isolates package concentration only. Actual trade utility must combine it "
            "with separately modeled lineup, forced-cut, liquidity/resilience, and other "
            "non-duplicative effects exactly once."
        ),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()

    values = load_values()
    spec = json.loads(Path(args.cases).read_text(encoding="utf-8"))
    missing = sorted({
        aid
        for case in spec.get("cases") or []
        for aid in (case.get("sent_assets") or []) + (case.get("received_assets") or [])
        if aid not in values
    })
    if missing:
        raise SystemExit(f"missing asset values: {missing}")

    results = [evaluate_case(case, values) for case in spec.get("cases") or []]
    payload = {
        "model_version": MODEL_VERSION,
        "production_scoring_changed": False,
        "empirical_calibration_claimed": False,
        "curve_provenance": {
            "legacy_mild_bound": "existing CONFIG.package_effective_value_weights plus inherited tail floor",
            "gm22_strong_bound": "existing GM22.package_weights plus inherited tail decay/floor",
            "midpoint": "arithmetic midpoint of inherited bounds; not fitted",
            "additive_benchmark": "zero-concentration benchmark only",
        },
        "double_count_policy": {
            "forced_cut_cost_excluded": True,
            "lineup_replacement_effect_excluded": True,
            "same_source_rank_repricing_excluded": True,
            "market_dynasty_is_base_coordinate_not_repriced_by_rank": True,
        },
        "results": results,
    }
    Path(args.output).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
