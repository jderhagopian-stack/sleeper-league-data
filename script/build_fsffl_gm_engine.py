#!/usr/bin/env python3
"""
FSFFL GM Engine v1.1 overlay

Runs on top of build_fsffl_gm_engine.py and corrects three decision-layer issues:
1) independently optimizes every legal starting lineup instead of trusting Sleeper's
   preseason `starters` field;
2) ranks candidate trade packages by Hurts So Good surplus and actual optimal-lineup
   improvement before seller acceptance fit;
3) builds a sell-leverage board showing which opponent values each HSG player most.

The v1.0 module remains the market/data foundation. This file monkey-patches the
relevant decision functions, then invokes its normal main() so all standard files are
rebuilt with the corrected logic.
"""

from __future__ import annotations

import functools
import itertools
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

import build_fsffl_gm_engine as gm

DATA = Path("data")

# Explicit model bump for generated files.
gm.CONFIG["model_version"] = "GM-1.1"
gm.CONFIG["notes"] = list(gm.CONFIG.get("notes") or []) + [
    "GM-1.1 independently optimizes legal starting lineups; Sleeper's current starters are not treated as authoritative.",
    "GM-1.1 ranks trade packages by HSG surplus and optimal-lineup gain before acceptance fit.",
    "GM-1.1 creates a sell-leverage board across every opponent valuation.",
]

FALLBACK_LINEUP_SLOTS = [
    "QB", "RB", "RB", "WR", "WR", "WR", "TE", "FLEX", "SUPER_FLEX"
]


def lineup_slots() -> List[str]:
    league = gm.load_json(DATA / "league.json", {}) or {}
    raw = league.get("roster_positions") or FALLBACK_LINEUP_SLOTS
    slots = [str(x).upper() for x in raw]
    # Sleeper roster_positions contains bench/IR/taxi entries too.
    legal = {"QB", "RB", "WR", "TE", "FLEX", "SUPER_FLEX", "SUPERFLEX"}
    filtered = ["SUPER_FLEX" if x == "SUPERFLEX" else x for x in slots if x in legal]
    return filtered or list(FALLBACK_LINEUP_SLOTS)


LINEUP_SLOTS = lineup_slots()


def eligible(position: str, slot: str) -> bool:
    pos = (position or "").upper()
    slot = slot.upper()
    if slot in {"QB", "RB", "WR", "TE"}:
        return pos == slot
    if slot == "FLEX":
        return pos in {"RB", "WR", "TE"}
    if slot == "SUPER_FLEX":
        return pos in {"QB", "RB", "WR", "TE"}
    return False


