#!/usr/bin/env python3
"""Evaluate the package-concentration challenger across a frozen cross-case set.

This uses already-confirmed 50k-simulation Shared Decision Utility 2.1 outputs
from source run 33648665812. It does not rerun football simulation and does not
change production. Because the challenger changes only the future primitive,
the active current/future weights can be recovered algebraically from each
frozen production score when liquidity/resilience were suppressed.

For each case:
1. infer the current/future weight split from the confirmed 2.1 score;
2. reconstruct raw negotiated trade-package dynasty delta from frozen asset IDs
   using the current canonical market-value file;
3. treat any difference between production future primitive and raw trade delta
   as non-trade future effects (e.g. forced cuts) and preserve it exactly once;
4. replace only raw trade-package additivity with each inherited package curve;
5. leave one-for-one trades unchanged by construction.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "data" / "fsffl_asset_values.json"
DEFAULT_CASES = ROOT / "data" / "model_validation" / "package_challenger_cross_case.json"

spec = importlib.util.spec_from_file_location(
    "package_curves_cross_case",
    ROOT / "script" / "package_concentration_sensitivity.py",
)
pkg = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(pkg)

MODEL_VERSION = "FSFFL-Package-Challenger-Cross-Case-1.0"


def sf(x):
    return float(x or 0.0)


def values():
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


def inferred_future_weight(current, future, score):
    den = future - current
    if abs(den) < 1e-9:
        return None
    w = (score - current) / den
    if not (0.0 <= w <= 1.0):
        raise ValueError(f"inferred future weight outside [0,1]: {w}")
    return w


def effective(rows, curve):
    return pkg.effective(rows, curve)[0]


def evaluate(case, catalog):
    sent = [catalog[x] for x in case["sent_assets"]]
    received = [catalog[x] for x in case["received_assets"]]
    raw_trade_delta = round(
        sum(x["market_dynasty"] for x in received)
        - sum(x["market_dynasty"] for x in sent),
        2,
    )
    current = sf(case["current_primitive"])
    future = sf(case["future_primitive"])
    score = sf(case["production_score"])
    wf = inferred_future_weight(current, future, score)
    wc = 1.0 - wf
    non_trade_future = round(future - raw_trade_delta, 2)

    curves = {}
    for name, curve in pkg.CURVES.items():
        eff_trade = round(effective(received, curve) - effective(sent, curve), 2)
        challenger_future = round(eff_trade + non_trade_future, 2)
        challenger_score = round(wc * current + wf * challenger_future, 2)
        curves[name] = {
            "effective_trade_delta": eff_trade,
            "non_trade_future_value_preserved": non_trade_future,
            "challenger_future_primitive": challenger_future,
            "challenger_score": challenger_score,
            "score_delta_vs_production": round(challenger_score - score, 2),
        }

    return {
        **case,
        "raw_trade_package_delta": raw_trade_delta,
        "non_trade_future_value_preserved": non_trade_future,
        "inferred_objective_weights": {
            "current": round(wc, 6),
            "future": round(wf, 6),
        },
        "curve_results": curves,
        "one_for_one": len(sent) == 1 and len(received) == 1,
        "fragmentation": len(sent) == 1 and len(received) > 1,
        "consolidation": len(sent) > 1 and len(received) == 1,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default=str(DEFAULT_CASES))
    ap.add_argument("--output", required=True)
    args = ap.parse_args()

    src = json.loads(Path(args.cases).read_text(encoding="utf-8"))
    catalog = values()
    missing = sorted({
        aid
        for case in src.get("cases") or []
        for aid in case["sent_assets"] + case["received_assets"]
        if aid not in catalog
    })
    if missing:
        raise SystemExit(f"missing canonical market values: {missing}")

    results = [evaluate(case, catalog) for case in src.get("cases") or []]

    one_for_one = [x for x in results if x["one_for_one"]]
    fragmentation = [x for x in results if x["fragmentation"]]
    consolidation = [x for x in results if x["consolidation"]]

    for row in one_for_one:
        for name, cr in row["curve_results"].items():
            assert cr["score_delta_vs_production"] == 0.0, (row["id"], name, cr)
    for row in fragmentation:
        for name in ("legacy_mild_bound", "inherited_curve_midpoint", "gm22_strong_bound"):
            assert row["curve_results"][name]["score_delta_vs_production"] < 0.0, (row["id"], name)
    for row in consolidation:
        for name in ("legacy_mild_bound", "inherited_curve_midpoint", "gm22_strong_bound"):
            assert row["curve_results"][name]["score_delta_vs_production"] > 0.0, (row["id"], name)

    payload = {
        "model_version": MODEL_VERSION,
        "source_run_id": src.get("source_run_id"),
        "source_model": src.get("source_model"),
        "production_behavior_changed": False,
        "empirical_coefficient_fit_performed": False,
        "weight_inference_note": (
            "Current/future weights are algebraically recovered from frozen confirmed scores "
            "for challenger sensitivity only; no new objective weights are introduced."
        ),
        "double_count_policy": {
            "future_trade_package_raw_value_replaced_not_added": True,
            "non_trade_future_effects_preserved_exactly_once": True,
            "current_lineup_and_simulation_block_unchanged": True,
            "same_source_rank_repricing_used": False,
        },
        "summary": {
            "case_count": len(results),
            "one_for_one_cases": len(one_for_one),
            "fragmentation_cases": len(fragmentation),
            "consolidation_cases": len(consolidation),
            "one_for_one_all_unchanged": True,
            "fragmentation_all_worsen_across_nonadditive_bounds": True,
            "consolidation_all_improve_across_nonadditive_bounds": True,
        },
        "results": results,
    }
    Path(args.output).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
