#!/usr/bin/env python3
"""Test whether historical package findings depend on the FAAB evidence weight.

Research only. The existing 0.50 FAAB evidence weight is descriptive rather than
empirically identified. This diagnostic reruns the augmented historical package
comparison across bounded evidence-weight and FAAB nuisance rails. It cannot alter
production authority or assign a hard FAAB exchange rate.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "script"
DATA = ROOT / "data"
OUT = DATA / "audit"
OUT.mkdir(parents=True, exist_ok=True)


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to import {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


AUG = load_module(SCRIPT / "run_augmented_historical_package_calibration.py", "faab_weight_aug")

EVIDENCE_WEIGHTS = (0.25, 0.50, 0.75, 1.00)
NUISANCE_RATIOS = AUG.RATIOS
MODEL_VERSION = "FSFFL-Historical-FAAB-Evidence-Weight-Sensitivity-1.0"


def loadj(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def margin_strong_vs_center(agg):
    strong = (agg.get("strong") or {}).get("weighted_mean_absolute_clearing_distance")
    center = (agg.get("center") or {}).get("weighted_mean_absolute_clearing_distance")
    if strong is None or center is None:
        return None
    return round(float(center) - float(strong), 4)


def main() -> None:
    base = loadj(OUT / "historical_package_concentration_expanded.json", {}) or {}
    faab = loadj(OUT / "historical_faab_nuisance_sensitivity.json", {}) or {}
    arche = loadj(OUT / "historical_faab_archetypes.json", {}) or {}

    base_rows = list(base.get("trades") or [])
    arche_map = {
        str(r.get("transaction_id") or ""): r.get("archetype")
        for r in (arche.get("trades") or [])
    }
    robust_raw = [
        r for r in (faab.get("trades") or [])
        if r.get("winner_stable_through_25pct")
        and arche_map.get(str(r.get("transaction_id") or ""))
        == "INCIDENTAL_FAAB_IN_SUBSTANTIVE_EXCHANGE"
    ]

    grid = {}
    all_rankings = []
    all_train_rankings = []
    all_validation_rankings = []
    all_margins = []

    for weight in EVIDENCE_WEIGHTS:
        by_ratio = {}
        for ratio in NUISANCE_RATIOS:
            added = []
            for raw in robust_raw:
                row = AUG.make_faab_row(raw, ratio)
                if row is None:
                    continue
                row["evidence_weight"] = weight
                row["evidence_source"] = "ROBUST_INCIDENTAL_FAAB_SENSITIVITY_WEIGHT"
                added.append(row)

            augmented = sorted(
                base_rows + added,
                key=lambda r: (int(r.get("created") or 0), str(r.get("transaction_id") or "")),
            )
            unequal = [r for r in augmented if r.get("topology") != "ONE_FOR_ONE"]
            split = max(1, int(len(augmented) * 0.70))
            train = [r for r in augmented[:split] if r.get("topology") != "ONE_FOR_ONE"]
            validate = [r for r in augmented[split:] if r.get("topology") != "ONE_FOR_ONE"]

            aggregate = AUG.aggregate(unequal)
            train_aggregate = AUG.aggregate(train)
            validation_aggregate = AUG.aggregate(validate)
            ranking = AUG.rank_from_aggregate(aggregate)
            train_ranking = AUG.rank_from_aggregate(train_aggregate)
            validation_ranking = AUG.rank_from_aggregate(validation_aggregate)
            margin = margin_strong_vs_center(aggregate)

            all_rankings.append(tuple(ranking))
            all_train_rankings.append(tuple(train_ranking))
            all_validation_rankings.append(tuple(validation_ranking))
            if margin is not None:
                all_margins.append(margin)

            by_ratio[str(ratio)] = {
                "added_robust_incidental_faab_count": len(added),
                "augmented_trade_count": len(augmented),
                "unequal_package_count": len(unequal),
                "ranking": ranking,
                "train_ranking": train_ranking,
                "validation_ranking": validation_ranking,
                "strong_vs_center_distance_advantage": margin,
                "aggregate_unequal": aggregate,
            }
        grid[str(weight)] = by_ratio

    expected = ("strong", "center", "mild", "additive")
    output = {
        "model_version": MODEL_VERSION,
        "research_only": True,
        "production_authority": False,
        "production_behavior_changed": False,
        "production_prior_changed": False,
        "faab_hard_exchange_rate_assigned": False,
        "evidence_weight_is_empirically_identified": False,
        "descriptive_primary_weight": AUG.FAAB_RESEARCH_WEIGHT,
        "evidence_weight_rails": list(EVIDENCE_WEIGHTS),
        "faab_nuisance_rails": list(NUISANCE_RATIOS),
        "robust_incidental_faab_candidates": len(robust_raw),
        "ranking_stable_across_weight_and_nuisance_grid": len(set(all_rankings)) == 1,
        "train_ranking_stable_across_grid": len(set(all_train_rankings)) == 1,
        "validation_ranking_stable_across_grid": len(set(all_validation_rankings)) == 1,
        "expected_order_holds_across_grid": all(r == expected for r in all_rankings),
        "strong_beats_center_across_grid": bool(all_margins) and all(m > 0 for m in all_margins),
        "minimum_strong_vs_center_distance_advantage": min(all_margins) if all_margins else None,
        "maximum_strong_vs_center_distance_advantage": max(all_margins) if all_margins else None,
        "grid": grid,
        "interpretation_guardrail": (
            "Stability across these rails supports robustness to the descriptive FAAB evidence weight; "
            "it does not identify the correct FAAB exchange rate, evidence weight, or point-optimal package curve."
        ),
    }

    path = OUT / "historical_faab_evidence_weight_sensitivity.json"
    path.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({k: output[k] for k in (
        "robust_incidental_faab_candidates",
        "ranking_stable_across_weight_and_nuisance_grid",
        "train_ranking_stable_across_grid",
        "validation_ranking_stable_across_grid",
        "expected_order_holds_across_grid",
        "strong_beats_center_across_grid",
        "minimum_strong_vs_center_distance_advantage",
        "maximum_strong_vs_center_distance_advantage",
    )}, indent=2))

    assert output["production_authority"] is False
    assert output["production_behavior_changed"] is False
    assert output["production_prior_changed"] is False
    assert output["faab_hard_exchange_rate_assigned"] is False


if __name__ == "__main__":
    main()
