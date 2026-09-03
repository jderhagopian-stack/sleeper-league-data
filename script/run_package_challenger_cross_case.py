#!/usr/bin/env python3
"""Evaluate the package-concentration challenger across a frozen cross-case set.

This uses already-confirmed 50k-simulation Shared Decision Utility 2.1 outputs
from source run 33648665812. It does not rerun football simulation and does not
change production. Because the challenger changes only the future primitive,
the active current/future weights can be recovered algebraically from each
frozen production score when liquidity/resilience were suppressed.

For each case:
1. infer the current/future weight split from the confirmed 2.1 score;
2. reconstruct raw negotiated trade-package dynasty delta from asset values
   frozen inside the regression fixture;
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
DEFAULT_CASES = ROOT / "data" / "model_validation" / "package_challenger_cross_case.json"

spec = importlib.util.spec_from_file_location(
    "package_curves_cross_case",
    ROOT / "script" / "package_concentration_sensitivity.py",
)
pkg = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(pkg)

MODEL_VERSION = "FSFFL-Package-Challenger-Cross-Case-1.1"


def sf(x):
    return float(x or 0.0)


def values(src):
    """Load the immutable market snapshot embedded in the fixture.

    The cross-case utility primitives are frozen outputs from a specific source
    run. Repricing only the package legs from today's mutable market catalog
    would convert ordinary snapshot drift into a fake package-concentration
    residual. The fixture therefore owns the asset values used by this test.
    """
    raw = src.get("frozen_asset_values") or {}
    out = {}
    for aid, row in raw.items():
        if isinstance(row, dict):
            name = row.get("name") or aid
            market = row.get("market_dynasty")
        else:
            name = aid
            market = row
        out[str(aid)] = {
            "asset_id": str(aid),
            "name": name,
            "market_dynasty": sf(market),
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
    if "expected_non_trade_future_value" in case:
        expected = round(sf(case["expected_non_trade_future_value"]), 2)
        assert non_trade_future == expected, (
            case["id"],
            "non_trade_future_value",
            non_trade_future,
            expected,
        )

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

    nonadditive_scores = [
        curves[name]["challenger_score"]
        for name in ("legacy_mild_bound", "inherited_curve_midpoint", "gm22_strong_bound")
    ]
    if all(x > 0 for x in nonadditive_scores):
        robust = "ROBUST_POSITIVE_ACROSS_PRIOR_RANGE"
    elif all(x < 0 for x in nonadditive_scores):
        robust = "ROBUST_NEGATIVE_ACROSS_PRIOR_RANGE"
    else:
        robust = "SENSITIVE_TO_PRIOR_RANGE"

    return {
        **case,
        "raw_trade_package_delta": raw_trade_delta,
        "non_trade_future_value_preserved": non_trade_future,
        "inferred_objective_weights": {
            "current": round(wc, 6),
            "future": round(wf, 6),
        },
        "curve_results": curves,
        "prior_range_decision_robustness": robust,
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
    catalog = values(src)
    if not catalog:
        raise SystemExit("frozen_asset_values is required for reproducible cross-case evaluation")
    if not src.get("source_market_value_ref"):
        raise SystemExit("source_market_value_ref is required for reproducible cross-case evaluation")
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

    robust_positive = [x["id"] for x in results if x["prior_range_decision_robustness"] == "ROBUST_POSITIVE_ACROSS_PRIOR_RANGE"]
    robust_negative = [x["id"] for x in results if x["prior_range_decision_robustness"] == "ROBUST_NEGATIVE_ACROSS_PRIOR_RANGE"]
    prior_sensitive = [x["id"] for x in results if x["prior_range_decision_robustness"] == "SENSITIVE_TO_PRIOR_RANGE"]

    payload = {
        "model_version": MODEL_VERSION,
        "source_run_id": src.get("source_run_id"),
        "source_model": src.get("source_model"),
        "source_market_value_ref": src.get("source_market_value_ref"),
        "market_values_frozen_with_fixture": True,
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
            "robust_positive_cases": robust_positive,
            "robust_negative_cases": robust_negative,
            "prior_sensitive_cases": prior_sensitive,
        },
        "results": results,
    }
    Path(args.output).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
