#!/usr/bin/env python3
"""Trade Decision application integration for Behavioral Intelligence.

Extracted from historical Counter Market Sweep v1.20 while preserving its
current production behavior:
- BI2 persistent + state-conditioned evidence remains the primary layer.
- Cached production BI3 opportunity/need-normalized positional evidence
  context-corrects only the positional-acquisition component.
- Pick appetite, package tolerance, youth preference, and same-state historical
  evidence remain BI2/state-conditioned.
- Interactive historical replay is forbidden; the compact BI3 cache is read
  only at trade-analysis runtime.

This is application-owned Trade Decision policy, not a shared FSFFL intelligence
service. It consumes the authoritative Behavioral Intelligence outputs and
interprets them for trade counterparty feasibility only. It cannot override
current-state trade utility, hard bilateral rationality, or final trade quality.
"""
from __future__ import annotations

import json
from pathlib import Path

BI3_CACHE = Path("data/behavioral/behavioral_intelligence_v3.json")
MODEL_VERSION = "FSFFL-Trade-Behavioral-Intelligence-1.0"
BI3_VERSION = "FSFFL-Behavioral-Intelligence-3.0"


def sf(x, d=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def clamp(x, a, b):
    return max(a, min(b, x))


def load_bi3_cache():
    try:
        raw = json.loads(BI3_CACHE.read_text(encoding="utf-8"))
        if not str(raw.get("model_version") or "").startswith(BI3_VERSION):
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


def install(historical_behavior, bi2, bi3_cache, cache_status):
    """Install the validated BI3-over-BI2 composition onto v24's state layer."""
    original = historical_behavior.install_historical_state_conditioning

    def upgraded(v23, hist):
        idx = original(v23, hist)
        prior = v23.state_condition_behavior

        def state_condition_behavior(row, br):
            br = prior(row, br)
            uid = str(row.get("buyer_user_id") or "")
            state = str(br.get("buyer_state") or "unknown")
            shape = historical_behavior.candidate_shape(row)
            signals = {}
            adj = 0.0

            recv_pos = shape.get("received_positions") or []
            if recv_pos:
                vals = []
                for p in recv_pos:
                    name = {
                        "QB": "qb_accumulation",
                        "WR": "wr_affinity",
                        "RB": "rb_affinity",
                        "TE": "te_affinity",
                    }.get(p)
                    if not name:
                        continue
                    t2 = bi2.trait_score(uid, name, state)
                    t3 = (
                        bi3_position_trait(bi3_cache, uid, p)
                        if cache_status == "AVAILABLE"
                        else {
                            "score": 0.0,
                            "confidence": 0.0,
                            "strength": None,
                            "source": "cache_unavailable",
                        }
                    )
                    # Preserve v1.20: BI3 receives up to 45% of the positional
                    # signal at full confidence; BI2/state remains the majority.
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

            pick = bi2.trait_score(uid, "draft_pick_accumulation", state)
            signals["draft_pick_accumulation"] = pick
            adj += .030 * sf(pick.get("score")) * clamp(
                sf(shape.get("net_pick_in")) / 2.0, -1, 1
            )

            large = bi2.trait_score(uid, "large_package_tolerance", state)
            signals["large_package_tolerance"] = large
            if int(shape.get("total_assets") or 0) >= 4:
                adj += .020 * sf(large.get("score"))

            youth = bi2.trait_score(uid, "youth_preference", state)
            signals["youth_preference"] = youth
            recv_players = [
                x for x in shape.get("buyer_receives") or []
                if not historical_behavior.is_pick(x)
            ]
            if recv_players:
                avg_y = sum(
                    bi2.youth_score(str(x).replace("player:", ""))
                    for x in recv_players
                ) / len(recv_players)
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
            ob["full_action_sources"] = [
                "trades", "drafts", "waivers_free_agents", "faab", "drops_cuts"
            ]
            ob["behavioral_intelligence_adjustment"] = round(adj, 4)
            ob["behavioral_intelligence_signals"] = signals
            ob["behavioral_intelligence_can_override_current_state_utility"] = False

            br["owner_behavior"] = ob
            br["heuristic_acceptance_fit_score"] = score
            br["heuristic_acceptance_fit"] = historical_behavior.band(score)
            br["acceptance_fit_basis"] = (
                "current_state_utility_plus_historical_same_state_trade_behavior_"
                "plus_BI2_state_conditioned_traits_plus_BI3_context_normalized_"
                "position_signal_with_bilateral_hard_gates"
            )
            return br

        v23.state_condition_behavior = state_condition_behavior
        return idx

    historical_behavior.install_historical_state_conditioning = upgraded


def apply_report_metadata(report, bi2, bi3_cache, cache_status):
    report.setdefault("policy", {}).update({
        "behavioral_intelligence_version": BI3_VERSION,
        "trade_behavioral_intelligence_model_version": MODEL_VERSION,
        "bi2_persistent_manager_traits_retained": True,
        "bi2_state_conditioned_full_action_behavior_retained": True,
        "bi3_context_normalized_position_signal_enabled": cache_status == "AVAILABLE",
        "bi3_opportunity_normalized": True,
        "bi3_cache_status": cache_status,
        "bi3_interactive_historical_replay": False,
        "behavioral_history_can_override_current_state_utility": False,
        "trade_decision_behavior_integration_internal_component": True,
        "behavioral_intelligence_shared_source_consumed": True,
        "trade_behavior_interpretation_owned_by_trade_decision": True,
    })
    report["behavioral_intelligence"] = {
        "model_version": BI3_VERSION,
        "bi2_model_version": "FSFFL-Behavioral-Intelligence-2.0",
        "trade_composition_model_version": MODEL_VERSION,
        "bi3_cache_status": cache_status,
        "owner_count": (
            len((bi3_cache.get("owners") or {}))
            if cache_status == "AVAILABLE"
            else len((bi2.build().get("owners") or {}))
        ),
        "position_signal": (
            "BI2 persistent/state-conditioned blended with BI3 "
            "opportunity/need-normalized context"
        ),
        "non_position_signals": "BI2 persistent/state-conditioned",
        "interactive_history_rebuild": False,
    }
    # Preserve the production execution-path contract from v1.20.
    report.setdefault("simulation", {})["execution_path"] = (
        "GM3_state_aware_plus_BI2_state_conditioned_behavior_plus_"
        "BI3_context_normalized_position_signal_plus_historical_state_at_trade_"
        "plus_bilateral_market_intelligence_plus_family_dedup_plus_multi_asset_search"
    )
