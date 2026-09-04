#!/usr/bin/env python3
"""Augmented historical package calibration with robust incidental-FAAB trades.

Research only. Adds only FAAB trades that are (a) substantive exchanges rather than
liquidations/FAAB-only transfers and (b) stable through the 25% nuisance rail.
FAAB rows receive reduced evidence weight. No FAAB exchange rate or production
coefficient is created.
"""
from __future__ import annotations

import importlib.util
import json
import statistics
import sys
from collections import defaultdict
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


BASE = load_module(SCRIPT / "run_historical_pick_calibration_research.py", "augmented_base")

RATIOS = (0.00, 0.10, 0.25, 0.50)
PRIMARY_RATIO = 0.25
FAAB_RESEARCH_WEIGHT = 0.50
MODEL_VERSION = "FSFFL-Historical-Package-Augmented-FAAB-1.0"


def loadj(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def topology(counts):
    if len(counts) != 2:
        return "MULTI_PARTY"
    a, b = counts
    if a == 1 and b == 1:
        return "ONE_FOR_ONE"
    if a == 1 and b > 1:
        return "ONE_FOR_MANY"
    if a > 1 and b == 1:
        return "MANY_FOR_ONE"
    return "MANY_FOR_MANY"


def rank_from_aggregate(agg):
    names = [n for n in BASE.CURVES if agg.get(n, {}).get("weighted_mean_absolute_clearing_distance") is not None]
    return sorted(names, key=lambda n: agg[n]["weighted_mean_absolute_clearing_distance"])


def aggregate(rows):
    out = {}
    for name in BASE.CURVES:
        pairs = []
        wins = 0
        for row in rows:
            distance = (row.get("absolute_clearing_distance") or {}).get(name)
            if distance is None:
                continue
            weight = float(row.get("evidence_weight", 1.0))
            if weight <= 0:
                continue
            pairs.append((float(distance), weight))
            if row.get("lowest_distance_curve") == name:
                wins += 1
        if not pairs:
            out[name] = {"n": 0, "weighted_mean_absolute_clearing_distance": None, "wins_lowest_distance": 0}
            continue
        denom = sum(w for _, w in pairs)
        out[name] = {
            "n": len(pairs),
            "weighted_mean_absolute_clearing_distance": round(sum(v*w for v,w in pairs) / denom, 4),
            "wins_lowest_distance": wins,
        }
    return out


def make_faab_row(raw, ratio):
    rail = (raw.get("rails") or {}).get(str(ratio))
    if not rail:
        return None
    counts = raw.get("principal_side_counts") or [0, 0]
    vals = []
    faab_side_values = rail.get("faab_side_values") or [0.0, 0.0]
    for i, side in enumerate(raw.get("principal_side_values") or []):
        augmented = list(side)
        if i < len(faab_side_values) and float(faab_side_values[i]) > 0:
            augmented.append(float(faab_side_values[i]))
        vals.append(augmented)
    distances = {k: float(v) for k, v in (rail.get("absolute_clearing_distance") or {}).items()}
    if len(vals) != 2 or not distances:
        return None
    return {
        "transaction_id": str(raw.get("transaction_id") or ""),
        "season": int(raw.get("season") or 0),
        "created": int(raw.get("created") or 0),
        "created_utc": raw.get("created_utc"),
        "topology": topology(counts),
        "asset_family": "FAAB_INCIDENTAL_SUBSTANTIVE_EXCHANGE",
        "package_counts": counts,
        "side_values": vals,
        "absolute_clearing_distance": distances,
        "lowest_distance_curve": min(distances, key=distances.get),
        "evidence_weight": FAAB_RESEARCH_WEIGHT,
        "evidence_source": "ROBUST_INCIDENTAL_FAAB_REDUCED_WEIGHT",
        "faab_nuisance_ratio": ratio,
        "research_only": True,
    }


def challenger(train_rows, validate_rows):
    train = [r for r in train_rows if r.get("topology") != "ONE_FOR_ONE"]
    validate = [r for r in validate_rows if r.get("topology") != "ONE_FOR_ONE"]
    if len(train) < 20 or len(validate) < 8:
        return {"status": "NOT_JUSTIFIED_SAMPLE_TOO_SMALL", "train_n": len(train), "validation_n": len(validate)}
    candidates = []
    for decay in (0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95):
        curve = [1.0, decay, decay**2, decay**3, decay**4]
        weighted = []
        for r in train:
            d = abs(BASE.effective(r["side_values"][0], curve) - BASE.effective(r["side_values"][1], curve))
            weighted.append((d, float(r.get("evidence_weight", 1.0))))
        denom = sum(w for _, w in weighted)
        mean = sum(v*w for v,w in weighted) / denom
        candidates.append((mean, decay, curve))
    candidates.sort()
    train_mean, decay, curve = candidates[0]
    hold = []
    for r in validate:
        d = abs(BASE.effective(r["side_values"][0], curve) - BASE.effective(r["side_values"][1], curve))
        hold.append((d, float(r.get("evidence_weight", 1.0))))
    denom = sum(w for _, w in hold)
    return {
        "status": "EXPLORATORY_CHALLENGER_ONLY",
        "selected_decay_on_earlier_train_only": decay,
        "curve": [round(x, 6) for x in curve],
        "train_n": len(train),
        "validation_n": len(validate),
        "train_weighted_mean_absolute_clearing_distance": round(train_mean, 4),
        "validation_weighted_mean_absolute_clearing_distance": round(sum(v*w for v,w in hold)/denom, 4),
        "production_authority": False,
    }


def managers_by_trade():
    out = {}
    for t in loadj(DATA / "trade_ledger.json", []) or []:
        tid = str(t.get("transaction_id") or "")
        names = sorted({str(s.get("manager") or s.get("team_name") or s.get("roster_id") or "unknown") for s in (t.get("sides") or [])})
        out[tid] = names
    return out


def main():
    base = loadj(OUT / "historical_package_concentration_expanded.json", {}) or {}
    faab = loadj(OUT / "historical_faab_nuisance_sensitivity.json", {}) or {}
    arche = loadj(OUT / "historical_faab_archetypes.json", {}) or {}

    base_rows = list(base.get("trades") or [])
    arche_map = {str(r.get("transaction_id") or ""): r.get("archetype") for r in (arche.get("trades") or [])}
    robust_raw = [
        r for r in (faab.get("trades") or [])
        if r.get("winner_stable_through_25pct")
        and arche_map.get(str(r.get("transaction_id") or "")) == "INCIDENTAL_FAAB_IN_SUBSTANTIVE_EXCHANGE"
    ]

    results_by_ratio = {}
    manager_map = managers_by_trade()
    for ratio in RATIOS:
        added = [make_faab_row(r, ratio) for r in robust_raw]
        added = [r for r in added if r]
        augmented = sorted(base_rows + added, key=lambda r: (int(r.get("created") or 0), str(r.get("transaction_id") or "")))
        unequal = [r for r in augmented if r.get("topology") != "ONE_FOR_ONE"]
        split = max(1, int(len(augmented) * 0.70))
        train, validate = augmented[:split], augmented[split:]
        agg = aggregate(unequal)
        ranking = rank_from_aggregate(agg)

        by_topology = {}
        for topo in ("ONE_FOR_MANY", "MANY_FOR_ONE", "MANY_FOR_MANY"):
            rows = [r for r in unequal if r.get("topology") == topo]
            a = aggregate(rows)
            by_topology[topo] = {"n": len(rows), "ranking": rank_from_aggregate(a), "aggregate": a}

        leaveout = []
        managers = sorted({m for r in augmented for m in manager_map.get(str(r.get("transaction_id") or ""), [])})
        for manager in managers:
            kept = [r for r in unequal if manager not in manager_map.get(str(r.get("transaction_id") or ""), [])]
            a = aggregate(kept)
            rk = rank_from_aggregate(a)
            leaveout.append({"manager": manager, "n": len(kept), "ranking": rk, "changed": rk != ranking})

        results_by_ratio[str(ratio)] = {
            "augmented_trade_count": len(augmented),
            "added_robust_incidental_faab_count": len(added),
            "unequal_package_count": len(unequal),
            "aggregate_unequal": agg,
            "ranking": ranking,
            "temporal": {
                "split_method": "time_ordered_earliest_70_percent_train_latest_30_percent_validate",
                "train_n": len(train),
                "validation_n": len(validate),
                "train_ranking": rank_from_aggregate(aggregate([r for r in train if r.get("topology") != "ONE_FOR_ONE"])),
                "validation_ranking": rank_from_aggregate(aggregate([r for r in validate if r.get("topology") != "ONE_FOR_ONE"])),
                "challenger": challenger(train, validate),
            },
            "topology_breakdown": by_topology,
            "manager_leaveout_ranking_changes": [r["manager"] for r in leaveout if r["changed"]],
            "manager_leaveout": leaveout,
        }

    rankings = [tuple(results_by_ratio[str(r)]["ranking"]) for r in RATIOS]
    primary = results_by_ratio[str(PRIMARY_RATIO)]
    output = {
        "model_version": MODEL_VERSION,
        "research_only": True,
        "production_authority": False,
        "production_behavior_changed": False,
        "production_prior_changed": False,
        "faab_hard_exchange_rate_assigned": False,
        "faab_research_weight": FAAB_RESEARCH_WEIGHT,
        "primary_faab_nuisance_ratio": PRIMARY_RATIO,
        "base_primary_trade_count": len(base_rows),
        "robust_incidental_faab_candidates": len(robust_raw),
        "augmented_primary_trade_count": primary["augmented_trade_count"],
        "augmented_unequal_package_count": primary["unequal_package_count"],
        "ranking_stable_across_all_faab_rails": len(set(rankings)) == 1,
        "ranking_by_faab_rail": {str(r): results_by_ratio[str(r)]["ranking"] for r in RATIOS},
        "primary_ratio_result": primary,
        "all_ratio_results": results_by_ratio,
    }
    (OUT / "historical_package_calibration_augmented_faab.json").write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(json.dumps({
        "base_primary_trade_count": len(base_rows),
        "robust_incidental_faab_candidates": len(robust_raw),
        "augmented_primary_trade_count": primary["augmented_trade_count"],
        "augmented_unequal_package_count": primary["unequal_package_count"],
        "ranking_stable_across_all_faab_rails": output["ranking_stable_across_all_faab_rails"],
        "ranking_by_faab_rail": output["ranking_by_faab_rail"],
        "train_ranking": primary["temporal"]["train_ranking"],
        "validation_ranking": primary["temporal"]["validation_ranking"],
        "challenger": primary["temporal"]["challenger"],
        "manager_leaveout_ranking_changes": primary["manager_leaveout_ranking_changes"],
        "topology_rankings": {k:v["ranking"] for k,v in primary["topology_breakdown"].items()},
    }, indent=2))

    assert output["production_authority"] is False
    assert output["production_behavior_changed"] is False
    assert output["faab_hard_exchange_rate_assigned"] is False


if __name__ == "__main__":
    main()
