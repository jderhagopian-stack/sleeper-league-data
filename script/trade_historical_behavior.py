#!/usr/bin/env python3
"""Canonical historical same-state trade behavior conditioning.

Extracted from historical Counter Market Sweep v1.18. Reconstructs manager
competitive state at the time of completed trades, prefers behavior observed in
historically similar states when evidence supports it, and falls back to the
aggregate current-state-conditioned behavior layer when same-state evidence is
weak.

Historical reconstruction is approximate and cannot override current-state
trade utility.
"""
from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path

ASSET_PATH = Path("data/fsffl_asset_values.json")
MODEL_VERSION = "FSFFL-Historical-State-Trade-Behavior-1.0"


def sf(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def clamp(x, lo, hi):
    return max(lo, min(hi, x))


def band(score):
    return "HIGH" if score >= .68 else "MEDIUM" if score >= .48 else "LOW" if score >= .28 else "VERY_LOW"


@lru_cache(maxsize=1)
def asset_meta():
    raw = json.loads(ASSET_PATH.read_text(encoding="utf-8")) if ASSET_PATH.exists() else {}
    out = {}
    for player in raw.get("players") or []:
        pid = str(player.get("player_id") or "")
        aid = str(player.get("asset_id") or pid)
        meta = {"asset_type": "player", "position": player.get("position")}
        if aid:
            out[aid] = meta
        if pid:
            out[pid] = meta
            out[f"player:{pid}"] = meta
    for pick in raw.get("picks") or []:
        aid = str(pick.get("asset_id") or "")
        if aid:
            out[aid] = {"asset_type": "pick", "position": None}
    return out


def is_pick(asset_id):
    aid = str(asset_id)
    return aid.startswith("pick:") or (asset_meta().get(aid) or {}).get("asset_type") == "pick"


def position(asset_id):
    return (asset_meta().get(str(asset_id)) or {}).get("position")


def candidate_shape(row):
    buyer_receives = [str(x) for x in (row.get("outgoing_assets") or [])]
    buyer_sends = [str(x) for x in (row.get("return_assets") or [])]
    recv_picks = sum(is_pick(x) for x in buyer_receives)
    send_picks = sum(is_pick(x) for x in buyer_sends)
    recv_pos = [position(x) for x in buyer_receives if not is_pick(x) and position(x)]
    send_pos = [position(x) for x in buyer_sends if not is_pick(x) and position(x)]
    return {
        "buyer_receives": buyer_receives,
        "buyer_sends": buyer_sends,
        "net_pick_in": recv_picks - send_picks,
        "received_positions": recv_pos,
        "sent_positions": send_pos,
        "total_assets": len(buyer_receives) + len(buyer_sends),
    }


def current_state_static_condition(row, br):
    sig = dict(br.get("owner_behavior") or {})
    static_adj = sf(sig.get("static_historical_adjustment", sig.get("adjustment")))
    base = sf(
        br.get("state_utility_acceptance_fit_score"),
        sf(br.get("heuristic_acceptance_fit_score"), .5) - static_adj,
    )
    state = str(br.get("buyer_state") or "unknown")
    shape = candidate_shape(row)
    net_pick_in = shape["net_pick_in"]

    if state == "elite_contender":
        compat = 1.0 if net_pick_in < 0 else .55 if net_pick_in > 0 else .75
    elif state == "contender":
        compat = .90 if net_pick_in < 0 else .60 if net_pick_in > 0 else .75
    elif state == "retool":
        compat = 1.0 if net_pick_in > 0 else .40 if net_pick_in < 0 else .70
    elif state == "rebuild":
        compat = 1.0 if net_pick_in > 0 else .15 if net_pick_in < 0 else .60
    else:
        compat = .50

    conditioned = static_adj * compat
    if state in {"rebuild", "retool"} and net_pick_in < 0 and conditioned > 0:
        conditioned = min(conditioned, .01)
    if state in {"contender", "elite_contender"} and net_pick_in > 0 and conditioned > 0:
        conditioned = min(conditioned, .02)
    return base, static_adj, conditioned, compat, state, shape, sig


def same_state_fit(profile, shape):
    sample = int(profile.get("trade_sample") or 0)
    avg_conf = sf(profile.get("average_state_reconstruction_confidence"))
    sample_rel = clamp(sample / 6.0, 0.0, 1.0)
    evidence_weight = clamp(sample_rel * avg_conf, 0.0, .92)
    if sample == 0:
        return 0.0, 0.0, {"position": 0.0, "pick": 0.0, "complexity": 0.0}

    shares = profile.get("position_acquisition_share") or {}

    def pos_pref(pos):
        return clamp((sf(shares.get(pos)) - .25) / .25, -1.0, 1.0)

    rp = shape.get("received_positions") or []
    sp = shape.get("sent_positions") or []
    rpref = sum(pos_pref(p) for p in rp) / len(rp) if rp else 0.0
    spref = sum(pos_pref(p) for p in sp) / len(sp) if sp else 0.0
    position_signal = .75 * rpref - .25 * spref

    historical_net_picks = sf(profile.get("average_net_picks_acquired_per_trade"))
    pick_preference = math.tanh(historical_net_picks / 1.25)
    pick_signal = pick_preference * clamp(sf(shape.get("net_pick_in")) / 2.0, -1.0, 1.0)

    complexity_signal = 0.0
    if int(shape.get("total_assets") or 0) >= 4:
        complexity_signal = clamp((sf(profile.get("multi_asset_rate")) - .45) / .45, -1.0, 1.0)

    raw = .50 * position_signal + .32 * pick_signal + .18 * complexity_signal
    adjustment = clamp(raw * evidence_weight * .14, -.14, .14)
    return adjustment, evidence_weight, {
        "position": round(position_signal, 4),
        "pick": round(pick_signal, 4),
        "complexity": round(complexity_signal, 4),
    }


def install_historical_state_conditioning(v23, hist):
    index = hist.build_index()

    def state_condition_behavior(row, br):
        base, static_adj, current_conditioned, compat, state, shape, sig = (
            current_state_static_condition(row, br)
        )
        uid = str(row.get("buyer_user_id") or "")
        profile = hist.owner_state_profile(uid, state)
        historical_adj, evidence_weight, signals = same_state_fit(profile, shape)

        hist_blend = clamp(evidence_weight * .78, 0.0, .72)
        combined = (1.0 - hist_blend) * current_conditioned + hist_blend * historical_adj
        combined = clamp(combined, -.16, .16)

        net_pick_in = int(shape.get("net_pick_in") or 0)
        if state in {"rebuild", "retool"} and net_pick_in < 0 and combined > 0:
            combined = min(combined, .01)
        if state in {"contender", "elite_contender"} and net_pick_in > 0 and combined > 0:
            combined = min(combined, .02)

        score = round(clamp(base + combined, 0.0, 1.0), 4)
        sig.update({
            "static_historical_adjustment": round(static_adj, 4),
            "current_state_conditioned_aggregate_adjustment": round(current_conditioned, 4),
            "historical_same_state_adjustment": round(historical_adj, 4),
            "historical_same_state_blend_weight": round(hist_blend, 4),
            "state_conditioned_adjustment": round(combined, 4),
            "adjustment": round(combined, 4),
            "current_state": state,
            "state_compatibility_weight": round(compat, 3),
            "buyer_net_pick_in": net_pick_in,
            "historical_same_state_sample": int(profile.get("trade_sample") or 0),
            "historical_same_state_reconstruction_confidence": round(
                sf(profile.get("average_state_reconstruction_confidence")), 4
            ),
            "historical_same_state_profile_available": bool(profile),
            "historical_same_state_signals": signals,
            "historical_state_model_version": index.get("model_version"),
            "historical_state_reconstruction_is_approximate": True,
            "historical_state_coverage": index.get("coverage"),
            "state_conditioning_note": (
                "Historical behavior in the manager's current competitive state "
                "is preferred over career-wide behavior when sample quality supports it."
            ),
        })
        br["owner_behavior"] = sig
        br["heuristic_acceptance_fit_score"] = score
        br["heuristic_acceptance_fit"] = band(score)
        br["acceptance_fit_basis"] = (
            "current_state_utility_plus_historical_same_state_behavior_with_aggregate_fallback"
        )
        return br

    v23.state_condition_behavior = state_condition_behavior
    return index


def apply_report_metadata(report, index):
    report.setdefault("policy", {}).update({
        "historical_state_trade_behavior_model_version": MODEL_VERSION,
        "historical_state_at_trade_reconstruction_enabled": True,
        "historical_state_at_trade_reconstruction_is_approximate": True,
        "historical_state_at_trade_uses_future_same_season_results": False,
        "historical_behavior_prefers_same_state_samples": True,
        "historical_same_state_behavior_has_aggregate_fallback": True,
        "historical_behavior_can_override_current_state_utility": False,
        "canonical_historical_state_trade_behavior_shared_component": True,
    })
    report["historical_trade_state_intelligence"] = {
        "model_version": index.get("model_version"),
        "trade_side_count": index.get("trade_side_count"),
        "state_labeled_trade_side_count": index.get("state_labeled_trade_side_count"),
        "coverage": index.get("coverage"),
        "state_counts": index.get("state_counts"),
    }