def optimize_lineup(
    player_ids: Iterable[str],
    player_values: Dict[str, Dict[str, Any]],
    value_key: str = "market_redraft",
) -> Dict[str, Any]:
    """Exact maximum-value legal lineup for the configured FSFFL starter slots."""
    pids = [str(pid) for pid in player_ids if str(pid) != "0"]
    # Players with no value can still fill a legal slot, so keep them.
    candidates = []
    for pid in pids:
        a = player_values.get(pid, {})
        pos = a.get("position")
        if pos in gm.POSITIONS:
            candidates.append(pid)

    # Constrained slots first dramatically reduces the DP state space.
    priority = {"QB": 0, "RB": 0, "WR": 0, "TE": 0, "FLEX": 1, "SUPER_FLEX": 2}
    ordered_slots = sorted(enumerate(LINEUP_SLOTS), key=lambda x: (priority.get(x[1], 3), x[0]))
    ordered_slot_names = tuple(slot for _, slot in ordered_slots)

    @functools.lru_cache(maxsize=None)
    def solve(slot_i: int, used_mask: int) -> Tuple[float, Tuple[int, ...]]:
        if slot_i >= len(ordered_slot_names):
            return 0.0, ()
        slot = ordered_slot_names[slot_i]
        best_value = float("-inf")
        best_choice: Tuple[int, ...] = ()
        for i, pid in enumerate(candidates):
            if used_mask & (1 << i):
                continue
            a = player_values.get(pid, {})
            if not eligible(a.get("position"), slot):
                continue
            rest_value, rest_choice = solve(slot_i + 1, used_mask | (1 << i))
            if rest_value == float("-inf"):
                continue
            v = gm.safe_float(a.get(value_key)) + rest_value
            if v > best_value:
                best_value = v
                best_choice = (i,) + rest_choice
        # An invalid/incomplete lineup is intentionally very bad.
        return best_value, best_choice

    total, choice = solve(0, 0)
    if total == float("-inf") or len(choice) != len(ordered_slot_names):
        # Graceful fallback for pathological rosters: greedily fill what can be filled.
        used = set()
        rows = []
        total = 0.0
        for slot in ordered_slot_names:
            opts = [
                pid for pid in candidates
                if pid not in used and eligible(player_values.get(pid, {}).get("position"), slot)
            ]
            if not opts:
                rows.append({"slot": slot, "player_id": None, "name": None, "position": None, "value": 0.0})
                continue
            pid = max(opts, key=lambda x: gm.safe_float(player_values.get(x, {}).get(value_key)))
            used.add(pid)
            a = player_values.get(pid, {})
            val = gm.safe_float(a.get(value_key))
            total += val
            rows.append({"slot": slot, "player_id": pid, "name": a.get("name"), "position": a.get("position"), "value": round(val, 1)})
        return {"total": round(total, 1), "player_ids": [r["player_id"] for r in rows if r["player_id"]], "lineup": rows, "complete": all(r["player_id"] for r in rows)}

    chosen = [candidates[i] for i in choice]
    rows = []
    for slot, pid in zip(ordered_slot_names, chosen):
        a = player_values.get(pid, {})
        rows.append({
            "slot": slot,
            "player_id": pid,
            "name": a.get("name"),
            "position": a.get("position"),
            "value": round(gm.safe_float(a.get(value_key)), 1),
        })
    return {"total": round(total, 1), "player_ids": chosen, "lineup": rows, "complete": True}


def optimized_starter_sets(rosters: List[Dict[str, Any]]) -> Dict[str, set]:
    # gm.main calls this after player_values exists, but the original signature does not
    # provide values. We cache the most recent values from optimized_team_strengths.
    values = getattr(optimized_starter_sets, "player_values", {})
    out = {}
    for r in rosters:
        uid = str(r.get("owner_id"))
        result = optimize_lineup(r.get("players") or [], values, "market_redraft")
        out[uid] = set(result["player_ids"])
    return out


