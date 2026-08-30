#!/usr/bin/env python3
"""FSFFL Behavioral Intelligence 3.0 research model.

BI3 preserves BI2 persistent + state-conditioned traits, then adds context-
normalized revealed preference. This revision also controls for the league's
observed opportunity environment by decision type using leave-one-manager-out
position priors. A manager is therefore compared with what other managers tend
to acquire in trades, drafts, and waivers/free agency under similar action types,
then roster need modifies that expectation.

The model consumes a PRECOMPUTED action-context artifact. It never rebuilds
historical ownership in Market Sweep's interactive path.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent
BI2_PATH = SCRIPT / "behavioral_intelligence.py"
MODEL_VERSION = "FSFFL-Behavioral-Intelligence-3.0-RESEARCH-SHRINKAGE-1"
POSITIONS = ("QB", "RB", "WR", "TE")
# Action-type opportunity normalization already controls for the very different
# choice environments of trades, drafts and acquisitions. Per-action
# context_confidence is therefore the evidence weight; no second hand-set source
# multiplier is applied.
SOURCE_WEIGHT = {"trade": 1.0, "draft": 1.0, "acquisition": 1.0}
OPPORTUNITY_SMOOTHING = 1.0
NEED_FLOOR = .30


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


def shrinkage_factor(weight, prior_strength):
    """Empirical-Bayes-style shrinkage toward the league-neutral prior.

    prior_strength is the median positive manager effective sample in the same
    action-context build, so the amount of shrinkage automatically adapts as the
    league history grows instead of depending on a hand-set saturation constant.
    """
    w = max(0.0, sf(weight))
    p = max(0.001, sf(prior_strength, 1.0))
    return clamp(w / (w + p), 0.0, 1.0)


def confidence(weight, avg_context, prior_strength=1.0):
    return round(
        min(.98, shrinkage_factor(weight, prior_strength) * clamp(avg_context, 0, 1)),
        4,
    )


def valid_acquired(row):
    return [p for p in (row.get("positions_acquired") or []) if p in POSITIONS]


def build_opportunity_environment(actions):
    """Observed position mix by action type, with per-owner contributions retained.

    This is not claimed to be a pure availability model. It is a league-specific
    empirical opportunity prior: the acquisition mix other managers actually
    encounter/execute for each action type. Leave-one-manager-out use prevents a
    manager's own historical choices from defining their benchmark.
    """
    totals = defaultdict(lambda: defaultdict(float))
    owner = defaultdict(lambda: defaultdict(lambda: defaultdict(float)))
    owner_weight = defaultdict(lambda: defaultdict(float))
    for row in actions:
        uid = str(row.get("user_id") or "")
        kind = str(row.get("event_type") or "")
        acquired = valid_acquired(row)
        if not uid or kind not in SOURCE_WEIGHT or not acquired:
            continue
        cc = max(0.05, sf(row.get("context_confidence"), 0.0))
        share = cc / len(acquired)
        for p in acquired:
            totals[kind][p] += share
            owner[uid][kind][p] += share
        owner_weight[uid][kind] += cc
    return {"totals": totals, "owner": owner, "owner_weight": owner_weight}


def loo_opportunity_share(env, uid, kind):
    numer = {}
    for p in POSITIONS:
        numer[p] = max(
            0.0,
            sf(env["totals"][kind].get(p)) - sf(env["owner"][uid][kind].get(p))
        ) + OPPORTUNITY_SMOOTHING
    den = sum(numer.values()) or 1.0
    return {p: numer[p] / den for p in POSITIONS}


def expected_position_share(needs, opportunity):
    raw = {}
    for p in POSITIONS:
        need = clamp(sf(needs.get(p), .5), 0, 1)
        need_factor = NEED_FLOOR + (1 - NEED_FLOOR) * need
        raw[p] = max(.0001, sf(opportunity.get(p), .25)) * need_factor
    den = sum(raw.values()) or 1.0
    return {p: raw[p] / den for p in POSITIONS}


def new_acc():
    return {
        "weight": 0.0,
        "context_weight": 0.0,
        "context_sum": 0.0,
        "source_weight": defaultdict(float),
        "opportunity_samples": defaultdict(float),
        "position": {p: {
            "chosen_weight": 0.0,
            "need_sum": 0.0,
            "redundancy_sum": 0.0,
            "preference_residual_sum": 0.0,
            "raw_need_residual_sum": 0.0,
            "expected_share_sum": 0.0,
            "expected_share_weight": 0.0,
            "exit_weight": 0.0,
            "exit_need_sum": 0.0,
            "source_residual": defaultdict(float),
            "source_weight": defaultdict(float),
        } for p in POSITIONS},
        "draft_weight": 0.0,
        "draft_redundancy_sum": 0.0,
        "bpa_supported_weight": 0.0,
    }


def add_action(acc, row, env):
    uid = str(row.get("user_id") or "")
    kind = str(row.get("event_type") or "")
    base = SOURCE_WEIGHT.get(kind, .16)
    cc = sf(row.get("context_confidence"), 0.0)
    w = base * cc
    if w <= 0:
        return
    pre = row.get("pre_action") or {}
    needs = pre.get("position_need") or {}
    surplus = pre.get("position_surplus") or {}
    acquired = valid_acquired(row)
    exited = [p for p in (row.get("positions_sent_or_dropped") or []) if p in POSITIONS]

    acc["weight"] += w
    acc["context_weight"] += w
    acc["context_sum"] += w * cc
    acc["source_weight"][kind] += w

    if acquired:
        opp = loo_opportunity_share(env, uid, kind)
        expected = expected_position_share(needs, opp)
        need_total = sum(max(.02, sf(needs.get(p), .5)) for p in POSITIONS)
        obs_each = 1.0 / len(acquired)
        acc["opportunity_samples"][kind] += w
        for p in POSITIONS:
            obs = obs_each if p in acquired else 0.0
            need_only = max(.02, sf(needs.get(p), .5)) / need_total
            pa = acc["position"][p]
            pa["preference_residual_sum"] += w * (obs - expected[p])
            pa["raw_need_residual_sum"] += w * (obs - need_only)
            pa["expected_share_sum"] += w * expected[p]
            pa["expected_share_weight"] += w
            pa["source_residual"][kind] += w * (obs - expected[p])
            pa["source_weight"][kind] += w
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


def finalize(acc, prior_strength):
    avg_ctx = acc["context_sum"] / max(.0001, acc["context_weight"])
    out_pos = {}
    for p in POSITIONS:
        a = acc["position"][p]
        cw = a["chosen_weight"]
        ew = a["exit_weight"]
        shrink = shrinkage_factor(acc["weight"], prior_strength)
        # obs - expected is already naturally bounded in [-1, 1]. The prior
        # implementation multiplied it by 3.0, an arbitrary scale expansion.
        # Keep the native residual scale and shrink sparse manager estimates
        # toward the league-neutral prior (zero).
        residual = clamp(a["preference_residual_sum"] / max(.001, acc["weight"]) * shrink)
        raw_need_residual = clamp(a["raw_need_residual_sum"] / max(.001, acc["weight"]) * shrink)
        need_response = a["need_sum"] / cw if cw else None
        redundancy = a["redundancy_sum"] / cw if cw else None
        exit_need = a["exit_need_sum"] / ew if ew else None
        expected_share = a["expected_share_sum"] / a["expected_share_weight"] if a["expected_share_weight"] else None
        by_source = {}
        for kind in SOURCE_WEIGHT:
            sw = a["source_weight"].get(kind, 0.0)
            if sw:
                by_source[kind] = round(clamp(a["source_residual"][kind] / sw), 4)
        out_pos[p] = {
            "opportunity_and_need_adjusted_preference": {
                "score": round(residual, 4),
                "strength": strength(residual),
                "confidence": confidence(acc["weight"], avg_ctx, prior_strength),
                "shrinkage_factor": round(shrink, 4),
                "interpretation": "positive means acquired more often than roster need plus the action-type league opportunity prior predicts",
            },
            "need_only_preference_for_comparison": round(raw_need_residual, 4),
            "average_expected_choice_share": round(expected_share, 4) if expected_share is not None else None,
            "opportunity_adjusted_score_by_action_type": by_source,
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
        "opportunity_normalization_sample": {k: round(v, 3) for k, v in sorted(acc["opportunity_samples"].items())},
        "positions": out_pos,
        "draft_context": {
            "weighted_draft_sample": round(acc["draft_weight"], 3),
            "average_redundancy_when_drafting": round(acc["draft_redundancy_sum"] / acc["draft_weight"], 4) if acc["draft_weight"] else None,
            "bpa_reach_signal_available": acc["bpa_supported_weight"] > 0,
            "bpa_supported_weight": round(acc["bpa_supported_weight"], 3),
            "limitation": None if acc["bpa_supported_weight"] else "No time-appropriate historical draft board is currently available; redundancy is measured but BPA/reach is not inferred.",
        },
    }


def environment_summary(env):
    out = {}
    for kind in SOURCE_WEIGHT:
        vals = {p: sf(env["totals"][kind].get(p)) + OPPORTUNITY_SMOOTHING for p in POSITIONS}
        den = sum(vals.values()) or 1.0
        out[kind] = {p: round(vals[p] / den, 4) for p in POSITIONS}
    return out


def league_bias_audit(owners):
    num = {p: 0.0 for p in POSITIONS}
    den = {p: 0.0 for p in POSITIONS}
    for row in owners.values():
        cn = row.get("context_normalized") or {}
        wt = sf(cn.get("weighted_context_sample"), 0)
        for p in POSITIONS:
            score = sf((((cn.get("positions") or {}).get(p) or {}).get("opportunity_and_need_adjusted_preference") or {}).get("score"), 0)
            num[p] += wt * score
            den[p] += wt
    return {p: round(num[p] / den[p], 4) if den[p] else 0.0 for p in POSITIONS}


def build(context_path):
    ctx = loadj(context_path, {})
    if ctx.get("model_version") != "FSFFL-Behavioral-Action-Context-1.1":
        raise RuntimeError(f"Unexpected action-context model: {ctx.get('model_version')}")
    actions = ctx.get("actions") or []
    env = build_opportunity_environment(actions)
    bi2 = load_bi2().build()
    accs = defaultdict(new_acc)
    for row in actions:
        uid = str(row.get("user_id") or "")
        if uid:
            add_action(accs[uid], row, env)
    owners = {}
    all_uids = sorted(set((bi2.get("owners") or {}).keys()) | set(accs.keys()))
    positive_weights = [sf(accs[uid].get("weight")) for uid in all_uids if sf(accs[uid].get("weight")) > 0]
    prior_strength = statistics.median(positive_weights) if positive_weights else 1.0
    for uid in all_uids:
        old = (bi2.get("owners") or {}).get(uid, {})
        owners[uid] = {
            "manager": old.get("manager"),
            "team_name": old.get("team_name"),
            "persistent_bi2": old.get("persistent"),
            "state_conditioned_bi2": old.get("by_state"),
            "context_normalized": finalize(accs[uid], prior_strength),
        }
    return {
        "model_version": MODEL_VERSION,
        "production_status": "RESEARCH_NOT_YET_PROMOTED",
        "architecture": {
            "persistent_traits_preserved": True,
            "state_conditioned_traits_preserved": True,
            "context_normalized_traits_added": True,
            "need_driven_acquisitions_discount_intrinsic_preference": True,
            "action_type_opportunity_normalization": True,
            "leave_one_manager_out_opportunity_prior": True,
            "surplus_accumulation_strengthens_intrinsic_preference": True,
            "shared_historical_state_provider": True,
            "independent_bi3_historical_replay": False,
            "interactive_historical_replay": False,
            "history_can_override_current_state_utility": False,
        },
        "opportunity_environment": {
            "method": "leave-one-manager-out empirical position priors by action type, smoothed and combined with pre-action roster need",
            "league_action_type_position_mix": environment_summary(env),
            "smoothing_per_position": OPPORTUNITY_SMOOTHING,
            "need_floor": NEED_FLOOR,
            "source_weight_policy": "equal_after_action_type_opportunity_normalization; per-action context_confidence supplies evidence weight",
        },
        "shrinkage": {
            "method": "manager_effective_sample_over_manager_effective_sample_plus_league_median_effective_sample",
            "prior_mean": 0.0,
            "prior_strength": round(prior_strength, 4),
            "prior_strength_basis": "median positive manager weighted context sample in the current build",
            "self_updates_as_history_grows": True,
        },
        "action_context_model_version": ctx.get("model_version"),
        "historical_state_provider": ctx.get("historical_state_provider"),
        "action_context_audit": ctx.get("audit"),
        "owner_count": len(owners),
        "league_bias_audit": league_bias_audit(owners),
        "owners": owners,
        "limitations": [
            "The action-type opportunity prior is empirical league behavior, not a perfect reconstruction of every available alternative at each decision timestamp.",
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
    print(json.dumps({
        "model_version": payload["model_version"],
        "owner_count": payload["owner_count"],
        "league_bias_audit": payload["league_bias_audit"],
        **audit,
    }, indent=2))


if __name__ == "__main__":
    main()
