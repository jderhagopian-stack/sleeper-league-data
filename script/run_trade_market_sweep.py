#!/usr/bin/env python3
"""FSFFL Decision Lab Counter & Market Sweep Engine 1.0.

Generates model-driven same-partner counters and league-wide alternate-buyer
packages around the focal outgoing assets from an incoming trade scenario.
Canonical Sleeper, GM, and Simulator state is read-only.

The engine intentionally separates candidate generation from confirmation:
1. enumerate and GM-pre-screen plausible packages quickly;
2. run quick Decision Lab simulations only on the shortlist;
3. optionally run a larger confirmation simulation on the best candidate.
"""

from __future__ import annotations

import argparse
import importlib.util
import itertools
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

DATA = Path("data")
MODEL_VERSION = "FSFFL-Counter-Market-Sweep-1.0"
DEFAULT_QUICK_SIMS = 250
DEFAULT_CONFIRM_SIMS = 0
DEFAULT_SHORTLIST = 5
DEFAULT_FINALISTS = 3


def load_json(path: Path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, obj: Any):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, sort_keys=True), encoding="utf-8")


def import_decision_lab():
    path = Path("script/run_roster_decision_lab.py")
    spec = importlib.util.spec_from_file_location("fsffl_decision_lab", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to import Decision Lab from {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def franchise_index() -> Dict[str, Dict[str, Any]]:
    idx = load_json(DATA / "gm" / "franchise_index.json", {}) or {}
    return {str(x.get("user_id")): x for x in idx.get("teams") or []}


def team_doc(uid: str, key: str) -> Dict[str, Any]:
    row = franchise_index().get(str(uid)) or {}
    path = ((row.get("paths") or {}).get(key))
    return load_json(Path(path), {}) if path else {}


def command_center(uid: str) -> Dict[str, Any]:
    return team_doc(uid, "command_center")


def strategic_assets(uid: str) -> Dict[str, Dict[str, Any]]:
    doc = team_doc(uid, "strategic_asset_profiles")
    return {str(a.get("asset_id")): a for a in doc.get("assets") or []}


def asset_catalog() -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    raw = load_json(DATA / "fsffl_asset_values.json", {}) or {}
    players, picks = {}, {}
    for p in raw.get("players") or []:
        aid = f"player:{p.get('player_id')}"
        players[aid] = {
            "asset_id": aid,
            "asset_type": "player",
            "player_id": str(p.get("player_id")),
            "name": p.get("name") or aid,
            "position": p.get("position"),
            "market_dynasty": float(p.get("market_dynasty") or 0.0),
            "market_redraft": float(p.get("market_redraft") or 0.0),
            "fsffl_value": float(p.get("fsffl_value") or p.get("market_dynasty") or 0.0),
            "owner_user_id": str(p.get("current_owner_user_id")) if p.get("current_owner_user_id") is not None else None,
        }
    for p in raw.get("picks") or []:
        aid = str(p.get("asset_id") or "")
        if not aid:
            continue
        picks[aid] = {
            "asset_id": aid,
            "asset_type": "pick",
            "name": p.get("name") or aid,
            "position": None,
            "market_dynasty": float(p.get("market_dynasty") or p.get("fsffl_value") or 0.0),
            "market_redraft": 0.0,
            "fsffl_value": float(p.get("fsffl_value") or p.get("market_dynasty") or 0.0),
            "owner_user_id": str(p.get("current_owner_user_id")) if p.get("current_owner_user_id") is not None else None,
        }
    return players, picks


def roster_player_owners(rosters: List[Dict[str, Any]]) -> Dict[str, str]:
    out = {}
    for r in rosters:
        uid = str(r.get("owner_id"))
        for pid in r.get("players") or []:
            out[f"player:{pid}"] = uid
    return out


def original_pick_owner_id(asset_id: str, rosters: List[Dict[str, Any]]) -> str | None:
    # asset ids are conventionally pick:<year>:R<round>:orig<roster_id>
    marker = ":orig"
    if marker not in asset_id:
        return None
    try:
        rid = int(asset_id.split(marker, 1)[1])
    except ValueError:
        return None
    for r in rosters:
        if int(r.get("roster_id") or -1) == rid:
            return str(r.get("owner_id"))
    return None


def build_owner_assets(rosters: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    players, picks = asset_catalog()
    player_owners = roster_player_owners(rosters)
    out: Dict[str, List[Dict[str, Any]]] = {}
    for aid, a in players.items():
        uid = a.get("owner_user_id") or player_owners.get(aid)
        if uid:
            out.setdefault(str(uid), []).append(a)
    for aid, a in picks.items():
        uid = a.get("owner_user_id") or original_pick_owner_id(aid, rosters)
        if uid:
            out.setdefault(str(uid), []).append(a)
    return out


def incoming_trade_parts(scenario: Dict[str, Any], focus_uid: str):
    sent, received, partners = [], [], set()
    for action in scenario.get("actions") or []:
        if str(action.get("type") or "").lower() != "trade":
            continue
        src, dst = str(action.get("from_user_id")), str(action.get("to_user_id"))
        assets = [f"player:{x}" for x in action.get("players") or []] + [str(x) for x in action.get("picks") or []]
        if src == focus_uid:
            sent.extend(assets)
            partners.add(dst)
        if dst == focus_uid:
            received.extend(assets)
            partners.add(src)
    partners.discard(focus_uid)
    if not sent or len(partners) != 1:
        raise ValueError("Market Sweep 1.0 requires a bilateral incoming trade with focal outgoing assets")
    return sent, received, next(iter(partners))


def asset_value(asset: Dict[str, Any], owner_uid: str | None = None) -> Dict[str, float]:
    gm = strategic_assets(owner_uid) if owner_uid else {}
    g = gm.get(str(asset.get("asset_id"))) or {}
    market = float(g.get("market_dynasty") or asset.get("market_dynasty") or 0.0)
    redraft = float(g.get("market_redraft") or asset.get("market_redraft") or 0.0)
    base = float(g.get("base_franchise_value") or asset.get("fsffl_value") or market)
    bg = float(g.get("break_glass_value") or base)
    return {"market": market, "redraft": redraft, "base": base, "break_glass": bg}


def need_map(uid: str) -> Dict[str, float]:
    cc = command_center(uid)
    return {str(x.get("position")): float(x.get("need_score") or 0.0) for x in cc.get("biggest_position_needs") or []}


def team_state(uid: str) -> str:
    return str(command_center(uid).get("team_state") or (franchise_index().get(uid) or {}).get("team_state") or "unknown")


def package_key(assets: Iterable[Dict[str, Any]]) -> Tuple[str, ...]:
    return tuple(sorted(str(a.get("asset_id")) for a in assets))


def candidate_packages(assets: List[Dict[str, Any]], max_players=2, max_picks=2) -> Iterable[List[Dict[str, Any]]]:
    players = [a for a in assets if a.get("asset_type") == "player"]
    picks = [a for a in assets if a.get("asset_type") == "pick"]
    seen = set()
    for np in range(0, min(max_players, len(players)) + 1):
        for nk in range(0, min(max_picks, len(picks)) + 1):
            if np + nk == 0 or np + nk > 3:
                continue
            for pc in itertools.combinations(players, np):
                for kc in itertools.combinations(picks, nk):
                    pkg = list(pc + kc)
                    key = package_key(pkg)
                    if key not in seen:
                        seen.add(key)
                        yield pkg


def score_candidate(focus_uid: str, buyer_uid: str, outgoing: List[Dict[str, Any]], incoming: List[Dict[str, Any]]) -> Dict[str, Any]:
    focal_needs = need_map(focus_uid)
    buyer_needs = need_map(buyer_uid)
    buyer_gm = strategic_assets(buyer_uid)
    outgoing_market = sum(asset_value(a, focus_uid)["market"] for a in outgoing)
    outgoing_redraft = sum(asset_value(a, focus_uid)["redraft"] for a in outgoing)
    return_market = sum(asset_value(a, buyer_uid)["market"] for a in incoming)
    return_redraft = sum(asset_value(a, buyer_uid)["redraft"] for a in incoming)
    return_break = sum(asset_value(a, buyer_uid)["break_glass"] for a in incoming)
    need_gain = sum(focal_needs.get(str(a.get("position")), 0.0) * asset_value(a, buyer_uid)["redraft"] for a in incoming if a.get("asset_type") == "player")
    buyer_need_solved = sum(buyer_needs.get(str(a.get("position")), 0.0) * asset_value(a, focus_uid)["redraft"] for a in outgoing if a.get("asset_type") == "player")
    core_penalty = 0.0
    for a in incoming:
        g = buyer_gm.get(str(a.get("asset_id"))) or {}
        status = str(g.get("core_status") or "")
        if status == "franchise_cornerstone":
            core_penalty += 0.55
        elif status == "core_high_hold":
            core_penalty += 0.28
        elif status == "core_pick":
            core_penalty += 0.12
    value_ratio = return_market / max(outgoing_market, 1.0)
    buyer_cost_ratio = return_break / max(outgoing_market, 1.0)
    # Plausibility rewards buyer need and near-market balance, penalizes surrender of protected assets.
    plausibility_score = 1.0
    plausibility_score -= min(0.65, abs(value_ratio - 1.0) * 0.7)
    plausibility_score -= min(0.45, max(0.0, buyer_cost_ratio - 1.20) * 0.45)
    plausibility_score -= core_penalty
    plausibility_score += min(0.20, buyer_need_solved / 50000.0)
    plausibility_score = max(0.0, min(1.0, plausibility_score))
    if plausibility_score >= 0.68:
        plaus = "HIGH"
    elif plausibility_score >= 0.45:
        plaus = "MEDIUM"
    elif plausibility_score >= 0.25:
        plaus = "LOW"
    else:
        plaus = "THEORETICAL_ONLY"

    # Fast focal utility used only for pre-screening; Decision Lab simulation decides finalists.
    future_surplus = return_market - outgoing_market
    redraft_replacement = return_redraft - outgoing_redraft
    strategic_score = (
        future_surplus
        + 0.35 * redraft_replacement
        + 0.10 * need_gain
        + 1800.0 * plausibility_score
        - 900.0 * core_penalty
    )
    return {
        "buyer_user_id": buyer_uid,
        "buyer_team": command_center(buyer_uid).get("focal_team") or (franchise_index().get(buyer_uid) or {}).get("team_name"),
        "buyer_state": team_state(buyer_uid),
        "return_assets": [a.get("asset_id") for a in incoming],
        "return_asset_names": [a.get("name") for a in incoming],
        "market_dynasty_delta_pre_screen": round(future_surplus, 2),
        "market_redraft_delta_pre_screen": round(redraft_replacement, 2),
        "focal_need_gain_score": round(need_gain, 2),
        "buyer_need_solved_score": round(buyer_need_solved, 2),
        "plausibility_score": round(plausibility_score, 4),
        "plausibility": plaus,
        "pre_screen_score": round(strategic_score, 2),
    }


def scenario_actions(focus_uid: str, buyer_uid: str, outgoing: List[Dict[str, Any]], incoming: List[Dict[str, Any]]):
    out_players = [a["player_id"] for a in outgoing if a.get("asset_type") == "player"]
    out_picks = [a["asset_id"] for a in outgoing if a.get("asset_type") == "pick"]
    in_players = [a["player_id"] for a in incoming if a.get("asset_type") == "player"]
    in_picks = [a["asset_id"] for a in incoming if a.get("asset_type") == "pick"]
    return [
        {"type": "trade", "from_user_id": focus_uid, "to_user_id": buyer_uid, "players": out_players, "picks": out_picks},
        {"type": "trade", "from_user_id": buyer_uid, "to_user_id": focus_uid, "players": in_players, "picks": in_picks},
    ]


def simulate_candidate(dl, model_inputs, baseline_lineups, baseline, focus_uid, buyer_uid, outgoing, incoming, sims, seed):
    simmod, league, canonical_rosters, users, players, season, projections, raw_schedule = model_inputs
    actions = scenario_actions(focus_uid, buyer_uid, outgoing, incoming)
    hypothetical_rosters, _ = dl.apply_actions(canonical_rosters, actions)
    touched = dl.touched_users(focus_uid, actions)
    hypothetical_lineups, reoptimized = dl.reoptimize_touched_lineups(
        simmod, baseline_lineups, hypothetical_rosters, touched, league, users, players, projections
    )
    hyp = dl.simulate_from_lineups(simmod, league, hypothetical_rosters, users, raw_schedule, hypothetical_lineups, sims, seed)
    bidx, hidx = dl.team_index(baseline), dl.team_index(hyp)
    b, h = bidx[focus_uid], hidx[focus_uid]
    ob, oh = bidx.get(buyer_uid), hidx.get(buyer_uid)
    strategic = dl.strategic_summary(focus_uid, actions)
    title_delta = dl.delta(b.get("championship_probability"), h.get("championship_probability"))
    buyer_title_delta = dl.delta(ob.get("championship_probability"), oh.get("championship_probability")) if ob and oh else 0.0
    return {
        "actions": actions,
        "teams_reoptimized": reoptimized,
        "focus_before": b,
        "focus_after": h,
        "focus_delta": {
            "expected_wins": dl.delta(b.get("expected_wins"), h.get("expected_wins")),
            "expected_points_for": dl.delta(b.get("expected_points_for"), h.get("expected_points_for")),
            "playoff_probability": dl.delta(b.get("playoff_probability"), h.get("playoff_probability")),
            "bye_probability": dl.delta(b.get("bye_probability"), h.get("bye_probability")),
            "championship_probability": title_delta,
        },
        "buyer_championship_probability_delta": buyer_title_delta,
        "net_title_equity_swing_against_focus": round(max(0.0, float(buyer_title_delta or 0.0)) - float(title_delta or 0.0), 5),
        "strategic": strategic,
    }


def contender_title_cap(state: str) -> float | None:
    return {"elite_contender": 0.025, "contender": 0.03, "retool": 0.06}.get(state)


def post_sim_score(row: Dict[str, Any], state: str) -> float:
    sim = row.get("simulation") or {}
    d = sim.get("focus_delta") or {}
    s = sim.get("strategic") or {}
    title = float(d.get("championship_probability") or 0.0)
    playoff = float(d.get("playoff_probability") or 0.0)
    dynasty = float(s.get("market_dynasty_delta") or 0.0)
    break_glass = float(s.get("break_glass_delta") or 0.0)
    externality = float(sim.get("net_title_equity_swing_against_focus") or 0.0)
    score = dynasty + 0.35 * break_glass + 25000.0 * title + 5000.0 * playoff - 12000.0 * externality
    cap = contender_title_cap(state)
    if cap is not None and title < -cap:
        score -= 12000.0 + 50000.0 * abs(title + cap)
        row["championship_equity_constraint"] = "FAIL"
    else:
        row["championship_equity_constraint"] = "PASS"
    return round(score, 2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", required=True, help="Incoming bilateral trade scenario JSON")
    ap.add_argument("--quick-sims", type=int, default=DEFAULT_QUICK_SIMS)
    ap.add_argument("--confirm-sims", type=int, default=DEFAULT_CONFIRM_SIMS)
    ap.add_argument("--shortlist", type=int, default=DEFAULT_SHORTLIST)
    ap.add_argument("--finalists", type=int, default=DEFAULT_FINALISTS)
    ap.add_argument("--output", default=None)
    ap.add_argument("--seed", type=int, default=20260821)
    args = ap.parse_args()
    if args.quick_sims < 100:
        raise ValueError("--quick-sims must be at least 100")

    scenario_path = Path(args.scenario)
    scenario = load_json(scenario_path, {}) or {}
    focus_uid = str(scenario.get("focus_user_id") or "")
    if not focus_uid:
        raise ValueError("scenario.focus_user_id is required")

    dl = import_decision_lab()
    model_inputs = dl.load_model_inputs()
    simmod, league, rosters, users, players, season, projections, raw_schedule = model_inputs
    baseline_lineups = dl.load_cached_lineups(season)
    baseline = dl.simulate_from_lineups(simmod, league, rosters, users, raw_schedule, baseline_lineups, args.quick_sims, args.seed)

    sent_ids, received_ids, current_partner = incoming_trade_parts(scenario, focus_uid)
    player_catalog, pick_catalog = asset_catalog()
    catalog = {**player_catalog, **pick_catalog}
    outgoing = [catalog[x] for x in sent_ids if x in catalog]
    if len(outgoing) != len(sent_ids):
        missing = [x for x in sent_ids if x not in catalog]
        raise ValueError(f"Outgoing assets missing from FSFFL asset catalog: {missing}")

    owner_assets = build_owner_assets(rosters)
    idx = franchise_index()
    raw_candidates = []
    for buyer_uid in idx:
        if buyer_uid == focus_uid:
            continue
        assets = owner_assets.get(buyer_uid) or []
        # Keep search finite and relevant: top liquid/value assets and all picks.
        player_pool = sorted([a for a in assets if a.get("asset_type") == "player"], key=lambda a: a.get("market_dynasty", 0), reverse=True)[:10]
        pick_pool = sorted([a for a in assets if a.get("asset_type") == "pick"], key=lambda a: a.get("market_dynasty", 0), reverse=True)[:8]
        for pkg in candidate_packages(player_pool + pick_pool):
            row = score_candidate(focus_uid, buyer_uid, outgoing, pkg)
            if row["plausibility"] == "THEORETICAL_ONLY":
                continue
            row["candidate_type"] = "SAME_PARTNER_COUNTER" if buyer_uid == current_partner else "ALTERNATE_BUYER"
            raw_candidates.append((row, pkg))

    raw_candidates.sort(key=lambda x: x[0]["pre_screen_score"], reverse=True)
    # Diversify shortlist so the current partner and alternate market both get a chance.
    selected = []
    seen_keys = set()
    current_rows = [x for x in raw_candidates if x[0]["candidate_type"] == "SAME_PARTNER_COUNTER"]
    alternate_rows = [x for x in raw_candidates if x[0]["candidate_type"] == "ALTERNATE_BUYER"]
    for bucket in (current_rows[: max(2, args.shortlist // 2)], alternate_rows[: args.shortlist]):
        for row, pkg in bucket:
            key = (row["buyer_user_id"], package_key(pkg))
            if key in seen_keys:
                continue
            seen_keys.add(key)
            selected.append((row, pkg))
            if len(selected) >= args.shortlist:
                break
        if len(selected) >= args.shortlist:
            break

    state = team_state(focus_uid)
    simulated = []
    for row, pkg in selected:
        row = dict(row)
        row["simulation"] = simulate_candidate(
            dl, model_inputs, baseline_lineups, baseline, focus_uid, row["buyer_user_id"], outgoing, pkg, args.quick_sims, args.seed
        )
        row["post_sim_score"] = post_sim_score(row, state)
        simulated.append(row)
    simulated.sort(key=lambda x: x["post_sim_score"], reverse=True)
    finalists = simulated[: args.finalists]

    if args.confirm_sims >= 100 and finalists:
        # Confirm only the top candidate at higher fidelity to preserve latency.
        top = finalists[0]
        pkg = [catalog[x] for x in top["return_assets"] if x in catalog]
        confirm_baseline = dl.simulate_from_lineups(simmod, league, rosters, users, raw_schedule, baseline_lineups, args.confirm_sims, args.seed)
        top["confirmation_simulation"] = simulate_candidate(
            dl, model_inputs, baseline_lineups, confirm_baseline, focus_uid, top["buyer_user_id"], outgoing, pkg, args.confirm_sims, args.seed
        )

    same_partner = [x for x in finalists if x["candidate_type"] == "SAME_PARTNER_COUNTER"]
    alternates = [x for x in finalists if x["candidate_type"] == "ALTERNATE_BUYER"]
    if not finalists:
        action = "DECLINE"
    elif alternates and (not same_partner or alternates[0]["post_sim_score"] > same_partner[0]["post_sim_score"] + 750):
        action = "SHOP_BEFORE_ACCEPTING"
    elif same_partner:
        action = "COUNTER_CURRENT_OFFEROR"
    else:
        action = "SHOP_BEFORE_ACCEPTING"

    report = {
        "model_version": MODEL_VERSION,
        "scenario_id": scenario.get("scenario_id") or scenario_path.stem,
        "focus_user_id": focus_uid,
        "focus_team_state": state,
        "current_offer_partner_user_id": current_partner,
        "outgoing_assets": sent_ids,
        "incoming_offer_assets": received_ids,
        "simulation": {
            "quick_sims": args.quick_sims,
            "confirm_sims": args.confirm_sims,
            "seed": args.seed,
            "simulator_model_version": simmod.MODEL_VERSION,
            "canonical_state_mutated": False,
            "execution_path": "gm_prescreen_then_decision_lab_shortlist",
        },
        "candidate_counts": {
            "enumerated_plausible": len(raw_candidates),
            "simulated_shortlist": len(simulated),
            "finalists": len(finalists),
        },
        "same_partner_counteroffers": [x for x in finalists if x["candidate_type"] == "SAME_PARTNER_COUNTER"],
        "alternate_buyer_candidates": [x for x in finalists if x["candidate_type"] == "ALTERNATE_BUYER"],
        "ranked_finalists": finalists,
        "recommended_next_action": action,
        "policy": {
            "contender_title_loss_cap": contender_title_cap(state),
            "acceptance_requires_market_sweep": True,
            "conditional_state_logic": True,
        },
    }
    output = Path(args.output) if args.output else DATA / "decision_lab" / "outputs" / f"{report['scenario_id']}_market_sweep.json"
    write_json(output, report)
    print(json.dumps(report, indent=2))
    print(f"\nWrote Counter & Market Sweep report: {output}")


if __name__ == "__main__":
    main()