def optimized_team_strengths(
    rosters: List[Dict[str, Any]],
    player_values: Dict[str, Dict[str, Any]],
    profile_by_uid: Dict[str, Dict[str, Any]],
):
    optimized_starter_sets.player_values = player_values
    raw = {}
    for r in rosters:
        uid = str(r.get("owner_id"))
        all_players = [str(x) for x in (r.get("players") or []) if str(x) != "0"]
        redraft_opt = optimize_lineup(all_players, player_values, "market_redraft")
        dynasty_opt = optimize_lineup(all_players, player_values, "market_dynasty")
        starters = set(redraft_opt["player_ids"])
        bench = [x for x in all_players if x not in starters]

        bench_redraft = sorted((gm.safe_float(player_values.get(x, {}).get("market_redraft")) for x in bench), reverse=True)
        bench_dynasty = sorted((gm.safe_float(player_values.get(x, {}).get("market_dynasty")) for x in bench), reverse=True)
        immediate_strength = redraft_opt["total"] + 0.20 * sum(bench_redraft[:5])
        dynasty_strength = dynasty_opt["total"] + 0.18 * sum(bench_dynasty[:6])

        pos_starter = defaultdict(float)
        pos_depth = defaultdict(list)
        for pid in all_players:
            a = player_values.get(pid, {})
            pos = a.get("position")
            if pos not in gm.POSITIONS:
                continue
            pos_depth[pos].append(gm.safe_float(a.get("market_redraft")))
        for pid in starters:
            a = player_values.get(pid, {})
            pos = a.get("position")
            if pos in gm.POSITIONS:
                pos_starter[pos] += gm.safe_float(a.get("market_redraft"))

        raw[uid] = {
            "user_id": uid,
            "manager": gm.manager_label(uid, profile_by_uid),
            "team_name": gm.team_label(uid, profile_by_uid),
            "starter_redraft_value": redraft_opt["total"],
            "starter_dynasty_value": dynasty_opt["total"],
            "immediate_strength_raw": immediate_strength,
            "dynasty_strength_raw": dynasty_strength,
            "pos_starter_raw": dict(pos_starter),
            "pos_depth_values": {p: sorted(v, reverse=True) for p, v in pos_depth.items()},
            "optimal_redraft_lineup": redraft_opt,
            "optimal_dynasty_lineup": dynasty_opt,
        }

    immediate_values = [x["immediate_strength_raw"] for x in raw.values()]
    dynasty_values = [x["dynasty_strength_raw"] for x in raw.values()]
    pos_distributions = {
        pos: [x["pos_starter_raw"].get(pos, 0.0) for x in raw.values()]
        for pos in gm.POSITIONS
    }

    out = {}
    for uid, x in raw.items():
        contender = gm.percentile_rank(x["immediate_strength_raw"], immediate_values)
        dynasty_pct = gm.percentile_rank(x["dynasty_strength_raw"], dynasty_values)
        if contender >= 0.75:
            tier = "elite_contender"
        elif contender >= 0.50:
            tier = "contender"
        elif contender >= 0.25:
            tier = "middle"
        else:
            tier = "retool_rebuild"

        needs = {}
        for pos in gm.POSITIONS:
            starter_strength = x["pos_starter_raw"].get(pos, 0.0)
            starter_pct = gm.percentile_rank(starter_strength, pos_distributions[pos])
            depth_vals = x["pos_depth_values"].get(pos, [])
            n = 3 if pos in ("RB", "WR") else 2
            depth_score = sum(depth_vals[:n])
            all_depth_scores = [sum(z["pos_depth_values"].get(pos, [])[:n]) for z in raw.values()]
            depth_pct = gm.percentile_rank(depth_score, all_depth_scores)
            needs[pos] = round(gm.clamp(0.65 * (1 - starter_pct) + 0.35 * (1 - depth_pct), 0, 1), 3)

        out[uid] = {
            "user_id": uid,
            "manager": x["manager"],
            "team_name": x["team_name"],
            "contender_score": round(contender, 3),
            "dynasty_roster_score": round(dynasty_pct, 3),
            "competitive_tier": tier,
            "starter_redraft_value": round(x["starter_redraft_value"], 1),
            "starter_dynasty_value": round(x["starter_dynasty_value"], 1),
            "position_need": needs,
            "lineup_source": "independently_optimized_legal_lineup",
            "optimal_redraft_lineup": x["optimal_redraft_lineup"],
            "optimal_dynasty_lineup": x["optimal_dynasty_lineup"],
        }
    return out


def optimized_current_starting_lineup_value(uid, rosters, player_values):
    for r in rosters:
        if str(r.get("owner_id")) == str(uid):
            return optimize_lineup(r.get("players") or [], player_values, "market_redraft")["total"]
    return 0.0


def lineup_after_trade_utility(hsg_uid, outgoing_asset_ids, target_asset, rosters, player_values):
    roster_players = None
    for r in rosters:
        if str(r.get("owner_id")) == str(hsg_uid):
            roster_players = [str(x) for x in (r.get("players") or [])]
            break
    if roster_players is None:
        return 0.0, {"available": False}

    before = optimize_lineup(roster_players, player_values, "market_redraft")
    outgoing_players = {
        aid.split(":", 1)[1]
        for aid in outgoing_asset_ids
        if aid.startswith("player:")
    }
    after_players = [pid for pid in roster_players if pid not in outgoing_players]
    target_pid = str(target_asset.get("player_id"))
    temp_values = player_values
    if target_pid and target_pid not in after_players:
        after_players.append(target_pid)
    after = optimize_lineup(after_players, temp_values, "market_redraft")
    delta = after["total"] - before["total"]
    cap = gm.CONFIG["championship_utility"]["max_trade_utility_adjustment"]
    utility = gm.clamp((delta / before["total"] * 6.0) if before["total"] else 0.0, -cap, cap)
    return utility, {
        "available": True,
        "base_optimal_lineup_redraft_value": round(before["total"], 1),
        "post_trade_optimal_lineup_redraft_value": round(after["total"], 1),
        "optimal_lineup_value_gain": round(delta, 1),
        "championship_utility_adjustment": round(utility, 4),
        "post_trade_optimal_lineup": after["lineup"],
    }


