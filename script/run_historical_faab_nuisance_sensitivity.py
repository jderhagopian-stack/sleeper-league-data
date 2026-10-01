#!/usr/bin/env python3
"""Research-only FAAB nuisance sensitivity for historical package calibration.

FAAB is not assigned a hard FSFFL exchange rate. Instead, for bilateral historical
trades with reconstructable principal assets, total FAAB consideration is treated as
an unknown side component bounded as a fraction of the smallest principal asset in
the trade. A trade is considered FAAB-robust when the identity of the lowest clearing-
distance concentration curve is unchanged across the full nuisance rail.

This is calibration research only and cannot change production authority.
"""
from __future__ import annotations

import importlib.util
import json
import statistics
import sys
from collections import Counter
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


BASE = load_module(SCRIPT / "run_historical_pick_calibration_research.py", "faab_base_research")
PICK = load_module(SCRIPT / "historical_pick_coordinate.py", "faab_pick_coordinate")
STATE = load_module(SCRIPT / "fsffl_historical_state_provider.py", "faab_history_state")

MODEL_VERSION = "FSFFL-Historical-FAAB-Nuisance-1.0"
# Ratios are deliberately dimensionless. They do not claim that FAAB actually has
# any one of these values; they ask whether the package conclusion survives even if
# the total FAAB side consideration were this material relative to the smallest
# principal asset in the transaction.
NUISANCE_RATIOS = (0.00, 0.10, 0.25, 0.50)
PRIMARY_ROBUSTNESS_RATIO = 0.25


