#!/usr/bin/env python3
"""FSFFL Counter & Market Sweep 1.8 — owner-behavior-aware acceptance.

Runs the 1.7 acceptance-frontier engine but injects the owner-behavior intelligence
already present in the GM model (completed trades, rookie drafts and waivers)
into both frontier selection and post-simulation acceptance fit.

Behavior is evidence, not a veto and not a calibrated acceptance probability.
Canonical Sleeper / GM / Simulator state remains read-only.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

V17_PATH = Path("script/run_trade_market_sweep_v17.py")
OWNER_BEHAVIOR_PATH = Path("data/owner_behavior_profiles.json")
ASSET_PATH = Path("data/fsffl_asset_values.json")
MODEL_VERSION = "FSFFL-Counter-Market-Sweep-1.8"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to import {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def safe_float(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def load_json(path: Path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def build_behavior_index() -> Dict[str, Dict[str, Any]]:
    """Mirror the transparent GM-2.2/3.0 owner-preference construction."""
    profiles = load_json(OWNER_BEHAVIOR_PATH, []) or []
    positions = ("QB", "RB", "WR", "TE")
    raw_share: Dict[str, Dict[str, float]] = {}
    raw_pick: Dict[str, float] = {}

    for p in profiles:
        uid = str(p.get("user_id"))
        trade = p.get("trade_profile") or {}
        draft = p.get("rookie_draft_profile") or {}
        waiver = p.get("waiver_profile") or {}
        acq = trade.get("player_positions_acquired") or {}
        drafted = draft.get("positions") or {}
        added = waiver.get("positions_added") or {}
        scores = {
            pos: safe_float(acq.get(pos)) + 0.70 * safe_float(drafted.get(pos)) + 0.20 * safe_float(added.get(pos))
            for pos in positions
        }
        total = sum(scores.values()) or 1.0
        raw_share[uid] = {pos: scores[pos] / total for pos in positions}
        acquired = sum(safe_float(trade.get(k)) for k in ("firsts_acquired", "seconds_acquired", "thirds_acquired"))
        sent = sum(safe_float(trade.get(k)) for k in ("firsts_sent", "seconds_sent", "thirds_sent"))
        raw_pick[uid] = acquired - sent + 0.15 * safe_float(draft.get("rookie_picks_made_2023_plus"))

    league_avg = {
        pos: (sum(x[pos] for x in raw_share.values()) / len(raw_share)) if raw_share else 0.25
        for pos in positions
    }
    pick_vals = list(raw_pick.values())
    pick_mean = sum(pick_vals) / len(pick_vals) if pick_vals else 0.0
    pick_var = sum((x - pick_mean) ** 2 for x in pick_vals) / len(pick_vals) if pick_vals else 1.0
    pick_sd = pick_var ** 0.5 or 1.0

    out = {}
    for p in profiles:
        uid = str(p.get("user_id"))
        trade = p.get("trade_profile") or {}
        shares = raw_share.get(uid, {})
        pos_pref = {}
        for pos in positions:
            avg = league_avg.get(pos) or 0.25
            ratio = shares.get(pos, 0.0) / avg
            pos_pref[pos] = round(clamp((ratio - 1.0) / 0.75, -1.0, 1.0), 3)
        pick_z = (raw_pick.get(uid, 0.0) - pick_mean) / pick_sd
        total_trades = safe_float(trade.get("total_trades"))
        recent = safe_float(trade.get("recent_trades_2025_2026"))
        initiated = safe_float(trade.get("initiated_trades"))
        multi = safe_float(trade.get("multi_asset_trades"))
        confidence = "HIGH" if total_trades >= 20 else "MEDIUM" if total_trades >= 8 else "LOW"
        out[uid] = {
            "position_preference": pos_pref,
            "pick_preference": round(clamp(pick_z / 2.0, -1.0, 1.0), 3),
            "completed_trade_sample": int(total_trades),
            "recent_trade_sample": int(recent),
            "initiation_rate": round(initiated / total_trades, 3) if total_trades else None,
            "multi_asset_rate": round(multi / total_trades, 3) if total_trades else None,
            "behavior_confidence": confidence,
            "confidence_weight": {"HIGH": 1.0, "MEDIUM": 0.65, "LOW": 0.35}[confidence],
        }
    return out


def build_asset_meta() -> Dict[str, Dict[str, Any]]:
    d = load_json(ASSET_PATH, {}) or {}
    out = {}
    for p in d.get("players") or []:
        aid = p.get("asset_id") or (f"player:{p.get('player_id')}" if p.get("player_id") is not None else None)
        if aid:
            out[str(aid)] = {"asset_type": "player", "position": p.get("position"), "name": p.get("name")}
    for p in d.get("picks") or []:
        aid = p.get("asset_id")
        if aid:
            out[str(aid)] = {"asset_type": "pick", "position": None, "name": p.get("name") or aid}
    return out


def behavior_signal(uid: str, buyer_receives: List[str], buyer_sends: List[str],
                    behavior: Dict[str, Dict[str, Any]], meta: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    b = behavior.get(str(uid)) or {}
    if not b:
        return {"available": False, "behavior_confidence": "NONE", "adjustment": 0.0,
                "reason": "No owner-behavior profile available; market/state model remains primary."}

    prefs = b.get("position_preference") or {}
    recv_pos = [meta.get(a, {}).get("position") for a in buyer_receives if meta.get(a, {}).get("asset_type") == "player"]
    send_pos = [meta.get(a, {}).get("position") for a in buyer_sends if meta.get(a, {}).get("asset_type") == "player"]
    recv_pos = [x for x in recv_pos if x]
    send_pos = [x for x in send_pos if x]
    recv_pref = sum(safe_float(prefs.get(x)) for x in recv_pos) / len(recv_pos) if recv_pos else 0.0
    send_pref = sum(safe_float(prefs.get(x)) for x in send_pos) / len(send_pos) if send_pos else 0.0
    position_signal = 0.75 * recv_pref - 0.25 * send_pref

    recv_picks = sum(1 for a in buyer_receives if meta.get(a, {}).get("asset_type") == "pick" or str(a).startswith("pick:"))
    send_picks = sum(1 for a in buyer_sends if meta.get(a, {}).get("asset_type") == "pick" or str(a).startswith("pick:"))
    pick_flow = clamp((recv_picks - send_picks) / 2.0, -1.0, 1.0)
    pick_signal = safe_float(b.get("pick_preference")) * pick_flow

    package_size = len(buyer_receives) + len(buyer_sends)
    multi_rate = b.get("multi_asset_rate")
    complexity_signal = 0.0
    if multi_rate is not None and package_size >= 4:
        complexity_signal = clamp((safe_float(multi_rate) - 0.45) / 0.45, -1.0, 1.0)
    activity_signal = clamp((safe_float(b.get("recent_trade_sample")) - 2.0) / 8.0, -0.5, 1.0)

    raw = 0.52 * position_signal + 0.25 * pick_signal + 0.13 * complexity_signal + 0.10 * activity_signal
    adjustment = round(clamp(raw * safe_float(b.get("confidence_weight"), 0.35) * 0.16, -0.16, 0.16), 4)

    positives, negatives = [], []
    if position_signal >= 0.20:
        positives.append("incoming player positions match this manager's historical acquisition tendencies")
    elif position_signal <= -0.20:
        negatives.append("incoming player positions run against this manager's historical acquisition tendencies")
    if pick_signal >= 0.15:
        positives.append("pick flow matches the manager's historical pick preference")
    elif pick_signal <= -0.15:
        negatives.append("pick flow conflicts with the manager's historical pick preference")
    if complexity_signal >= 0.20:
        positives.append("the manager has historically participated in multi-asset trades")
    elif complexity_signal <= -0.20:
        negatives.append("the package is more complex than this manager's typical completed trades")
    if activity_signal >= 0.25:
        positives.append("the manager has been an active recent trader")
    reason_parts = positives[:2] + negatives[:2]

    return {
        "available": True,
        "behavior_confidence": b.get("behavior_confidence"),
        "completed_trade_sample": b.get("completed_trade_sample"),
        "recent_trade_sample": b.get("recent_trade_sample"),
        "initiation_rate": b.get("initiation_rate"),
        "multi_asset_rate": b.get("multi_asset_rate"),
        "position_signal": round(position_signal, 4),
        "pick_signal": round(pick_signal, 4),
        "complexity_signal": round(complexity_signal, 4),
        "activity_signal": round(activity_signal, 4),
        "adjustment": adjustment,
        "reason": "; ".join(reason_parts) if reason_parts else "Historical behavior is roughly neutral for this package.",
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", required=True)
    ap.add_argument("--quick-sims", type=int, default=100)
    ap.add_argument("--confirm-sims", type=int, default=0)
    ap.add_argument("--search-depth", type=int, default=60)
    ap.add_argument("--output", required=True)
    ap.add_argument("--seed", type=int, default=20260821)
    args = ap.parse_args()

    behavior = build_behavior_index()
    meta = build_asset_meta()
    v17 = load_module(V17_PATH, "market_sweep_v17_for_v18")
    original_static = v17.static_buyer_fit
    original_loader = v17.load_module

    def behavior_static(engine, focus_uid, buyer_uid, outgoing, incoming):
        base = original_static(engine, focus_uid, buyer_uid, outgoing, incoming)
        recv = [engine.asset_id(a) for a in outgoing]
        send = [engine.asset_id(a) for a in incoming]
        sig = behavior_signal(str(buyer_uid), recv, send, behavior, meta)
        adjusted_utility = safe_float(base.get("estimated_buyer_utility")) + safe_float(sig.get("adjustment")) * 3500.0
        base["owner_behavior_pre_screen"] = sig
        base["behavior_adjusted_buyer_utility"] = round(adjusted_utility, 2)
        base["frontier_distance"] = round(abs(adjusted_utility), 2)
        base["buyer_friendly_bonus"] = round(min(1500.0, max(-1500.0, adjusted_utility)), 2)
        return base

    def patched_loader(path, name):
        mod = original_loader(path, name)
        if Path(path) == Path(v17.V16_PATH):
            original_br = mod.buyer_rationality

            def behavior_br(row, dl):
                out = original_br(row, dl)
                uid = str(row.get("buyer_user_id") or "")
                recv = [str(x) for x in (row.get("outgoing_assets") or [])]
                send = [str(x) for x in (row.get("return_assets") or [])]
                sig = behavior_signal(uid, recv, send, behavior, meta)
                base_score = safe_float(out.get("heuristic_acceptance_fit_score"), 0.5)
                adjusted = round(clamp(base_score + safe_float(sig.get("adjustment")), 0.0, 1.0), 4)
                band = "HIGH" if adjusted >= 0.68 else "MEDIUM" if adjusted >= 0.48 else "LOW" if adjusted >= 0.28 else "VERY_LOW"
                out["state_utility_acceptance_fit_score"] = base_score
                out["owner_behavior"] = sig
                out["heuristic_acceptance_fit_score"] = adjusted
                out["heuristic_acceptance_fit"] = band
                out["acceptance_fit_basis"] = "state_utility_plus_GM_owner_behavior"
                out["acceptance_fit_is_probability"] = False
                return out

            mod.buyer_rationality = behavior_br
        return mod

    v17.static_buyer_fit = behavior_static
    v17.load_module = patched_loader

    old_argv = sys.argv[:]
    try:
        sys.argv = [str(V17_PATH), "--scenario", args.scenario, "--quick-sims", str(args.quick_sims),
                    "--confirm-sims", str(args.confirm_sims), "--search-depth", str(args.search_depth),
                    "--output", args.output, "--seed", str(args.seed)]
        v17.main()
    finally:
        sys.argv = old_argv

    report = load_json(Path(args.output), {}) or {}
    report["model_version"] = MODEL_VERSION
    report.setdefault("policy", {})["GM_owner_behavior_integrated"] = True
    report["policy"]["owner_behavior_sources"] = ["completed_trades", "rookie_drafts", "waivers"]
    report["policy"]["owner_behavior_is_evidence_not_veto"] = True
    report["policy"]["acceptance_fit_is_calibrated_probability"] = False
    report.setdefault("simulation", {})["execution_path"] = "owner_behavior_acceptance_frontier_then_fast_decision_lab"
    report["owner_behavior_profiles_available"] = len(behavior)
    Path(args.output).write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