def build_hsg_trade_opportunities_v11(
    rosters,
    player_values,
    pick_assets,
    owner_by_player,
    team_profiles,
    owner_matrix,
    profile_by_uid,
    profiles,
    usage=None,
    snaps=None,
    manual=None,
):
    user_uid = None
    for uid, p in profile_by_uid.items():
        if p.get("manager") == gm.USER_MANAGER or p.get("username") == gm.USER_MANAGER or p.get("team_name") == gm.USER_TEAM:
            user_uid = uid
            break
    if not user_uid:
        return {"error": f"Could not locate {gm.USER_MANAGER}/{gm.USER_TEAM}"}

    val = gm.matrix_lookup(owner_matrix)
    hsg_assets = gm.owner_current_assets(user_uid, rosters, pick_assets)
    outgoing_candidates = []
    for aid in hsg_assets:
        meta = gm.asset_metadata(aid, player_values, pick_assets)
        if aid.startswith("player:") and meta.get("name") in gm.PROTECTED_HSG_PLAYERS:
            continue
        if val[user_uid].get(aid, 0) > 0:
            outgoing_candidates.append(aid)
    outgoing_candidates.sort(key=lambda a: val[user_uid].get(a, 0), reverse=True)
    outgoing_candidates = outgoing_candidates[:16]

    profile_trade = {str(p.get("user_id")): (p.get("trade_profile") or {}) for p in profiles}
    opportunities = []

    for pid, target in player_values.items():
        seller_uid = owner_by_player.get(pid)
        if not seller_uid or seller_uid == user_uid:
            continue
        target_aid = f"player:{pid}"
        hsg_value = val[user_uid].get(target_aid, 0)
        seller_hold = val[seller_uid].get(target_aid, 0)
        if hsg_value <= 0 or seller_hold <= 0:
            continue

        pos = target.get("position")
        need = gm.safe_float(team_profiles[user_uid]["position_need"].get(pos), 0.5)
        if target.get("market_dynasty", 0) < 1500 and need < 0.65:
            continue

        seller_trade = profile_trade.get(seller_uid, {})
        activity = gm.safe_float(seller_trade.get("total_trades"))
        recent = gm.safe_float(seller_trade.get("recent_trades_2025_2026"))
        activity_score = gm.clamp(0.6 * min(activity / 40, 1) + 0.4 * min(recent / 15, 1), 0, 1)

        package_rows = []
        for n in (1, 2, 3):
            for combo in itertools.combinations(outgoing_candidates, n):
                seller_values = [val[seller_uid].get(a, 0) for a in combo]
                hsg_costs = [val[user_uid].get(a, 0) for a in combo]
                if any(v <= 0 for v in seller_values):
                    continue
                seller_effective = gm.effective_package_value(seller_values)
                ratio = seller_effective / seller_hold if seller_hold else 0
                # Wider search band so surplus-positive creative offers are not prematurely discarded.
                if ratio < 0.84 or ratio > 1.16:
                    continue
                hsg_cost = sum(hsg_costs)
                hsg_surplus = hsg_value - hsg_cost
                fairness = 1 - min(abs(1.0 - ratio), 0.30) / 0.30
                complexity_bonus = 0.03 if len(combo) == 2 else (-0.03 if len(combo) == 3 else 0)
                acceptance_fit = gm.clamp(0.64 * fairness + 0.30 * activity_score + complexity_bonus, 0, 1)
                championship_utility, championship_meta = lineup_after_trade_utility(
                    user_uid, list(combo), target, rosters, player_values
                )
                lineup_gain = gm.safe_float(championship_meta.get("optimal_lineup_value_gain"))

                # Decision score explicitly prioritizes our economics, then lineup gain,
                # then the probability-shaped acceptance heuristic.
                normalized_surplus = hsg_surplus / max(hsg_value, 1.0)
                normalized_lineup = lineup_gain / max(championship_meta.get("base_optimal_lineup_redraft_value", 1.0), 1.0)
                decision_score = (
                    0.58 * normalized_surplus
                    + 0.27 * normalized_lineup
                    + 0.15 * acceptance_fit
                )
                severe_overpay = hsg_surplus < -0.12 * hsg_value
                recommendation_band = (
                    "strong_candidate" if hsg_surplus >= 0 and lineup_gain > 0
                    else "negotiation_candidate" if hsg_surplus >= -0.06 * hsg_value and lineup_gain > 0
                    else "overpay" if severe_overpay
                    else "low_priority"
                )

                package_rows.append({
                    "outgoing_asset_ids": list(combo),
                    "outgoing_assets": [gm.asset_metadata(a, player_values, pick_assets).get("name") for a in combo],
                    "seller_perceived_effective_value": round(seller_effective, 1),
                    "seller_hold_value_target": round(seller_hold, 1),
                    "seller_value_ratio": round(ratio, 3),
                    "hsg_hold_cost": round(hsg_cost, 1),
                    "hsg_value_of_target": round(hsg_value, 1),
                    "hsg_modeled_surplus": round(hsg_surplus, 1),
                    "acceptance_fit_score": round(acceptance_fit, 3),
                    "championship_utility_score": round(championship_utility, 4),
                    "championship_utility": championship_meta,
                    "decision_score": round(decision_score, 5),
                    "recommendation_band": recommendation_band,
                })

        band_rank = {"strong_candidate": 3, "negotiation_candidate": 2, "low_priority": 1, "overpay": 0}
        package_rows.sort(
            key=lambda x: (
                band_rank.get(x["recommendation_band"], 0),
                x["hsg_modeled_surplus"],
                gm.safe_float((x.get("championship_utility") or {}).get("optimal_lineup_value_gain")),
                x["acceptance_fit_score"],
                x["decision_score"],
            ),
            reverse=True,
        )

        best = package_rows[0] if package_rows else None
        opportunities.append({
            "target_player_id": pid,
            "target_player": target.get("name"),
            "position": pos,
            "seller_user_id": seller_uid,
            "seller_manager": gm.manager_label(seller_uid, profile_by_uid),
            "seller_team": gm.team_label(seller_uid, profile_by_uid),
            "market_value": round(gm.safe_float(target.get("market_dynasty")), 1),
            "fsffl_value": round(gm.fsffl_league_value(target), 1),
            "hsg_value": round(hsg_value, 1),
            "seller_hold_value": round(seller_hold, 1),
            "hsg_position_need": round(need, 3),
            "seller_trade_activity_score": round(activity_score, 3),
            "target_value_gap_hsg_minus_seller": round(hsg_value - seller_hold, 1),
            "best_candidate_packages": package_rows[:10],
            "best_package_decision_score": best.get("decision_score") if best else None,
            "best_package_recommendation_band": best.get("recommendation_band") if best else None,
        })

    opportunities.sort(
        key=lambda x: (
            1 if x.get("best_package_recommendation_band") == "strong_candidate" else 0,
            gm.safe_float(x.get("best_package_decision_score"), -999),
            x["hsg_position_need"],
            x["hsg_value"],
        ),
        reverse=True,
    )

    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "model_version": "GM-1.1",
        "user_id": user_uid,
        "manager": gm.USER_MANAGER,
        "team_name": gm.USER_TEAM,
        "protected_players_excluded_from_auto_offers": sorted(gm.PROTECTED_HSG_PLAYERS),
        "methodology_note": (
            "GM-1.1 ranks HSG surplus first, optimal legal-lineup improvement second, "
            "and seller acceptance fit third. Packages marked overpay are retained only "
            "for price discovery and should not be interpreted as recommendations."
        ),
        "opportunities": opportunities[:80],
    }


