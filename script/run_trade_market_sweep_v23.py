#!/usr/bin/env python3
"""FSFFL Counter & Market Sweep 1.17 — dynamic state-conditioned market intelligence.

Builds on 1.16. Competitive state is treated as time-varying rather than a
permanent manager trait. Normal recommendations must be beneficial under the
focal franchise's current state-aware objective. Historical owner behavior is
still useful evidence, but its acceptance adjustment is conditioned on the
buyer's current competitive state and cannot override current-state utility.
"""
from __future__ import annotations

import importlib.util
import json
import math
import sys
from collections import Counter
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent
V22_PATH = SCRIPT / "run_trade_market_sweep_v22.py"
V21_PATH = SCRIPT / "run_trade_market_sweep_v21.py"
V20_PATH = SCRIPT / "run_trade_market_sweep_v20.py"
V19_PATH = SCRIPT / "run_trade_market_sweep_v19.py"
V18_PATH = SCRIPT / "run_trade_market_sweep_v18.py"
V16_PATH = SCRIPT / "run_trade_market_sweep_v16.py"
MODEL_VERSION = "FSFFL-Counter-Market-Sweep-1.17"
NEGOTIATION_RANKING = SCRIPT / "negotiation_ranking.py"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to import {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def sf(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def clamp(x, lo, hi):
    return max(lo, min(hi, x))


def band(score):
    return "HIGH" if score >= .68 else "MEDIUM" if score >= .48 else "LOW" if score >= .28 else "VERY_LOW"


def focal_current_state(row):
    strategic = ((row.get("simulation") or {}).get("strategic") or {})
    return str(strategic.get("objective_state") or row.get("focus_state") or "unknown")


def focal_state_beneficial(row):
    state = focal_current_state(row)
    post = sf(row.get("post_sim_score"))
    comps = row.get("state_aware_score_components") or {}
    future = sf(comps.get("future"))
    current = sf(comps.get("current"))
    if post <= 0:
        return False
    if state == "rebuild":
        return future > 0
    if state == "retool":
        return future > -250
    if state in {"contender", "elite_contender"}:
        return row.get("championship_equity_constraint") == "PASS" and current > -500
    return True


def state_condition_behavior(row, br):
    sig = dict(br.get("owner_behavior") or {})
    static_adj = sf(sig.get("adjustment"))
    base = sf(br.get("state_utility_acceptance_fit_score"), sf(br.get("heuristic_acceptance_fit_score"), .5) - static_adj)
    state = str(br.get("buyer_state") or "unknown")
    buyer_receives = [str(x) for x in (row.get("outgoing_assets") or [])]
    buyer_sends = [str(x) for x in (row.get("return_assets") or [])]
    recv_picks = sum(x.startswith("pick:") for x in buyer_receives)
    send_picks = sum(x.startswith("pick:") for x in buyer_sends)
    net_pick_in = recv_picks - send_picks

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

    score = round(clamp(base + conditioned, 0.0, 1.0), 4)
    sig.update({
        "static_historical_adjustment": round(static_adj, 4),
        "state_conditioned_adjustment": round(conditioned, 4),
        "adjustment": round(conditioned, 4),
        "current_state": state,
        "state_compatibility_weight": round(compat, 3),
        "buyer_net_pick_in": int(net_pick_in),
        "state_conditioning_note": "Aggregate historical behavior is attenuated when it conflicts with the manager's current competitive state.",
    })
    br["owner_behavior"] = sig
    br["heuristic_acceptance_fit_score"] = score
    br["heuristic_acceptance_fit"] = band(score)
    br["acceptance_fit_basis"] = "current_state_utility_plus_state_conditioned_historical_behavior"
    br["acceptance_band_is_descriptive_not_probability"] = True
    return br


def recompute_negotiation_ranking(row):
    br = row.get("buyer_rationality") or {}
    post = sf(row.get("post_sim_score"))
    strategic = clamp(.50 + .50 * math.tanh(post / 5000.0), 0, 1)
    acceptance = clamp(sf(br.get("heuristic_acceptance_fit_score"), .5), 0, 1)
    behavior = clamp(.50 + sf((br.get("owner_behavior") or {}).get("adjustment")) / .32, 0, 1)
    nr = load_module(NEGOTIATION_RANKING, "negotiation_ranking_for_v117")
    out = nr.compose(strategic, acceptance, behavior)
    out["focal_strategic_gain_source"] = "state_aware_post_sim_score"
    out["state_aware_post_sim_score"] = round(post, 2)
    return out


def patch_v18(mod):
    original = mod.adjusted_buyer_rationality
    def adjusted(base_mod, row, dl, beh, meta):
        br = original(base_mod, row, dl, beh, meta)
        return state_condition_behavior(row, br)
    mod.adjusted_buyer_rationality = adjusted
    return mod


def patch_v16(mod):
    original = mod.focal_viable
    def focal_viable(row):
        ok = original(row)
        beneficial = focal_state_beneficial(row)
        row["focal_current_state_beneficial"] = bool(beneficial)
        row["focal_current_state"] = focal_current_state(row)
        return bool(ok and beneficial)
    mod.focal_viable = focal_viable
    return mod


def patch_v21_selectors(mod):
    original_swing = mod.select_swing_distinct

    def prepare(rows):
        for r in rows:
            br = r.get("buyer_rationality") or {}
            if br:
                state_condition_behavior(r, br)
                r["acceptance_likelihood"] = br.get("heuristic_acceptance_fit")
                r["negotiation_ranking"] = recompute_negotiation_ranking(r)
        return sorted(rows, key=lambda r: (sf((r.get("negotiation_ranking") or {}).get("score")), sf(r.get("post_sim_score"))), reverse=True)

    def normal(viable, swing):
        prepared = [r for r in prepare(list(viable)) if focal_state_beneficial(r)]
        selected = []
        counts = Counter()
        used_families = set()
        swing_family = mod.negotiation_family_key(swing) if swing else None
        for row in prepared:
            fam = mod.negotiation_family_key(row)
            if swing_family and fam == swing_family:
                continue
            if fam in used_families:
                continue
            uid = str(row.get("buyer_user_id") or "")
            if counts[uid] >= mod.MAX_NORMAL_OPTIONS_PER_BUYER:
                continue
            selected.append(row)
            used_families.add(fam)
            counts[uid] += 1
            if len(selected) == 4:
                break
        return selected

    def swing(viable):
        return original_swing(prepare(list(viable)))

    mod.select_normal_four_strict = normal
    mod.select_swing_distinct = swing
    return mod


def recompute_action_without_acceptance_band_gate(report):
    """Recompute action from strategic/bilateral viability, not HIGH/MEDIUM labels."""
    top = list(report.get("top_5_alternatives") or report.get("ranked_finalists") or [])
    if not top:
        return "DECLINE"

    current = report.get("current_offer_evaluation") or {}
    current_buyer_ok = bool((current.get("buyer_rationality") or {}).get("current_state_viable"))
    current_focal_ok = focal_state_beneficial(current)
    best = top[0]

    if current_focal_ok and current_buyer_ok:
        return "SHOP_BEFORE_ACCEPTING" if sf(best.get("post_sim_score")) > sf(current.get("post_sim_score")) + 750 else "ACCEPT_NOW"
    if any(r.get("candidate_type") == "SAME_PARTNER_COUNTER" for r in top[:5]):
        return "COUNTER_CURRENT_OFFEROR"
    return "SHOP_BEFORE_ACCEPTING"


def output_path_from_argv():
    if "--output" not in sys.argv:
        return None
    i = sys.argv.index("--output")
    return Path(sys.argv[i + 1]) if i + 1 < len(sys.argv) else None


def main():
    v22 = load_module(V22_PATH, "market_sweep_v22_for_v117")
    original_v22_loader = v22.load_module

    def patched_v22_loader(path: Path, name: str):
        mod = original_v22_loader(path, name)
        if Path(path) == V21_PATH:
            mod = patch_v21_selectors(mod)
            original_v21_loader = mod.load_module
            def patched_v21_loader(p2: Path, n2: str):
                m2 = original_v21_loader(p2, n2)
                if Path(p2) == V20_PATH:
                    original_v20_loader = m2.load_module
                    def patched_v20_loader(p3: Path, n3: str):
                        m3 = original_v20_loader(p3, n3)
                        if Path(p3) == V19_PATH:
                            original_v19_loader = m3.load_module
                            def patched_v19_loader(p4: Path, n4: str):
                                m4 = original_v19_loader(p4, n4)
                                if Path(p4) == V18_PATH:
                                    m4 = patch_v18(m4)
                                elif Path(p4) == V16_PATH:
                                    m4 = patch_v16(m4)
                                return m4
                            m3.load_module = patched_v19_loader
                        return m3
                    m2.load_module = patched_v20_loader
                return m2
            mod.load_module = patched_v21_loader
        return mod

    v22.load_module = patched_v22_loader
    v22.MODEL_VERSION = MODEL_VERSION
    v22.main()

    output = output_path_from_argv()
    if output and output.exists():
        report = json.loads(output.read_text(encoding="utf-8"))
        report["model_version"] = MODEL_VERSION
        inherited_action = report.get("recommended_next_action")
        report["recommended_next_action"] = recompute_action_without_acceptance_band_gate(report)
        report.setdefault("policy", {}).update({
            "competitive_state_treated_as_time_varying": True,
            "normal_recommendations_require_positive_focal_current_state_utility": True,
            "rebuild_normal_recommendations_require_positive_future_component": True,
            "owner_behavior_conditioned_on_current_competitive_state": True,
            "historical_behavior_can_override_current_state_utility": False,
            "acceptance_band_is_authoritative_candidate_gate": False,
            "acceptance_band_is_authoritative_action_gate": False,
            "acceptance_band_is_ranking_signal_not_eligibility_gate": True,
            "acceptance_fit_used_as_negotiation_ranking_signal": True,
            "accepted_rejected_opportunity_denominator_available": False,
            "historical_state_at_trade_reconstruction_complete": False,
        })
        report["acceptance_gate_action_audit"] = {
            "inherited_pre_override_action": inherited_action,
            "final_action_without_acceptance_band_gate": report.get("recommended_next_action"),
        }
        report.setdefault("simulation", {})["execution_path"] = (
            "GM3_state_aware_plus_dynamic_current_state_focal_gate_plus_state_conditioned_owner_behavior_plus_"
            "bilateral_market_intelligence_plus_family_dedup_plus_multi_asset_search"
        )
        output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")


if __name__ == "__main__":
    main()
