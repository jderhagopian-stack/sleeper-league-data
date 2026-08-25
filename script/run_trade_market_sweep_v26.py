#!/usr/bin/env python3
"""FSFFL Counter & Market Sweep 1.20 — Behavioral Intelligence 3.0 candidate.

Extends 1.19 conservatively:
- BI2 persistent + state-conditioned behavior remains the primary behavioral layer.
- BI3 opportunity/need-normalized positional preference corrects only the
  positional-acquisition component.
- BI2 continues to govern pick appetite, large-package tolerance, youth
  preference, and state-conditioned historical evidence.
- BI3 history is read only from a compact refresh-time cache; interactive
  historical replay is forbidden. Missing cache falls back to BI2.

Behavior remains secondary evidence and cannot override current-state utility,
bilateral rationality, or normal-recommendation focal-value gates.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent
V24 = SCRIPT / "run_trade_market_sweep_v24.py"
BI2 = SCRIPT / "behavioral_intelligence.py"
BI3_CACHE = Path("data/behavioral/behavioral_intelligence_v3.json")
MODEL_VERSION = "FSFFL-Counter-Market-Sweep-1.20"
BI3_VERSION = "FSFFL-Behavioral-Intelligence-3.0"


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def sf(x, d=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def clamp(x, a, b):
    return max(a, min(b, x))


def output_path():
    if "--output" not in sys.argv:
        return None
    i = sys.argv.index("--output")
    return Path(sys.argv[i + 1]) if i + 1 < len(sys.argv) else None


def load_bi3_cache():
    try:
        raw = json.loads(BI3_CACHE.read_text(encoding="utf-8"))
        if not str(raw.get("model_version") or "").startswith("FSFFL-Behavioral-Intelligence-3.0"):
            return {}, "VERSION_MISMATCH"
        return raw, "AVAILABLE"
    except Exception:
        return {}, "MISSING"


def bi3_position_trait(cache, uid, position):
    owner = (cache.get("owners") or {}).get(str(uid)) or {}
    pos = (((owner.get("context_normalized") or {}).get("positions") or {}).get(position) or {})
    trait = pos.get("opportunity_and_need_adjusted_preference") or {}
    return {
        "score": sf(trait.get("score"), 0.0),
        "confidence": clamp(sf(trait.get("confidence"), 0.0), 0, 1),
        "strength": trait.get("strength"),
        "source": "opportunity_and_need_adjusted_preference",
    }


def install(v24, bi2, bi3_cache, cache_status):
    original = v24.install_historical_state_conditioning

    def upgraded(v23, hist):
        idx = original(v23, hist)
        prior = v23.state_condition_behavior

        def state_condition_behavior(row, br):
            br = prior(row, br)
            uid = str(row.get("buyer_user_id") or "")
            state = str(br.get("buyer_state") or "unknown")
            shape = v24.candidate_shape(row)
            signals = {}
            adj = 0.0

            # Position acquisition: retain BI2's state-conditioned signal as the
            # majority component, then context-correct it with cached BI3.
            recv_pos = shape.get("received_positions") or []
            if recv_pos:
                vals = []
                for p in recv_pos:
                    name = {"QB": "qb_accumulation", "WR": "wr_affinity", "RB": "rb_affinity", "TE": "te_affinity"}.get(p)
                    if not name:
                        continue
                    t2 = bi2.trait_score(uid, name, state)
                    t3 = bi3_position_trait(bi3_cache, uid, p) if cache_status == "AVAILABLE" else {"score": 0.0, "confidence": 0.0, "strength": None, "source": "cache_unavailable"}
                    # At full BI3 confidence, context normalization gets 45% of
                    # the positional signal; BI2/state remains 55%. Sparse BI3
                    # evidence naturally reduces the context share.
                    w3 = .45 * sf(t3.get("confidence"), 0.0)
                    blend = (1 - w3) * sf(t2.get("score")) + w3 * sf(t3.get("score"))
                    vals.append(blend)
                    signals[f"{p}_affinity"] = {
                        "bi2_persistent_plus_state": t2,
                        "bi3_context_normalized": t3,
                        "bi3_blend_weight": round(w3, 4),
                        "blended_score": round(blend, 4),
                    }
                if vals:
                    adj += .035 * (sum(vals) / len(vals))

            # Non-positional traits remain BI2/state-conditioned in 1.20.
            pick = bi2.trait_score(uid, "draft_pick_accumulation", state)
            signals["draft_pick_accumulation"] = pick
            adj += .030 * sf(pick.get("score")) * clamp(sf(shape.get("net_pick_in")) / 2.0, -1, 1)

            large = bi2.trait_score(uid, "large_package_tolerance", state)
            signals["large_package_tolerance"] = large
            if int(shape.get("total_assets") or 0) >= 4:
                adj += .020 * sf(large.get("score"))

            youth = bi2.trait_score(uid, "youth_preference", state)
            signals["youth_preference"] = youth
            recv_players = [x for x in shape.get("buyer_receives") or [] if not v24.is_pick(x)]
            if recv_players:
                avg_y = sum(bi2.youth_score(str(x).replace("player:", "")) for x in recv_players) / len(recv_players)
                adj += .025 * sf(youth.get("score")) * ((avg_y - .5) * 2)

            adj = clamp(adj, -.075, .075)
            base = sf(br.get("heuristic_acceptance_fit_score"), .5)
            score = round(clamp(base + adj, 0, 1), 4)
            ob = dict(br.get("owner_behavior") or {})
            ob["behavioral_intelligence_version"] = BI3_VERSION
            ob["bi2_persistent_plus_state_conditioned_retained"] = True
            ob["bi3_context_normalized_position_signal_enabled"] = cache_status == "AVAILABLE"
            ob["bi3_cache_status"] = cache_status
            ob["bi3_interactive_historical_replay"] = False
            ob["full_action_sources"] = ["trades", "drafts", "waivers_free_agents", "faab", "drops_cuts"]
            ob["behavioral_intelligence_adjustment"] = round(adj, 4)
            ob["behavioral_intelligence_signals"] = signals
            ob["behavioral_intelligence_can_override_current_state_utility"] = False
            br["owner_behavior"] = ob
            br["heuristic_acceptance_fit_score"] = score
            br["heuristic_acceptance_fit"] = v24.band(score)
            br["acceptance_fit_basis"] = "current_state_utility_plus_historical_same_state_trade_behavior_plus_BI2_state_conditioned_traits_plus_BI3_context_normalized_position_signal_with_bilateral_hard_gates"
            return br

        v23.state_condition_behavior = state_condition_behavior
        return idx

    v24.install_historical_state_conditioning = upgraded


def main():
    v24 = load(V24, "market_v24_for_120")
    bi2 = load(BI2, "behavioral_intelligence_for_120")
    bi3_cache, cache_status = load_bi3_cache()
    install(v24, bi2, bi3_cache, cache_status)
    v24.MODEL_VERSION = MODEL_VERSION
    v24.main()

    out = output_path()
    if out and out.exists():
        r = json.loads(out.read_text(encoding="utf-8"))
        r["model_version"] = MODEL_VERSION
        r.setdefault("policy", {}).update({
            "behavioral_intelligence_version": BI3_VERSION,
            "bi2_persistent_manager_traits_retained": True,
            "bi2_state_conditioned_full_action_behavior_retained": True,
            "bi3_context_normalized_position_signal_enabled": cache_status == "AVAILABLE",
            "bi3_opportunity_normalized": True,
            "bi3_cache_status": cache_status,
            "bi3_interactive_historical_replay": False,
            "behavioral_history_can_override_current_state_utility": False,
        })
        r["behavioral_intelligence"] = {
            "model_version": BI3_VERSION,
            "bi2_model_version": "FSFFL-Behavioral-Intelligence-2.0",
            "bi3_cache_status": cache_status,
            "owner_count": len((bi3_cache.get("owners") or {})) if cache_status == "AVAILABLE" else len((bi2.build().get("owners") or {})),
            "position_signal": "BI2 persistent/state-conditioned blended with BI3 opportunity/need-normalized context",
            "non_position_signals": "BI2 persistent/state-conditioned",
            "interactive_history_rebuild": False,
        }
        r.setdefault("simulation", {})["execution_path"] = "GM3_state_aware_plus_BI2_state_conditioned_behavior_plus_BI3_context_normalized_position_signal_plus_historical_state_at_trade_plus_bilateral_market_intelligence_plus_family_dedup_plus_multi_asset_search"
        out.write_text(json.dumps(r, indent=2, sort_keys=True), encoding="utf-8")


if __name__ == "__main__":
    main()