def build_sell_leverage_board():
    owner_payload = gm.load_json(DATA / "owner_perceived_values.json", {}) or {}
    owners = owner_payload.get("owners") or {}
    assets_payload = gm.load_json(DATA / "fsffl_asset_values.json", {}) or {}
    team_payload = gm.load_json(DATA / "team_contender_profiles.json", {}) or {}
    teams = {str(x.get("user_id")): x for x in team_payload.get("teams") or []}

    hsg_uid = None
    for uid, block in owners.items():
        if block.get("manager") == gm.USER_MANAGER or block.get("team_name") == gm.USER_TEAM:
            hsg_uid = str(uid)
            break
    if not hsg_uid:
        return {"error": "Could not locate Hurts So Good"}

    player_meta = {str(x.get("player_id")): x for x in assets_payload.get("players") or []}
    owner_asset_values = {}
    for uid, block in owners.items():
        owner_asset_values[str(uid)] = {
            x.get("asset_id"): gm.safe_float(x.get("owner_perceived_value"))
            for x in block.get("assets") or []
        }

    rows = []
    for pid, meta in player_meta.items():
        if str(meta.get("current_owner_user_id")) != hsg_uid:
            continue
        aid = f"player:{pid}"
        hsg_hold = owner_asset_values.get(hsg_uid, {}).get(aid, 0.0)
        market = gm.safe_float(meta.get("market_dynasty"))
        buyers = []
        for uid, vals in owner_asset_values.items():
            if uid == hsg_uid:
                continue
            value = vals.get(aid, 0.0)
            if value <= 0:
                continue
            buyers.append({
                "buyer_user_id": uid,
                "buyer_manager": (owners.get(uid) or {}).get("manager"),
                "buyer_team": (owners.get(uid) or {}).get("team_name"),
                "buyer_perceived_value": round(value, 1),
                "premium_vs_market": round(value - market, 1),
                "premium_vs_hsg_hold": round(value - hsg_hold, 1),
                "buyer_position_need": round(gm.safe_float((teams.get(uid, {}).get("position_need") or {}).get(meta.get("position")), 0.5), 3),
            })
        buyers.sort(key=lambda x: (x["premium_vs_hsg_hold"], x["premium_vs_market"], x["buyer_position_need"]), reverse=True)
        best = buyers[0] if buyers else None
        rows.append({
            "player_id": pid,
            "player": meta.get("name"),
            "position": meta.get("position"),
            "protected_core": meta.get("name") in gm.PROTECTED_HSG_PLAYERS,
            "market_value": round(market, 1),
            "hsg_hold_value": round(hsg_hold, 1),
            "best_buyer": best,
            "top_buyers": buyers[:5],
            "positive_arbitrage_vs_hsg_hold": bool(best and best["premium_vs_hsg_hold"] > 0),
        })

    rows.sort(
        key=lambda x: (
            not x["protected_core"],
            gm.safe_float((x.get("best_buyer") or {}).get("premium_vs_hsg_hold"), -99999),
            gm.safe_float((x.get("best_buyer") or {}).get("premium_vs_market"), -99999),
        ),
        reverse=True,
    )
    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "model_version": "GM-1.1",
        "user_id": hsg_uid,
        "manager": gm.USER_MANAGER,
        "team_name": gm.USER_TEAM,
        "methodology_note": (
            "For every HSG player, compares each opponent's modeled acquire value with "
            "market value and HSG's own hold value. Protected-core players are analyzed "
            "for information but remain excluded from automatic outgoing packages."
        ),
        "players": rows,
    }


