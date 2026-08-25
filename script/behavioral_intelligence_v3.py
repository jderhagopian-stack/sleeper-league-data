#!/usr/bin/env python3
"""FSFFL Behavioral Intelligence 3.0 research model.

BI3 keeps BI2's persistent + competitive-state layers, then adds a third layer:
context-normalized revealed preference. A positional acquisition is compared
with the manager's reconstructed pre-action positional needs, so need-driven
behavior is discounted and redundant/surplus accumulation receives more weight.

This module consumes a PRECOMPUTED action-context artifact. It never rebuilds
historical ownership in Market Sweep's interactive path.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
from collections import defaultdict
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent
BI2_PATH = SCRIPT / "behavioral_intelligence.py"
MODEL_VERSION = "FSFFL-Behavioral-Intelligence-3.0-RESEARCH"
POSITIONS = ("QB", "RB", "WR", "TE")
SOURCE_WEIGHT = {"trade": 1.0, "draft": .58, "acquisition": .22}


def loadj(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def clamp(x, lo=-1.0, hi=1.0):
    return max(lo, min(hi, float(x)))


def sf(x, d=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def load_bi2():
    spec = importlib.util.spec_from_file_location("bi2_for_bi3", BI2_PATH)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def strength(v):
    a = abs(v)
    return "VERY_HIGH" if a >= .55 else "HIGH" if a >= .36 else "MODERATE" if a >= .18 else "LOW"


def confidence(weight, avg_context):
    sample_conf = 1 - math.exp(-max(0.0, weight) / 7.0)
    return round(min(.98, sample_conf * (.45 + .55 * clamp(avg_context, 0, 1))), 4)


def new_acc():
    return {
        "weight": 0.0,
        "context_weight": 0.0,
        "context_sum": 0.0,
        "source_weight": defaultdict(float),
        "position": {p: {
            "chosen_weight": 0.0,
            "need_sum": 0.0,
            "redundancy_sum": 0.0,
            "preference_residual_sum": 0.0,
            "exit_weight": 0.0,
            "exit_need_sum": 0.0,
        } for p in POSITIONS},
        "draft_weight": 0.0,
        "draft_redundancy_sum": 0.0,
        "bpa_supported_weight": 0.0,
    }


def add_action(acc, row):
    kind = str(row.get("event_type") or "")
    base = SOURCE_WEIGHT.get(kind, .16)
    cc = sf(row.get("context_confidence"), 0.0)
    w = base * cc
    if w <= 0:
        return
    pre = row.get("pre_action") or {}
    needs = pre.get("position_need") or {}
    surplus = pre.get("position_surplus") or {}
    need_total = sum(max(.02, sf(needs.get(p), .5)) for p in POSITIONS)
    acquired = [p for p in (row.get("positions_acquired") or []) if p in POSITIONS]
    exited = [p for p in (row.get("positions_sent_or_dropped") or []) if p in POSITIONS]

    acc["weight"] += w
    acc["context_weight"] += w
    acc["context_sum"] += w * cc
    acc["source_weight"][kind] += w

    if acquired:
        obs_each = 1.0 / len(acquired)
        for p in POSITIONS:
            exp = max(.02, sf(needs.get(p), .5)) / need_total
            obs = obs_each if p in acquired else 0.0
            acc["position"][p]["preference_residual_sum"] += w * (obs - exp)
        for p in acquired:
            need = sf(needs.get(p), .5)
            sur = sf(surplus.get(p), 0.0)
            redundancy = .72 * (1 - need) + .28 * sur
            pa = acc["position"][p]
            pa["chosen_weight"] += w
            pa["need_sum"] += w * need
            pa["redundancy_sum"] += w * redundancy
        if kind == "draft":
            acc["draft_weight"] += w
            acc["draft_redundancy_sum"] += w * sum(
                .72 * (1 - sf(needs.get(p), .5)) + .28 * sf(surplus.get(p), 0)
                for p in acquired
            ) / len(acquired)
            if row.get("bpa_reach_signal") is not None:
                acc["bpa_supported_weight"] += w

    if exited:
        for p in exited:
            pa = acc["position"][p]
            pa["exit_weight"] += w
            pa["exit_need_sum"] += w * sf(needs.get(p), .5)


def finalize(acc):
    avg_ctx = acc["context_sum"] / max(.0001, acc["context_weight"])
    out_pos = {}
    for p in POSITIONS:
        a = acc["position"][p]
        cw = a["chosen_weight"]
        ew = a["exit_weight"]
        residual = clamp(a["preference_residual_sum"] / max(.001, acc["weight"]) * 3.0)
        need_response = a["need_sum"] / cw if cw else None
        redundancy = a["redundancy_sum"] / cw if cw else None
        exit_need = a["exit_need_sum"] / ew if ew else None
        out_pos[p] = {
            "need_adjusted_preference": {
                "score": round(residual, 4),
                "strength": strength(residual),
                "confidence": confidence(acc["weight"], avg_ctx),
                "interpretation": "positive means acquired more often than positional need alone predicts",
            },
            "need_response": round(need_response, 4) if need_response is not None else None,
            "surplus_or_redundant_accumulation": round(redundancy, 4) if redundancy is not None else None,
            "acquisition_weight": round(cw, 3),
            "exit_when_position_needed": round(exit_need, 4) if exit_need is not None else None,
            "exit_weight": round(ew, 3),
        }
    return {
        "weighted_context_sample": round(acc["weight"], 3),
        "average_context_confidence": round(avg_ctx, 4),
        "weighted_source_mix": {k: round(v, 3) for k, v in sorted(acc["source_weight"].items())},
        "positions": out_pos,
        "draft_context": {
            "weighted_draft_sample": round(acc["draft_weight"], 3),
            "average_redundancy_when_drafting": round(acc["draft_redundancy_sum"] / acc["draft_weight"], 4) if acc["draft_weight"] else None,
            "bpa_reach_signal_available": acc["bpa_supported_weight"] > 0,
            "bpa_supported_weight": round(acc["bpa_supported_weight"], 3),
            "limitation": None if acc["bpa_supported_weight"] else "No time-appropriate historical draft board is currently available; redundancy is measured but BPA/reach is not inferred.",
        },
    }


def build(context_path):
    ctx = loadj(context_path, {})
    if ctx.get("model_version") != "FSFFL-Behavioral-Action-Context-1.1":
        raise RuntimeError(f"Unexpected action-context model: {ctx.get('model_version')}")
    bi2 = load_bi2().build()
    accs = defaultdict(new_acc)
    for row in ctx.get("actions") or []:
        uid = str(row.get("user_id") or "")
        if uid:
            add_action(accs[uid], row)
    owners = {}
    all_uids = sorted(set((bi2.get("owners") or {}).keys()) | set(accs.keys()))
    for uid in all_uids:
        old = (bi2.get("owners") or {}).get(uid, {})
        owners[uid] = {
            "manager": old.get("manager"),
            "team_name": old.get("team_name"),
            "persistent_bi2": old.get("persistent"),
            "state_conditioned_bi2": old.get("by_state"),
            "context_normalized": finalize(accs[uid]),
        }
    return {
        "model_version": MODEL_VERSION,
        "production_status": "RESEARCH_NOT_YET_PROMOTED",
        "architecture": {
            "persistent_traits_preserved": True,
            "state_conditioned_traits_preserved": True,
            "context_normalized_traits_added": True,
            "need_driven_acquisitions_discount_intrinsic_preference": True,
            "surplus_accumulation_strengthens_intrinsic_preference": True,
            "shared_historical_state_provider": True,
            "independent_bi3_historical_replay": False,
            "interactive_historical_replay": False,
            "history_can_override_current_state_utility": False,
        },
        "action_context_model_version": ctx.get("model_version"),
        "historical_state_provider": ctx.get("historical_state_provider"),
        "action_context_audit": ctx.get("audit"),
        "owner_count": len(owners),
        "owners": owners,
        "limitations": [
            "Historical ownership facts come from the shared point-in-time state provider derived from Alternate History reconstruction logic.",
            "Historical player quality uses prior completed-season FSFFL production only; unknown quality is retained as unknown.",
            "Exact historical market-value-at-time is not available.",
            "BPA/reach inference is withheld until time-appropriate historical draft-board evidence is available.",
        ],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--context", required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()
    payload = build(args.context)
    Path(args.output).write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    audit = payload.get("action_context_audit") or {}
    print(json.dumps({"model_version": payload["model_version"], "owner_count": payload["owner_count"], **audit}, indent=2))


if __name__ == "__main__":
    main()
