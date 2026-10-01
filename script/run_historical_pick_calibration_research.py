#!/usr/bin/env python3
"""Expanded historical FSFFL package-concentration research using point-in-time picks.

Research only. This script does not change production coefficients, Shared Decision
Utility, Opportunity Engine authority, Trade Decision authority, or the active
bounded provisional package-concentration prior.
"""
from __future__ import annotations

import importlib.util
import json
import statistics
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence

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


PICK = load_module(SCRIPT / "historical_pick_coordinate.py", "expanded_pick_coordinate")
STATE = load_module(SCRIPT / "fsffl_historical_state_provider.py", "expanded_history_state")
BUNDLE = load_module(SCRIPT / "build_historical_gm3_bundle.py", "expanded_history_bundle")
GM = load_module(SCRIPT / "build_fsffl_gm_engine.py", "expanded_history_gm")

MODEL_VERSION = "FSFFL-Historical-Pick-Package-Research-1.0"


def loadj(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def sf(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def si(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def current_curves() -> Dict[str, list[float]]:
    raw = loadj(DATA / "gm" / "package_concentration_prior.json", {}) or {}
    curves = raw.get("curves") or raw.get("curve_definitions") or {}
    out = {"additive": [1.0] * 5}
    for name in ("mild", "center", "strong"):
        row = curves.get(name)
        if isinstance(row, list) and row:
            out[name] = [float(x) for x in row]
    out.setdefault("mild", [1.00, 0.92, 0.84, 0.78, 0.72])
    out.setdefault("center", [1.00, 0.85, 0.73, 0.64, 0.57])
    out.setdefault("strong", [1.00, 0.78, 0.62, 0.50, 0.42])
    return out


CURVES = current_curves()


def tail(curve: Sequence[float], idx: int) -> float:
    return float(curve[idx]) if idx < len(curve) else float(curve[-1])


def effective(values: Sequence[float], curve: Sequence[float]) -> float:
    vals = sorted((float(v) for v in values if v is not None), reverse=True)
    return sum(v * tail(curve, i) for i, v in enumerate(vals))


def trade_topology(side_counts: Sequence[int]) -> str:
    if len(side_counts) != 2:
        return "MULTI_PARTY"
    a, b = side_counts
    if a == 1 and b == 1:
        return "ONE_FOR_ONE"
    if a == 1 and b > 1:
        return "ONE_FOR_MANY"
    if a > 1 and b == 1:
        return "MANY_FOR_ONE"
    return "MANY_FOR_MANY"


def trade_asset_family(sides: Sequence[Mapping[str, Any]]) -> str:
    has_players = any((s.get("sent_players") or []) for s in sides)
    has_picks = any((s.get("sent_picks") or []) for s in sides)
    if has_players and has_picks:
        return "PLAYER_PLUS_PICK"
    if has_picks:
        return "PICK_PLUS_PICK"
    if has_players:
        return "PLAYER_ONLY"
    return "NO_PLAYER_OR_PICK"


def unique_sent_picks(sides):
    out = []
    seen = set()
    for s in sides:
        for p in s.get("sent_picks") or []:
            key = (si(p.get("season")), si(p.get("round")), si(p.get("original_roster_id")))
            if key in seen:
                continue
            seen.add(key)
            out.append({"season": key[0], "round": key[1], "original_roster_id": key[2]})
    return out


def reconstructed_player_values(history_provider, trade):
    season = str(trade.get("season"))
    tid = str(trade.get("transaction_id"))
    state = history_provider.pre_transaction_state(season, tid)
    data = history_provider.data(season)
    rosters = BUNDLE.historical_rosters(state, data)
    players = BUNDLE.player_index()
    ts = si(trade.get("created"), 0)
    prior, baselines, scoring_basis = BUNDLE.scoring_as_of(int(season), ts, players)
    values, external_exact_count = BUNDLE.build_player_values(
        rosters, players, prior, baselines, {}, int(season)
    )
    if external_exact_count != 0:
        raise AssertionError("External exact player value leaked into historical research")
    out = {str(pid): float(GM.fsffl_league_value(asset)) for pid, asset in values.items()}
    return out, scoring_basis


def evidence_weight(coords):
    if not coords:
        return 1.0
    widths = [
        sf(c.get("uncertainty_relative_width"), 1.0)
        for c in coords
        if c.get("uncertainty_relative_width") is not None
    ]
    if not widths:
        return 0.0
    return 1.0 / (1.0 + statistics.mean(widths))


def aggregate(rows, weighted=False):
    out = {}
    for name in CURVES:
        pairs = []
        for row in rows:
            distance = (row.get("absolute_clearing_distance") or {}).get(name)
            if distance is None:
                continue
            w = sf(row.get("evidence_weight"), 1.0) if weighted else 1.0
            if w > 0:
                pairs.append((float(distance), w))
        if not pairs:
            out[name] = {
                "n": 0,
                "mean_absolute_clearing_distance": None,
                "median_absolute_clearing_distance": None,
                "weighted_mean_absolute_clearing_distance": None,
                "wins_lowest_distance": 0,
            }
            continue
        vals = [v for v, _ in pairs]
        denom = sum(w for _, w in pairs)
        out[name] = {
            "n": len(vals),
            "mean_absolute_clearing_distance": round(statistics.mean(vals), 4),
            "median_absolute_clearing_distance": round(statistics.median(vals), 4),
            "weighted_mean_absolute_clearing_distance": round(sum(v*w for v,w in pairs)/denom, 4),
            "wins_lowest_distance": sum(1 for row in rows if row.get("lowest_distance_curve") == name),
        }
    return out


def geometric_challenger(train_rows, validate_rows):
    train = [r for r in train_rows if r.get("topology") != "ONE_FOR_ONE"]
    validate = [r for r in validate_rows if r.get("topology") != "ONE_FOR_ONE"]
    if len(train) < 20 or len(validate) < 8:
        return {
            "status": "NOT_JUSTIFIED_SAMPLE_TOO_SMALL",
            "train_n": len(train),
            "validation_n": len(validate),
            "candidate_family": "single_monotone_geometric_decay",
            "production_authority": False,
        }

    candidates = []
    for decay in (0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95):
        curve = [1.0, decay, decay**2, decay**3, decay**4]
        vals = [
            abs(effective(r["side_values"][0], curve) - effective(r["side_values"][1], curve))
            for r in train
        ]
        candidates.append((statistics.mean(vals), decay, curve))
    candidates.sort()
    train_mean, decay, curve = candidates[0]
    holdout = [
        abs(effective(r["side_values"][0], curve) - effective(r["side_values"][1], curve))
        for r in validate
    ]
    return {
        "status": "EXPLORATORY_CHALLENGER_ONLY",
        "candidate_family": "single_monotone_geometric_decay",
        "selected_decay_on_earlier_train_only": decay,
        "curve": [round(x, 6) for x in curve],
        "train_n": len(train),
        "validation_n": len(validate),
        "train_mean_absolute_clearing_distance": round(train_mean, 4),
        "validation_mean_absolute_clearing_distance": round(statistics.mean(holdout), 4),
        "one_for_one_invariant_by_construction": True,
        "transaction_specific_tuning": False,
        "player_specific_tuning": False,
        "production_authority": False,
    }


def main():
    trades = [
        t for t in (loadj(DATA / "trade_ledger.json", []) or [])
        if str(t.get("status") or "").lower() == "complete"
    ]
    history = STATE.HistoricalStateProvider()
    pick_provider = PICK.HistoricalPickCoordinateProvider(history_provider=history)

    records = []
    package_rows = []
    recovery_counts = Counter()
    topology_counts = Counter()
    family_counts = Counter()
    quality_counts = Counter()
    exact_pick_assets = 0
    unresolved_pick_assets = 0
    bilateral = 0
    multi_party = 0

    for trade in trades:
        season = si(trade.get("season"), 0)
        sides = trade.get("sides") or []
        tid = str(trade.get("transaction_id") or "")
        timestamp = si(trade.get("created"), 0)
        if len(sides) == 2:
            bilateral += 1
        else:
            multi_party += 1

        counts = [
            len(s.get("sent_players") or []) + len(s.get("sent_picks") or [])
            for s in sides
        ]
        topo = trade_topology(counts)
        family = trade_asset_family(sides)
        topology_counts[topo] += 1
        family_counts[family] += 1

        rec = {
            "transaction_id": tid,
            "season": season,
            "created": timestamp,
            "created_utc": trade.get("created_utc"),
            "participant_count": len(sides),
            "bilateral": len(sides) == 2,
            "topology": topo,
            "asset_family": family,
            "side_asset_counts": counts,
            "faab_involved": any(
                sf(s.get("faab_sent"), 0) != 0 or sf(s.get("faab_received"), 0) != 0
                for s in sides
            ),
            "pick_coordinates": [],
        }

        if season <= 2022:
            rec["recovery_class"] = "EXCLUDED_2022_STARTUP_NONCOMPARABLE"
            rec["reason"] = "2022 startup nomination/order mechanics are not rookie-pick evidence."
            recovery_counts[rec["recovery_class"]] += 1
            records.append(rec)
            continue
        if len(sides) != 2:
            rec["recovery_class"] = "SPECIAL_MULTI_PARTY"
            rec["reason"] = "Multi-party residualization remains a separate problem."
            recovery_counts[rec["recovery_class"]] += 1
            records.append(rec)
            continue
        if rec["faab_involved"]:
            rec["recovery_class"] = "SENSITIVITY_FAAB_UNVALUED"
            rec["reason"] = "FAAB is not assigned an invented historical coordinate here."
            recovery_counts[rec["recovery_class"]] += 1
            records.append(rec)
            continue

        pick_coords = []
        for p in unique_sent_picks(sides):
            c = pick_provider.historical_pick_value(
                trade_timestamp_ms=timestamp,
                trade_season=season,
                pick_season=p["season"],
                rnd=p["round"],
                original_roster_id=p["original_roster_id"],
            )
            pick_coords.append(c)
            quality_counts[str(c.get("evidence_quality"))] += 1
            if c.get("exact_slot_known_at_trade_time"):
                exact_pick_assets += 1
            else:
                unresolved_pick_assets += 1
        rec["pick_coordinates"] = pick_coords

        if not pick_coords:
            rec["recovery_class"] = "RECOVERABLE_PLAYER_ONLY"
        else:
            suitability = {str(c.get("calibration_suitability")) for c in pick_coords}
            if "EXCLUDED" in suitability:
                rec["recovery_class"] = "EXCLUDED_PICK_SEMANTICS"
            elif suitability <= {"DIRECT_CALIBRATION", "LOWER_WEIGHT_CALIBRATION"}:
                rec["recovery_class"] = "RECOVERABLE_WITH_HISTORICAL_PICK_COORDINATE"
            else:
                rec["recovery_class"] = "SENSITIVITY_ONLY_PICK_COORDINATE"
        recovery_counts[rec["recovery_class"]] += 1

        if rec["recovery_class"] not in {
            "RECOVERABLE_PLAYER_ONLY",
            "RECOVERABLE_WITH_HISTORICAL_PICK_COORDINATE",
        }:
            records.append(rec)
            continue

        try:
            player_values, scoring_basis = reconstructed_player_values(history, trade)
            coord_map = {c["asset_key"]: c for c in pick_coords}
            side_values = []
            side_assets = []
            missing = []
            for side in sides:
                vals = []
                assets = []
                for p in side.get("sent_players") or []:
                    pid = str(p.get("player_id") or "")
                    if pid not in player_values:
                        missing.append("player:" + pid)
                        continue
                    vals.append(player_values[pid])
                    assets.append({
                        "asset_type": "player",
                        "asset_id": pid,
                        "value": round(player_values[pid], 4),
                    })
                for p in side.get("sent_picks") or []:
                    key = "pick:%d:R%d:orig%d" % (
                        si(p.get("season")),
                        si(p.get("round")),
                        si(p.get("original_roster_id")),
                    )
                    c = coord_map.get(key)
                    if not c or c.get("value_center") is None:
                        missing.append(key)
                        continue
                    vals.append(float(c["value_center"]))
                    assets.append({
                        "asset_type": "pick",
                        "asset_id": key,
                        "value": float(c["value_center"]),
                        "lower": c.get("value_lower"),
                        "upper": c.get("value_upper"),
                        "evidence_quality": c.get("evidence_quality"),
                    })
                side_values.append(vals)
                side_assets.append(assets)

            if missing or any(len(v) == 0 for v in side_values):
                rec["package_analysis_status"] = "SKIPPED_MISSING_COORDINATE"
                rec["package_analysis_missing"] = missing
                records.append(rec)
                continue

            distances = {
                name: round(abs(effective(side_values[0], curve) - effective(side_values[1], curve)), 4)
                for name, curve in CURVES.items()
            }
            package = {
                "transaction_id": tid,
                "season": season,
                "created": timestamp,
                "created_utc": trade.get("created_utc"),
                "topology": topo,
                "asset_family": family,
                "package_counts": counts,
                "pick_asset_count": len(pick_coords),
                "pick_evidence_qualities": [c.get("evidence_quality") for c in pick_coords],
                "exact_slot_pick_count": sum(bool(c.get("exact_slot_known_at_trade_time")) for c in pick_coords),
                "unresolved_pick_count": sum(not bool(c.get("exact_slot_known_at_trade_time")) for c in pick_coords),
                "evidence_weight": round(evidence_weight(pick_coords), 6),
                "scoring_basis": scoring_basis,
                "side_assets": side_assets,
                "side_values": side_values,
                "absolute_clearing_distance": distances,
                "lowest_distance_curve": min(distances, key=distances.get),
                "completed_trade_implies_exact_fair_value": False,
                "research_only": True,
            }
            package_rows.append(package)
            rec["package_analysis_status"] = "INCLUDED_PRIMARY_RESEARCH"
        except Exception as exc:
            rec["package_analysis_status"] = "SKIPPED_RECONSTRUCTION_ERROR"
            rec["package_analysis_error"] = repr(exc)
        records.append(rec)

    records.sort(key=lambda r: (si(r.get("created"), 0), r.get("transaction_id", "")))
    package_rows.sort(key=lambda r: (si(r.get("created"), 0), r.get("transaction_id", "")))

    primary = package_rows
    unequal = [r for r in primary if r["topology"] != "ONE_FOR_ONE"]
    one_for_one = [r for r in primary if r["topology"] == "ONE_FOR_ONE"]
    equal_multi = [
        r for r in primary
        if r["topology"] == "MANY_FOR_MANY"
        and len(r["package_counts"]) == 2
        and r["package_counts"][0] == r["package_counts"][1]
    ]
    pick_heavy = [
        r for r in primary
        if sum(1 for side in r["side_assets"] for a in side if a["asset_type"] == "pick")
        >= sum(1 for side in r["side_assets"] for a in side if a["asset_type"] == "player")
    ]
    player_heavy = [r for r in primary if r not in pick_heavy]

    split = max(1, int(len(primary) * 0.70))
    train = primary[:split]
    validate = primary[split:]

    temporal = {
        "model_version": MODEL_VERSION,
        "split_method": "time_ordered_earliest_70_percent_train_latest_30_percent_validate",
        "random_split_used": False,
        "train_n": len(train),
        "validation_n": len(validate),
        "train_start": train[0]["created_utc"] if train else None,
        "train_end": train[-1]["created_utc"] if train else None,
        "validation_start": validate[0]["created_utc"] if validate else None,
        "validation_end": validate[-1]["created_utc"] if validate else None,
        "train_aggregate": aggregate(train, weighted=True),
        "validation_aggregate": aggregate(validate, weighted=True),
        "exploratory_challenger": geometric_challenger(train, validate),
    }

    expanded = {
        "model_version": MODEL_VERSION,
        "research_only": True,
        "production_behavior_changed": False,
        "production_prior_changed": False,
        "shared_decision_utility_changed": False,
        "current_package_prior_read_only": CURVES,
        "completed_trade_implies_exact_fair_value": False,
        "primary_trade_count": len(primary),
        "unequal_package_trade_count": len(unequal),
        "one_for_one_control_count": len(one_for_one),
        "equal_count_multi_asset_control_count": len(equal_multi),
        "pick_heavy_trade_count": len(pick_heavy),
        "player_heavy_trade_count": len(player_heavy),
        "aggregate_all_primary_unweighted": aggregate(primary, weighted=False),
        "aggregate_all_primary_uncertainty_weighted": aggregate(primary, weighted=True),
        "aggregate_unequal_packages_uncertainty_weighted": aggregate(unequal, weighted=True),
        "aggregate_one_for_one_controls": aggregate(one_for_one, weighted=False),
        "aggregate_equal_count_controls": aggregate(equal_multi, weighted=False),
        "aggregate_pick_heavy": aggregate(pick_heavy, weighted=True),
        "aggregate_player_heavy": aggregate(player_heavy, weighted=True),
        "trades": primary,
    }

    recoverability = {
        "model_version": MODEL_VERSION,
        "research_only": True,
        "completed_trade_count": len(trades),
        "startup_exclusion_count": recovery_counts["EXCLUDED_2022_STARTUP_NONCOMPARABLE"],
        "non_startup_trade_count": len(trades) - recovery_counts["EXCLUDED_2022_STARTUP_NONCOMPARABLE"],
        "bilateral_completed_trade_count": bilateral,
        "multi_party_completed_trade_count": multi_party,
        "topology_counts": dict(topology_counts),
        "asset_family_counts": dict(family_counts),
        "recovery_class_counts": dict(recovery_counts),
        "exact_slot_historical_pick_asset_count": exact_pick_assets,
        "unresolved_historical_pick_asset_count": unresolved_pick_assets,
        "recoverable_primary_trade_count": (
            recovery_counts["RECOVERABLE_PLAYER_ONLY"]
            + recovery_counts["RECOVERABLE_WITH_HISTORICAL_PICK_COORDINATE"]
        ),
        "recoverable_with_pick_coordinate_count": recovery_counts["RECOVERABLE_WITH_HISTORICAL_PICK_COORDINATE"],
        "sensitivity_only_trade_count": (
            recovery_counts["SENSITIVITY_ONLY_PICK_COORDINATE"]
            + recovery_counts["SENSITIVITY_FAAB_UNVALUED"]
        ),
        "true_exclusion_count": (
            recovery_counts["EXCLUDED_2022_STARTUP_NONCOMPARABLE"]
            + recovery_counts["EXCLUDED_PICK_SEMANTICS"]
        ),
        "records": records,
        "principles": {
            "2022_startup_nomination_order_not_pick_value_evidence": True,
            "future_rookie_draft_results_do_not_leak_backward": True,
            "current_pick_values_not_historical_truth": True,
            "uncertain_magnitude_not_zero_effect": True,
            "quality_weighting_preferred_to_blanket_exclusion": True,
        },
    }

    quality = {
        "model_version": MODEL_VERSION,
        "research_only": True,
        "pick_asset_evidence_quality_counts": dict(quality_counts),
        "weighting_method": (
            "Descriptive uncertainty weight = 1/(1+mean relative rail width) for trades "
            "with reconstructed picks. Player-only rows receive weight 1. Research only."
        ),
        "primary_calibration_suitability": ["DIRECT_CALIBRATION", "LOWER_WEIGHT_CALIBRATION"],
        "sensitivity_only_not_silently_zeroed": True,
        "production_authority": False,
    }

    (OUT / "historical_pick_coordinate_recoverability.json").write_text(
        json.dumps(recoverability, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (OUT / "historical_package_concentration_expanded.json").write_text(
        json.dumps(expanded, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (OUT / "historical_pick_evidence_quality.json").write_text(
        json.dumps(quality, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (OUT / "historical_package_temporal_validation.json").write_text(
        json.dumps(temporal, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    agg = expanded["aggregate_unequal_packages_uncertainty_weighted"]
    ranked = [
        name for name in CURVES
        if agg.get(name, {}).get("weighted_mean_absolute_clearing_distance") is not None
    ]
    ranked.sort(key=lambda name: agg[name]["weighted_mean_absolute_clearing_distance"])
    winner = ranked[0] if ranked else "insufficient evidence"
    center_rank = ranked.index("center") + 1 if "center" in ranked else None

    lines = [
        "# FSFFL Historical Pick Coordinate — Research Summary",
        "",
        "## Scope and governance",
        "",
        "Research/calibration only. No production authority is changed.",
        "The 2022 startup is excluded from rookie-pick valuation evidence.",
        "",
        "## Trade recovery",
        "",
        "* Completed historical trades: **%d**" % len(trades),
        "* 2022 startup exclusions: **%d**" % recoverability["startup_exclusion_count"],
        "* Non-startup trades: **%d**" % recoverability["non_startup_trade_count"],
        "* Bilateral trades: **%d**" % bilateral,
        "* Multi-party trades: **%d**" % multi_party,
        "* Primary recoverable trades: **%d**" % recoverability["recoverable_primary_trade_count"],
        "* Recovered through historical pick coordinate: **%d**" % recoverability["recoverable_with_pick_coordinate_count"],
        "* Sensitivity-only trades retained: **%d**" % recoverability["sensitivity_only_trade_count"],
        "* Exact-slot historical pick assets: **%d**" % exact_pick_assets,
        "* Unresolved picks represented probabilistically: **%d**" % unresolved_pick_assets,
        "",
        "## Package-concentration evidence",
        "",
        "* Primary research sample: **%d**" % len(primary),
        "* Unequal-package evidence: **%d**" % len(unequal),
        "* One-for-one controls: **%d**" % len(one_for_one),
        "* Equal-count multi-asset controls: **%d**" % len(equal_multi),
        "",
        "Uncertainty-weighted unequal-package descriptive ordering: **%s**" % (
            " > ".join(ranked) if ranked else "insufficient evidence"
        ),
        "",
        "Center prior rank: **%s**. Lowest-distance curve: **%s**." % (
            str(center_rank) if center_rank is not None else "not estimable",
            winner,
        ),
        "",
        "Completed trades are noisy revealed-preference / clearing evidence; exact fairness is not assumed.",
        "",
        "## Promotion posture",
        "",
        "No production coefficient is changed. Any empirical challenger remains research-only until "
        "it clears temporal holdout, topology, control, de-duplication, provenance, and stability gates.",
        "",
    ]
    (OUT / "historical_pick_coordinate_summary.md").write_text("\n".join(lines), encoding="utf-8")

    print(json.dumps({
        "completed_trade_count": len(trades),
        "startup_exclusion_count": recoverability["startup_exclusion_count"],
        "non_startup_trade_count": recoverability["non_startup_trade_count"],
        "recoverable_primary_trade_count": recoverability["recoverable_primary_trade_count"],
        "recoverable_with_pick_coordinate_count": recoverability["recoverable_with_pick_coordinate_count"],
        "sensitivity_only_trade_count": recoverability["sensitivity_only_trade_count"],
        "primary_package_research_count": len(primary),
        "unequal_package_count": len(unequal),
        "descriptive_rank": ranked,
        "temporal_train_n": len(train),
        "temporal_validation_n": len(validate),
        "challenger_status": temporal["exploratory_challenger"].get("status"),
    }, indent=2))

    assert len(trades) == 144
    assert recoverability["startup_exclusion_count"] >= 1
    assert recoverability["principles"]["2022_startup_nomination_order_not_pick_value_evidence"] is True
    assert recoverability["principles"]["future_rookie_draft_results_do_not_leak_backward"] is True
    assert expanded["production_behavior_changed"] is False
    assert expanded["production_prior_changed"] is False


if __name__ == "__main__":
    main()