def write_optimal_lineup_index():
    payload = gm.load_json(DATA / "team_contender_profiles.json", {}) or {}
    teams = payload.get("teams") or []
    out = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "model_version": "GM-1.1",
        "lineup_slots": LINEUP_SLOTS,
        "teams": [
            {
                "user_id": t.get("user_id"),
                "manager": t.get("manager"),
                "team_name": t.get("team_name"),
                "optimal_redraft_lineup": t.get("optimal_redraft_lineup"),
                "optimal_dynasty_lineup": t.get("optimal_dynasty_lineup"),
            }
            for t in teams
        ],
    }
    gm.write_json(DATA / "optimal_lineups.json", out)


def main():
    # Patch the v1.0 decision layer while retaining its tested data/market plumbing.
    gm.build_team_strengths = optimized_team_strengths
    gm.starter_sets = optimized_starter_sets
    gm.current_starting_lineup_value = optimized_current_starting_lineup_value
    gm.hsg_trade_championship_utility = lineup_after_trade_utility
    gm.build_hsg_trade_opportunities = build_hsg_trade_opportunities_v11

    gm.main()

    gm.write_json(DATA / "sell_leverage_board.json", build_sell_leverage_board())
    write_optimal_lineup_index()

    print("FSFFL GM Engine v1.1 overlay complete.")
    print("Wrote data/sell_leverage_board.json")
    print("Wrote data/optimal_lineups.json")


if __name__ == "__main__":
    main()