def loadj(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def side_principal_values(side, player_values, coord_map):
    vals = []
    missing = []
    for p in side.get("sent_players") or []:
        pid = str(p.get("player_id") or "")
        if pid not in player_values:
            missing.append("player:" + pid)
        else:
            vals.append(float(player_values[pid]))
    for p in side.get("sent_picks") or []:
        key = "pick:%d:R%d:orig%d" % (
            BASE.si(p.get("season")),
            BASE.si(p.get("round")),
            BASE.si(p.get("original_roster_id")),
        )
        c = coord_map.get(key)
        if not c or c.get("value_center") is None:
            missing.append(key)
        else:
            vals.append(float(c["value_center"]))
    return vals, missing


def evaluate_trade(side_values, faab_sent, nuisance_ratio):
    principal = [v for side in side_values for v in side if v > 0]
    if not principal:
        return None
    smallest = min(principal)
    max_faab = max(faab_sent) if faab_sent else 0.0
    if max_faab <= 0:
        return None
    # Scale each side's sent FAAB proportionally. The side with the largest FAAB
    # transfer receives the full nuisance allowance; smaller transfers receive less.
    faab_values = [
        float(nuisance_ratio) * smallest * (amt / max_faab)
        for amt in faab_sent
    ]
    distances = {}
    for name, curve in BASE.CURVES.items():
        lhs = list(side_values[0]) + ([faab_values[0]] if faab_values[0] > 0 else [])
        rhs = list(side_values[1]) + ([faab_values[1]] if faab_values[1] > 0 else [])
        distances[name] = round(abs(BASE.effective(lhs, curve) - BASE.effective(rhs, curve)), 4)
    winner = min(distances, key=distances.get)
    return {
        "ratio": nuisance_ratio,
        "smallest_principal_asset_value": round(smallest, 4),
        "faab_side_values": [round(x, 4) for x in faab_values],
        "absolute_clearing_distance": distances,
        "lowest_distance_curve": winner,
    }


def weighted_mean(rows, curve, ratio):
    vals = []
    for row in rows:
        rail = row.get("rails", {}).get(str(ratio))
        if not rail:
            continue
        d = rail["absolute_clearing_distance"].get(curve)
        if d is not None:
            vals.append(float(d))
    return round(statistics.mean(vals), 4) if vals else None


def main():
    trades = [
        t for t in (loadj(DATA / "trade_ledger.json", []) or [])
        if str(t.get("status") or "").lower() == "complete" and BASE.si(t.get("season")) > 2022
    ]
    history = STATE.HistoricalStateProvider()
    pick_provider = PICK.HistoricalPickCoordinateProvider(history_provider=history)

    rows = []
    counts = Counter()
    for trade in trades:
        sides = trade.get("sides") or []
        faab_sent = [BASE.sf(s.get("faab_sent"), 0.0) for s in sides]
        if len(sides) != 2 or max(faab_sent or [0]) <= 0:
            continue

        principal_counts = [
            len(s.get("sent_players") or []) + len(s.get("sent_picks") or [])
            for s in sides
        ]
        if sum(principal_counts) == 0:
            counts["FAAB_ONLY_NOT_PACKAGE_CALIBRATION"] += 1
            rows.append({
                "transaction_id": str(trade.get("transaction_id") or ""),
                "season": BASE.si(trade.get("season")),
                "status": "FAAB_ONLY_NOT_PACKAGE_CALIBRATION",
                "faab_sent": faab_sent,
            })
            continue

        pick_coords = []
        for p in BASE.unique_sent_picks(sides):
            c = pick_provider.historical_pick_value(
                trade_timestamp_ms=BASE.si(trade.get("created")),
                trade_season=BASE.si(trade.get("season")),
                pick_season=p["season"],
                rnd=p["round"],
                original_roster_id=p["original_roster_id"],
            )
            pick_coords.append(c)
        suitability = {str(c.get("calibration_suitability")) for c in pick_coords}
        if pick_coords and not suitability <= {"DIRECT_CALIBRATION", "LOWER_WEIGHT_CALIBRATION"}:
            counts["BLOCKED_BY_PICK_COORDINATE_NOT_FAAB"] += 1
            rows.append({
                "transaction_id": str(trade.get("transaction_id") or ""),
                "season": BASE.si(trade.get("season")),
                "status": "BLOCKED_BY_PICK_COORDINATE_NOT_FAAB",
                "faab_sent": faab_sent,
                "pick_suitability": sorted(suitability),
            })
            continue

        try:
            player_values, scoring_basis = BASE.reconstructed_player_values(history, trade)
        except Exception as exc:
            counts["PLAYER_RECONSTRUCTION_ERROR"] += 1
            rows.append({
                "transaction_id": str(trade.get("transaction_id") or ""),
                "season": BASE.si(trade.get("season")),
                "status": "PLAYER_RECONSTRUCTION_ERROR",
                "error": repr(exc),
            })
            continue

        coord_map = {c["asset_key"]: c for c in pick_coords}
        side_values = []
        missing = []
        for side in sides:
            vals, miss = side_principal_values(side, player_values, coord_map)
            side_values.append(vals)
            missing.extend(miss)
        if missing or any(len(v) == 0 for v in side_values):
            counts["MISSING_PRINCIPAL_COORDINATE"] += 1
            rows.append({
                "transaction_id": str(trade.get("transaction_id") or ""),
                "season": BASE.si(trade.get("season")),
                "status": "MISSING_PRINCIPAL_COORDINATE",
                "missing": missing,
            })
            continue

        rails = {}
        winners = []
        for ratio in NUISANCE_RATIOS:
            result = evaluate_trade(side_values, faab_sent, ratio)
            rails[str(ratio)] = result
            winners.append(result["lowest_distance_curve"])

        primary_winners = [
            rails[str(r)]["lowest_distance_curve"]
            for r in NUISANCE_RATIOS if r <= PRIMARY_ROBUSTNESS_RATIO
        ]
        robust_25 = len(set(primary_winners)) == 1
        robust_50 = len(set(winners)) == 1
        status = "FAAB_ROBUST_TO_25PCT_SMALLEST_PRINCIPAL" if robust_25 else "FAAB_MATERIALITY_SENSITIVE"
        counts[status] += 1
        if robust_50:
            counts["FAAB_ROBUST_TO_50PCT_SMALLEST_PRINCIPAL"] += 1

        rows.append({
            "transaction_id": str(trade.get("transaction_id") or ""),
            "season": BASE.si(trade.get("season")),
            "created": BASE.si(trade.get("created")),
            "created_utc": trade.get("created_utc"),
            "status": status,
            "scoring_basis": scoring_basis,
            "principal_side_values": side_values,
            "principal_side_counts": principal_counts,
            "faab_sent": faab_sent,
            "pick_evidence_qualities": [c.get("evidence_quality") for c in pick_coords],
            "rails": rails,
            "winner_stable_through_25pct": robust_25,
            "winner_stable_through_50pct": robust_50,
            "research_weight_if_used": 0.5 if robust_25 else 0.0,
        })

    robust = [r for r in rows if r.get("winner_stable_through_25pct")]
    curve_means_by_ratio = {
        str(ratio): {name: weighted_mean(robust, name, ratio) for name in BASE.CURVES}
        for ratio in NUISANCE_RATIOS
    }
    ordering_by_ratio = {}
    for ratio in NUISANCE_RATIOS:
        vals = curve_means_by_ratio[str(ratio)]
        names = [n for n, v in vals.items() if v is not None]
        names.sort(key=lambda n: vals[n])
        ordering_by_ratio[str(ratio)] = names

    output = {
        "model_version": MODEL_VERSION,
        "research_only": True,
        "production_authority": False,
        "production_behavior_changed": False,
        "faab_hard_exchange_rate_assigned": False,
        "method": "Treat total FAAB as a nuisance component bounded relative to the smallest principal asset; recover only trades whose concentration winner is invariant through the 25% rail.",
        "nuisance_ratios_of_smallest_principal_asset": list(NUISANCE_RATIOS),
        "primary_robustness_ratio": PRIMARY_ROBUSTNESS_RATIO,
        "counts": dict(counts),
        "robust_trade_count": len(robust),
        "curve_mean_clearing_distance_by_ratio_on_robust_subset": curve_means_by_ratio,
        "curve_ordering_by_ratio_on_robust_subset": ordering_by_ratio,
        "trades": rows,
    }
    (OUT / "historical_faab_nuisance_sensitivity.json").write_text(
        json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps({
        "faab_trade_count": sum(1 for r in rows),
        "counts": dict(counts),
        "robust_trade_count": len(robust),
        "ordering_by_ratio": ordering_by_ratio,
    }, indent=2))

    assert output["production_authority"] is False
    assert output["faab_hard_exchange_rate_assigned"] is False


if __name__ == "__main__":
    main()
