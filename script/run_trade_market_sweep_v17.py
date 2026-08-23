#!/usr/bin/env python3
"""FSFFL Counter & Market Sweep 1.7 — acceptance-frontier search.

Searches for trades near the counterparty's strategic indifference point instead
of merely walking farther down the focal team's preferred list. The engine first
uses GM 3.0 values/state to cheaply estimate bilateral fit, deliberately reserves
same-partner and alternate-buyer frontier candidates, then runs the fast exact
Decision Lab simulation and applies the full buyer-rationality gate.

The actionable report prefers MEDIUM/HIGH acceptance-fit trades. Up to one
SWING_FOR_FENCES trade may be shown separately in the Top 5, but it cannot drive
the final action. Human acceptance remains a heuristic, not a calibrated
probability. Canonical Sleeper / GM / Simulator state remains read-only.
"""
from __future__ import annotations

import argparse
import functools
import importlib.util
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

V13_PATH = Path("script/run_trade_market_sweep_v13.py")
V16_PATH = Path("script/run_trade_market_sweep_v16.py")
MODEL_VERSION = "FSFFL-Counter-Market-Sweep-1.7"
DEFAULT_SEARCH_DEPTH = 60


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to import {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def install_read_caches(engine):
    engine.franchise_index = functools.lru_cache(maxsize=1)(engine.franchise_index)
    engine.asset_catalog = functools.lru_cache(maxsize=1)(engine.asset_catalog)
    engine.command_center = functools.lru_cache(maxsize=None)(engine.command_center)
    engine.strategic_assets = functools.lru_cache(maxsize=None)(engine.strategic_assets)
    engine.need_map = functools.lru_cache(maxsize=None)(engine.need_map)
    engine.team_state = functools.lru_cache(maxsize=None)(engine.team_state)
    return engine


def static_buyer_fit(engine, focus_uid: str, buyer_uid: str,
                     outgoing: List[Dict[str, Any]], incoming: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Cheap GM-side estimate used only to choose what deserves simulation."""
    state = engine.team_state(buyer_uid)
    gain_market = sum(engine.asset_value(a, focus_uid)["market"] for a in outgoing)
    gain_redraft = sum(engine.asset_value(a, focus_uid)["redraft"] for a in outgoing)
    cost_market = sum(engine.asset_value(a, buyer_uid)["market"] for a in incoming)
    cost_redraft = sum(engine.asset_value(a, buyer_uid)["redraft"] for a in incoming)
    cost_break = sum(engine.asset_value(a, buyer_uid)["break_glass"] for a in incoming)
    buyer_market_delta = gain_market - cost_market
    buyer_redraft_delta = gain_redraft - cost_redraft

    # State-aware scalar whose zero neighborhood approximates the acceptance
    # frontier. This is only a pre-simulation search heuristic.
    if state == "elite_contender":
        utility = 0.62 * buyer_redraft_delta + 0.28 * buyer_market_delta - 0.10 * max(0.0, cost_break - cost_market)
    elif state == "contender":
        utility = 0.52 * buyer_redraft_delta + 0.38 * buyer_market_delta - 0.10 * max(0.0, cost_break - cost_market)
    elif state == "retool":
        utility = 0.35 * buyer_redraft_delta + 0.55 * buyer_market_delta - 0.10 * max(0.0, cost_break - cost_market)
    elif state == "rebuild":
        utility = 0.15 * buyer_redraft_delta + 0.75 * buyer_market_delta - 0.10 * max(0.0, cost_break - cost_market)
    else:
        utility = 0.45 * buyer_redraft_delta + 0.45 * buyer_market_delta - 0.10 * max(0.0, cost_break - cost_market)

    # Near zero is the bargaining frontier; mildly positive buyer utility is
    # preferred to materially negative buyer utility.
    frontier_distance = abs(utility)
    buyer_friendly_bonus = min(1500.0, max(-1500.0, utility))
    return {
        "buyer_state": state,
        "estimated_buyer_market_delta": round(buyer_market_delta, 2),
        "estimated_buyer_redraft_delta": round(buyer_redraft_delta, 2),
        "estimated_buyer_utility": round(utility, 2),
        "frontier_distance": round(frontier_distance, 2),
        "buyer_friendly_bonus": round(buyer_friendly_bonus, 2),
    }


def frontier_select(rows: List[Tuple[Dict[str, Any], List[Dict[str, Any]], List[Dict[str, Any]]]],
                    depth: int, current_partner: str) -> List[Tuple[Dict[str, Any], List[Dict[str, Any]], List[Dict[str, Any]]]]:
    """Diversify around the bilateral frontier before global fill."""
    selected = []
    seen = set()

    def key(item):
        r = item[0]
        return (r["buyer_user_id"], tuple(r["outgoing_assets"]), tuple(r["return_assets"]))

    def add(item):
        k = key(item)
        if k in seen or len(selected) >= depth:
            return
        seen.add(k)
        selected.append(item)

    # Rank by focal value while strongly favoring a small absolute buyer utility.
    ranked = sorted(rows, key=lambda x: (
        float(x[0].get("frontier_search_score") or 0.0),
        float(x[0].get("plausibility_score") or 0.0),
    ), reverse=True)

    # Reserve a meaningful same-partner search and distinct alternate buyers.
    same = [x for x in ranked if x[0]["buyer_user_id"] == current_partner]
    for item in same[: max(8, depth // 5)]:
        add(item)

    by_buyer: Dict[str, List[Any]] = {}
    for item in ranked:
        if item[0]["buyer_user_id"] == current_partner:
            continue
        by_buyer.setdefault(item[0]["buyer_user_id"], []).append(item)
    for buyer_rows in by_buyer.values():
        for item in buyer_rows[:2]:
            add(item)

    for item in ranked:
        add(item)
        if len(selected) >= depth:
            break
    return selected


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", required=True)
    ap.add_argument("--quick-sims", type=int, default=100)
    ap.add_argument("--confirm-sims", type=int, default=0)
    ap.add_argument("--search-depth", type=int, default=DEFAULT_SEARCH_DEPTH)
    ap.add_argument("--output", required=True)
    ap.add_argument("--seed", type=int, default=20260821)
    args = ap.parse_args()
    if args.quick_sims < 100:
        raise ValueError("--quick-sims must be at least 100")
    depth = max(30, args.search_depth)

    v13 = load_module(V13_PATH, "market_sweep_v13_for_v17")
    v16 = load_module(V16_PATH, "market_sweep_v16_for_v17")
    engine = v13.load_module(v13.BASE_ENGINE, "market_sweep_base_for_v17")
    install_read_caches(engine)
    dl = engine.import_decision_lab()

    def fast_sim(dl_mod, model_inputs, baseline_lineups, baseline,
                 focus_uid, buyer_uid, outgoing, incoming, sims, seed):
        return v13.fast_simulate_candidate(
            engine, dl_mod, model_inputs, baseline_lineups, baseline,
            focus_uid, buyer_uid, outgoing, incoming, sims, seed
        )

    scenario_path = Path(args.scenario)
    scenario = engine.load_json(scenario_path, {}) or {}
    focus_uid = str(scenario.get("focus_user_id") or "")
    sent_ids, received_ids, current_partner = engine.incoming_trade_parts(scenario, focus_uid)

    model_inputs = dl.load_model_inputs()
    simmod, league, rosters, users, players, season, projections, raw_schedule = model_inputs
    baseline_lineups = dl.load_cached_lineups(season)
    baseline = dl.simulate_from_lineups(
        simmod, league, rosters, users, raw_schedule, baseline_lineups, args.quick_sims, args.seed
    )

    player_catalog, pick_catalog = engine.asset_catalog()
    catalog = {**player_catalog, **pick_catalog}
    full_outgoing = [catalog[x] for x in sent_ids if x in catalog]
    current_incoming = [catalog[x] for x in received_ids if x in catalog]
    missing = [x for x in sent_ids + received_ids if x not in catalog]
    if missing:
        raise ValueError(f"Scenario assets missing from FSFFL asset catalog: {missing}")

    owner_assets = engine.build_owner_assets(rosters)
    idx = engine.franchise_index()
    variants = engine.outgoing_variants(full_outgoing)
    full_key = engine.package_key(full_outgoing)
    raw = []

    for outgoing in variants:
        variant = "FULL" if engine.package_key(outgoing) == full_key else "SUBSET"
        for buyer_uid in idx:
            if buyer_uid == focus_uid:
                continue
            assets = owner_assets.get(buyer_uid) or []
            player_pool = sorted([a for a in assets if a.get("asset_type") == "player"],
                                 key=lambda a: a.get("market_dynasty", 0), reverse=True)[:10]
            pick_pool = sorted([a for a in assets if a.get("asset_type") == "pick"],
                               key=lambda a: a.get("market_dynasty", 0), reverse=True)[:8]
            for pkg in engine.candidate_packages(player_pool + pick_pool):
                row = engine.score_candidate(focus_uid, buyer_uid, outgoing, pkg)
                if row["plausibility"] == "THEORETICAL_ONLY":
                    continue
                row["outgoing_variant"] = variant
                row["candidate_type"] = "SAME_PARTNER_COUNTER" if buyer_uid == current_partner else "ALTERNATE_BUYER"
                sf = static_buyer_fit(engine, focus_uid, buyer_uid, outgoing, pkg)
                row["frontier_pre_screen"] = sf
                # Reward focal pre-screen value but pull search toward the buyer's
                # indifference zone. Mild buyer-positive utility is desirable.
                row["frontier_search_score"] = round(
                    float(row.get("pre_screen_score") or 0.0)
                    - 0.60 * float(sf["frontier_distance"])
                    + 0.30 * float(sf["buyer_friendly_bonus"]), 2
                )
                raw.append((row, pkg, outgoing))

    selected = frontier_select(raw, depth, current_partner)
    focus_state = engine.team_state(focus_uid)
    simulated = []
    for row, pkg, outgoing in selected:
        r = dict(row)
        r["simulation"] = fast_sim(
            dl, model_inputs, baseline_lineups, baseline, focus_uid,
            r["buyer_user_id"], outgoing, pkg, args.quick_sims, args.seed
        )
        r["post_sim_score"] = engine.post_sim_score(r, focus_state)
        r["buyer_rationality"] = v16.buyer_rationality(r, dl)
        simulated.append(r)

    current = engine.score_candidate(focus_uid, current_partner, full_outgoing, current_incoming)
    current["outgoing_assets"] = sent_ids
    current["outgoing_asset_names"] = [a.get("name") for a in full_outgoing]
    current["candidate_type"] = "CURRENT_OFFER"
    current["outgoing_variant"] = "FULL"
    current["simulation"] = fast_sim(
        dl, model_inputs, baseline_lineups, baseline, focus_uid, current_partner,
        full_outgoing, current_incoming, args.quick_sims, args.seed
    )
    current["post_sim_score"] = engine.post_sim_score(current, focus_state)
    current["buyer_rationality"] = v16.buyer_rationality(current, dl)

    for r in simulated:
        r["comparison_to_current_offer"] = v13.compare_candidate(r, current)

    focal_ok = [r for r in simulated if v16.focal_viable(r)]
    bilateral = [r for r in focal_ok if r["buyer_rationality"]["current_state_viable"]]
    realistic = [r for r in bilateral if r["buyer_rationality"]["heuristic_acceptance_fit"] in {"HIGH", "MEDIUM"}]
    realistic.sort(key=lambda r: (
        float(r.get("post_sim_score") or 0.0),
        float(r["buyer_rationality"]["heuristic_acceptance_fit_score"]),
    ), reverse=True)

    low_fit = [r for r in bilateral if r["buyer_rationality"]["heuristic_acceptance_fit"] in {"LOW", "VERY_LOW"}]
    low_fit.sort(key=lambda r: float(r.get("post_sim_score") or 0.0), reverse=True)
    swing = low_fit[0] if low_fit else None
    if swing:
        swing["report_role"] = "SWING_FOR_FENCES"

    top5 = realistic[:5]
    if swing and len(top5) < 5:
        top5.append(swing)
    for i, r in enumerate(top5, 1):
        r["actionable_rank"] = i
        r.setdefault("report_role", "REALISTIC_BILATERAL")

    same_realistic = next((r for r in realistic if r["candidate_type"] == "SAME_PARTNER_COUNTER"), None)
    alt_realistic = next((r for r in realistic if r["candidate_type"] == "ALTERNATE_BUYER"), None)
    pivot = [r for r in focal_ok if not r["buyer_rationality"]["current_state_viable"]
             and r["buyer_rationality"]["state_change_viable"]]
    pivot.sort(key=lambda r: float(r.get("post_sim_score") or 0.0), reverse=True)

    # Only realistic (MEDIUM/HIGH fit) trades may drive the final action.
    if v16.focal_viable(current) and current["buyer_rationality"]["current_state_viable"]:
        best = realistic[0] if realistic else None
        action = "SHOP_BEFORE_ACCEPTING" if best and best.get("post_sim_score", 0) > current.get("post_sim_score", 0) + 750 else "ACCEPT_NOW"
    elif same_realistic:
        action = "COUNTER_CURRENT_OFFEROR"
    elif alt_realistic:
        action = "SHOP_BEFORE_ACCEPTING"
    else:
        action = "DECLINE"

    report = {
        "model_version": MODEL_VERSION,
        "scenario_id": scenario.get("scenario_id") or scenario_path.stem,
        "focus_user_id": focus_uid,
        "focus_team_state": focus_state,
        "current_offer_partner_user_id": current_partner,
        "outgoing_assets": sent_ids,
        "incoming_offer_assets": received_ids,
        "current_offer_evaluation": current,
        "top_5_alternatives": top5,
        "best_realistic_counter": realistic[0] if realistic else None,
        "best_same_partner_counter": same_realistic,
        "best_alternate_buyer": alt_realistic,
        "best_state_change_dependent": pivot[0] if pivot else None,
        "swing_for_fences": swing,
        "state_change_dependent_alternatives": pivot[:5],
        "recommended_next_action": action,
        "candidate_counts": {
            "enumerated_plausible": len(raw),
            "frontier_selected_for_simulation": len(selected),
            "simulated": len(simulated),
            "focal_guardrail_pass": len(focal_ok),
            "buyer_current_state_viable": len(bilateral),
            "realistic_acceptance_fit": len(realistic),
            "low_or_very_low_fit_bilateral": len(low_fit),
            "state_change_dependent": len(pivot),
        },
        "simulation": {
            "quick_sims": args.quick_sims,
            "confirm_sims": args.confirm_sims,
            "seed": args.seed,
            "simulator_model_version": simmod.MODEL_VERSION,
            "canonical_state_mutated": False,
            "execution_path": "acceptance_frontier_prescreen_then_fast_decision_lab",
            "lineup_reoptimization": "exact_slot_mask_dynamic_programming",
        },
        "policy": {
            "buyer_current_state_rationality_gate": True,
            "actionable_normal_slot_requires_acceptance_fit": ["HIGH", "MEDIUM"],
            "swing_for_fences_max_count": 1,
            "swing_for_fences_can_drive_action": False,
            "state_change_dependent_candidates_separated": True,
            "heuristic_acceptance_fit_not_probability": True,
            "acceptance_frontier_search": True,
            "focal_and_counterparty_must_both_pass": True,
            "fast_exact_lineup_dp": True,
            "full_report_artifact_required": True,
        },
    }
    Path(args.output).write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
