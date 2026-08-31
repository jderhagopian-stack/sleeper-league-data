#!/usr/bin/env python3
"""FSFFL Decision Lab Counter & Market Sweep Engine 1.1.

Generates model-driven same-partner counters and league-wide alternate-buyer
packages around the focal outgoing assets from an incoming trade scenario.
Canonical Sleeper, GM, and Simulator state is read-only.

1.1 refinements:
- test strict subsets of focal outgoing assets (e.g. Dak alone instead of Dak+CeeDee);
- penalize protected buyer assets and LOW-plausibility packages more strongly;
- guarantee market representation in the simulation shortlist;
- diversify alternate-buyer candidates across distinct franchises;
- preserve best same-partner and alternate-buyer options in the report even
  when they are not top-N overall by post-simulation score.
"""

from __future__ import annotations

import argparse
import functools
import importlib.util
import itertools
import json
import math
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

DATA = Path("data")
MODEL_VERSION = "FSFFL-Counter-Market-Sweep-1.1"
DEFAULT_QUICK_SIMS = 250
DEFAULT_CONFIRM_SIMS = 0
DEFAULT_SHORTLIST = 5
DEFAULT_FINALISTS = 3
DEFAULT_REPORT_CANDIDATES = 5


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


@functools.lru_cache(maxsize=None)
def franchise_index() -> Dict[str, Dict[str, Any]]:
    idx = load_json(DATA / "gm" / "franchise_index.json", {}) or {}
    return {str(x.get("user_id")): x for x in idx.get("teams") or []}


@functools.lru_cache(maxsize=None)
def team_doc(uid: str, key: str) -> Dict[str, Any]:
    row = franchise_index().get(str(uid)) or {}
    path = ((row.get("paths") or {}).get(key))
    return load_json(Path(path), {}) if path else {}


@functools.lru_cache(maxsize=None)
def command_center(uid: str) -> Dict[str, Any]:
    return team_doc(uid, "command_center")


@functools.lru_cache(maxsize=None)
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
        raise ValueError("Market Sweep 1.1 requires a bilateral incoming trade with focal outgoing assets")
    return sent, received, next(iter(partners))


def asset_value(asset: Dict[str, Any], owner_uid: str | None = None) -> Dict[str, float]:
    gm = strategic_assets(owner_uid) if owner_uid else {}
    g = gm.get(str(asset.get("asset_id"))) or {}
    market = float(g.get("market_dynasty") or asset.get("market_dynasty") or 0.0)
    redraft = float(g.get("market_redraft") or asset.get("market_redraft") or 0.0)
    base = float(g.get("base_franchise_value") or asset.get("fsffl_value") or market)
    bg = float(g.get("break_glass_value") or base)
    return {"market": market, "redraft": redraft, "base": base, "break_glass": bg}


@functools.lru_cache(maxsize=None)
def need_map(uid: str) -> Dict[str, float]:
    cc = command_center(uid)
    return {str(x.get("position")): float(x.get("need_score") or 0.0) for x in cc.get("biggest_position_needs") or []}


@functools.lru_cache(maxsize=None)
def team_state(uid: str) -> str:
    return str(command_center(uid).get("team_state") or (franchise_index().get(uid) or {}).get("team_state") or "unknown")


def package_key(assets: Iterable[Dict[str, Any]]) -> Tuple[str, ...]:
    return tuple(sorted(str(a.get("asset_id")) for a in assets))


def outgoing_variants(outgoing: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    """Full package plus strict non-empty subsets, largest first.

    This lets the engine discover a cleaner sale of a liquid asset without
    silently requiring a core player to move with it.
    """
    if len(outgoing) <= 1:
        return [outgoing]
    variants = []
    for n in range(len(outgoing), 0, -1):
        for combo in itertools.combinations(outgoing, n):
            variants.append(list(combo))
    return variants


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
    """Cheap discovery features with no hand-set cross-signal score.

    The prescreen exists to decide which packages receive expensive simulation,
    not to estimate trade quality. Market-distance is the primary search key;
    roster need, protected/core status and redraft deltas are retained as
    diagnostics for downstream interpretation rather than assigned arbitrary
    percentage weights.
    """
    focal_needs = need_map(focus_uid)
    buyer_needs = need_map(buyer_uid)
    buyer_gm = strategic_assets(buyer_uid)
    outgoing_market = sum(asset_value(a, focus_uid)["market"] for a in outgoing)
    outgoing_redraft = sum(asset_value(a, focus_uid)["redraft"] for a in outgoing)
    return_market = sum(asset_value(a, buyer_uid)["market"] for a in incoming)
    return_redraft = sum(asset_value(a, buyer_uid)["redraft"] for a in incoming)
    need_gain = sum(
        focal_needs.get(str(a.get("position")), 0.0) * asset_value(a, buyer_uid)["redraft"]
        for a in incoming if a.get("asset_type") == "player"
    )
    buyer_need_solved = sum(
        buyer_needs.get(str(a.get("position")), 0.0) * asset_value(a, focus_uid)["redraft"]
        for a in outgoing if a.get("asset_type") == "player"
    )

    protected_assets = []
    for a in incoming:
        g = buyer_gm.get(str(a.get("asset_id"))) or {}
        if str(g.get("core_status") or "") in {"franchise_cornerstone", "core_high_hold", "core_pick"}:
            protected_assets.append(a.get("name"))

    out_base = max(outgoing_market, 1.0)
    in_base = max(return_market, 1.0)
    value_ratio = in_base / out_base
    market_balance_distance = abs(math.log(value_ratio))
    # A monotone, coefficient-free display transform. This is not acceptance
    # probability and does not determine candidate eligibility.
    plausibility_score = 1.0 / (1.0 + market_balance_distance)
    if plausibility_score >= 0.80:
        plaus = "HIGH"
    elif plausibility_score >= 0.65:
        plaus = "MEDIUM"
    elif plausibility_score >= 0.50:
        plaus = "LOW"
    else:
        plaus = "THEORETICAL_ONLY"

    future_surplus = return_market - outgoing_market
    redraft_replacement = return_redraft - outgoing_redraft

    return {
        "buyer_user_id": buyer_uid,
        "buyer_team": command_center(buyer_uid).get("focal_team") or (franchise_index().get(buyer_uid) or {}).get("team_name"),
        "buyer_state": team_state(buyer_uid),
        "outgoing_assets": [a.get("asset_id") for a in outgoing],
        "outgoing_asset_names": [a.get("name") for a in outgoing],
        "return_assets": [a.get("asset_id") for a in incoming],
        "return_asset_names": [a.get("name") for a in incoming],
        "market_dynasty_delta_pre_screen": round(future_surplus, 2),
        "market_redraft_delta_pre_screen": round(redraft_replacement, 2),
        "market_value_ratio_pre_screen": round(value_ratio, 6),
        "market_balance_distance_pre_screen": round(market_balance_distance, 6),
        "focal_need_gain_score": round(need_gain, 2),
        "buyer_need_solved_score": round(buyer_need_solved, 2),
        "protected_buyer_assets": protected_assets,
        "plausibility_score": round(plausibility_score, 4),
        "plausibility": plaus,
        "pre_screen_score": round(plausibility_score, 6),
        "prescreen_score_role": "MARKET_DISTANCE_SEARCH_PRIORITY_NOT_TRADE_QUALITY",
        "protected_status_affects_prescreen_score": False,
        "need_affects_prescreen_score": False,
        "redraft_affects_prescreen_score": False,
    }


def prescreen_sort_key(row: Dict[str, Any]):
    """Coefficient-free search ordering.

    Prefer market-near packages first; at identical distance prefer the package
    with more focal market surplus, then redraft surplus. All final judgments
    come from canonical simulation/utility.
    """
    return (
        -float(row.get("market_balance_distance_pre_screen") or 0.0),
        float(row.get("market_dynasty_delta_pre_screen") or 0.0),
        float(row.get("market_redraft_delta_pre_screen") or 0.0),
    )


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
    baseline_teams = list((baseline or {}).get("teams") or [])
    def mean_metric(key):
        vals = [float(x.get(key) or 0.0) for x in baseline_teams]
        return (sum(vals) / len(vals)) if vals else 0.0
    league_reference = {
        "team_count": len(baseline_teams),
        "expected_wins_mean": mean_metric("expected_wins"),
        "expected_points_for_mean": mean_metric("expected_points_for"),
        "playoff_probability_mean": mean_metric("playoff_probability"),
        "championship_probability_mean": mean_metric("championship_probability"),
        "source": "canonical_baseline_simulator_league_mean",
    }
    title_delta = dl.delta(b.get("championship_probability"), h.get("championship_probability"))
    buyer_title_delta = dl.delta(ob.get("championship_probability"), oh.get("championship_probability")) if ob and oh else 0.0
    buyer_delta = {
        "expected_wins": dl.delta(ob.get("expected_wins"), oh.get("expected_wins")) if ob and oh else 0.0,
        "expected_points_for": dl.delta(ob.get("expected_points_for"), oh.get("expected_points_for")) if ob and oh else 0.0,
        "playoff_probability": dl.delta(ob.get("playoff_probability"), oh.get("playoff_probability")) if ob and oh else 0.0,
        "bye_probability": dl.delta(ob.get("bye_probability"), oh.get("bye_probability")) if ob and oh else 0.0,
        "championship_probability": buyer_title_delta,
    }
    buyer_strategic = dl.strategic_summary(buyer_uid, actions) if ob and oh else {}
    return {
        "actions": actions,
        "teams_reoptimized": reoptimized,
        "league_reference": league_reference,
        "focus_before": b,
        "focus_after": h,
        "focus_delta": {
            "expected_wins": dl.delta(b.get("expected_wins"), h.get("expected_wins")),
            "expected_points_for": dl.delta(b.get("expected_points_for"), h.get("expected_points_for")),
            "playoff_probability": dl.delta(b.get("playoff_probability"), h.get("playoff_probability")),
            "bye_probability": dl.delta(b.get("bye_probability"), h.get("bye_probability")),
            "championship_probability": title_delta,
        },
        "buyer_before": ob,
        "buyer_after": oh,
        "buyer_delta": buyer_delta,
        "buyer_strategic": buyer_strategic,
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
    plausibility = float(row.get("plausibility_score") or 0.0)
    score = dynasty + 0.35 * break_glass + 25000.0 * title + 5000.0 * playoff - 12000.0 * externality
    score += 1200.0 * plausibility
    if row.get("plausibility") == "LOW":
        score -= 3000.0
    cap = contender_title_cap(state)
    if cap is not None and title < -cap:
        score -= 12000.0 + 50000.0 * abs(title + cap)
        row["championship_equity_constraint"] = "FAIL"
    else:
        row["championship_equity_constraint"] = "PASS"
    return round(score, 2)


def diversified_select(raw_candidates, shortlist: int):
    """Guarantee current-partner, alternate-market, and subset representation."""
    if shortlist <= 0:
        return []
    current = [x for x in raw_candidates if x[0]["candidate_type"] == "SAME_PARTNER_COUNTER"]
    alternates = [x for x in raw_candidates if x[0]["candidate_type"] == "ALTERNATE_BUYER"]

    # Prefer MEDIUM/HIGH candidates; LOW becomes fallback only.
    preferred_current = [x for x in current if x[0]["plausibility"] in {"HIGH", "MEDIUM"}] or current
    preferred_alts = [x for x in alternates if x[0]["plausibility"] in {"HIGH", "MEDIUM"}] or alternates

    selected, seen = [], set()

    def add(item):
        if not item or len(selected) >= shortlist:
            return
        row, pkg, outgoing = item
        key = (row["buyer_user_id"], package_key(outgoing), package_key(pkg))
        if key not in seen:
            seen.add(key)
            selected.append(item)

    # For an incoming offer, reserve simulation capacity for the
    # smallest target-preserving concessions first. These are local negotiation
    # tests around observed willingness, not assumed acceptances.
    offeror_concessions = [
        x for x in current
        if x[0].get("counter_strategy") == "OFFEROR_ANCHORED_TARGET_PRESERVING_CONCESSION"
    ]
    offeror_concessions.sort(
        key=lambda x: (
            float(x[0].get("concession_value_vs_current_offer") or 0.0),
            float(x[0].get("market_balance_distance_pre_screen") or 0.0),
            package_key(x[2]),
        )
    )
    for item in offeror_concessions[: min(10, shortlist)]:
        add(item)

    # Preserve at least one best same-partner counter from the broader search.
    if preferred_current:
        add(preferred_current[0])

    # Slot 2: best alternate buyer, guaranteed when one exists.
    if preferred_alts and len(selected) < shortlist:
        add(preferred_alts[0])

    # Slot 3: best strict-subset trade (e.g. Dak alone), regardless of buyer.
    if len(selected) < shortlist:
        subset_rows = [x for x in raw_candidates if x[0].get("outgoing_variant") == "SUBSET" and x[0]["plausibility"] in {"HIGH", "MEDIUM"}]
        if not subset_rows:
            subset_rows = [x for x in raw_candidates if x[0].get("outgoing_variant") == "SUBSET"]
        if subset_rows:
            add(subset_rows[0])

    # Add alternate buyers from distinct teams before allowing repeats.
    used_alt_buyers = {r[0]["buyer_user_id"] for r in selected if r[0]["candidate_type"] == "ALTERNATE_BUYER"}
    for item in preferred_alts:
        if len(selected) >= shortlist:
            break
        if item[0]["buyer_user_id"] in used_alt_buyers:
            continue
        add(item)
        used_alt_buyers.add(item[0]["buyer_user_id"])

    # Fill remaining slots by global score.
    for item in raw_candidates:
        if len(selected) >= shortlist:
            break
        add(item)
    return selected


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
    offer_initiator_uid = str(scenario.get("offer_initiator_user_id") or "")
    offer_direction = (
        "INCOMING_OFFER" if offer_initiator_uid == current_partner
        else "FOCAL_INITIATED" if offer_initiator_uid == focus_uid
        else "UNKNOWN"
    )
    player_catalog, pick_catalog = asset_catalog()
    catalog = {**player_catalog, **pick_catalog}
    full_outgoing = [catalog[x] for x in sent_ids if x in catalog]
    current_incoming = [catalog[x] for x in received_ids if x in catalog]
    if len(full_outgoing) != len(sent_ids):
        missing = [x for x in sent_ids if x not in catalog]
        raise ValueError(f"Outgoing assets missing from FSFFL asset catalog: {missing}")
    if len(current_incoming) != len(received_ids):
        missing = [x for x in received_ids if x not in catalog]
        raise ValueError(f"Incoming assets missing from FSFFL asset catalog: {missing}")

    owner_assets = build_owner_assets(rosters)
    idx = franchise_index()
    raw_candidates = []
    variants = outgoing_variants(full_outgoing)
    full_key = package_key(full_outgoing)

    # When the counterparty initiated the offer, their willingness at the
    # current terms is observed. Explicitly test nearby target-preserving
    # concessions instead of searching only for entirely different returns.
    # This does not assume the counter will be accepted; it creates a local
    # negotiation frontier around an observed willingness point.
    if offer_direction == "INCOMING_OFFER" and current_incoming:
        current_outgoing_market = sum(
            asset_value(a, focus_uid)["market"] for a in full_outgoing
        )
        fixed_players = [a for a in full_outgoing if a.get("asset_type") == "player"]
        current_pick_ids = {
            str(a.get("asset_id")) for a in full_outgoing
            if a.get("asset_type") == "pick"
        }
        focal_picks = [
            a for a in (owner_assets.get(focus_uid) or [])
            if a.get("asset_type") == "pick"
            and str(a.get("asset_id")) not in current_pick_ids
        ]
        current_pick_count = len(current_pick_ids)
        # A replacement package may use one additional pick to trade a more
        # valuable near-term pick for multiple smaller assets. The cap never
        # exceeds the canonical multi-asset search ceiling.
        replacement_pick_cap = min(4, current_pick_count + 1, len(focal_picks))
        replacement_combos = [()]
        for n in range(1, replacement_pick_cap + 1):
            replacement_combos.extend(itertools.combinations(focal_picks, n))
        for combo in replacement_combos:
            outgoing = fixed_players + list(combo)
            if not outgoing or package_key(outgoing) == full_key:
                continue
            outgoing_market = sum(
                asset_value(a, focus_uid)["market"] for a in outgoing
            )
            if outgoing_market >= current_outgoing_market:
                continue
            row = score_candidate(
                focus_uid, current_partner, outgoing, current_incoming
            )
            row["outgoing_variant"] = "OFFEROR_CONCESSION"
            row["candidate_type"] = "SAME_PARTNER_COUNTER"
            row["counter_strategy"] = "OFFEROR_ANCHORED_TARGET_PRESERVING_CONCESSION"
            row["offer_initiator_user_id"] = offer_initiator_uid
            row["current_offer_willingness_observed"] = True
            row["concession_value_vs_current_offer"] = round(
                current_outgoing_market - outgoing_market, 2
            )
            raw_candidates.append((row, current_incoming, outgoing))

    for outgoing in variants:
        variant = "FULL" if package_key(outgoing) == full_key else "SUBSET"
        for buyer_uid in idx:
            if buyer_uid == focus_uid:
                continue
            assets = owner_assets.get(buyer_uid) or []
            # Enumerate every owned tradeable asset. The former top-10-player /
            # top-8-pick gate had only 35% recall for the expanded Top 40 in the
            # governed regression case. Read-only team context is memoized above,
            # so full-pool enumeration is practical without changing scoring.
            player_pool = sorted(
                [a for a in assets if a.get("asset_type") == "player"],
                key=lambda a: a.get("market_dynasty", 0),
                reverse=True,
            )
            pick_pool = sorted(
                [a for a in assets if a.get("asset_type") == "pick"],
                key=lambda a: a.get("market_dynasty", 0),
                reverse=True,
            )
            for pkg in candidate_packages(player_pool + pick_pool):
                row = score_candidate(focus_uid, buyer_uid, outgoing, pkg)
                # Plausibility is a search/ranking feature, not an eligibility
                # gate. Low-scoring packages remain available to the diversified
                # shortlist so an arbitrary prescreen band cannot create a false
                # negative before canonical simulation.
                row["outgoing_variant"] = variant
                row["candidate_type"] = "SAME_PARTNER_COUNTER" if buyer_uid == current_partner else "ALTERNATE_BUYER"
                if (
                    offer_direction == "INCOMING_OFFER"
                    and buyer_uid == current_partner
                    and package_key(pkg) == package_key(current_incoming)
                    and package_key(outgoing) != full_key
                ):
                    row["counter_strategy"] = "OFFEROR_ANCHORED_TARGET_PRESERVING_CONCESSION"
                    row["offer_initiator_user_id"] = offer_initiator_uid
                    row["current_offer_willingness_observed"] = True
                    current_outgoing_market = sum(
                        asset_value(a, focus_uid)["market"] for a in full_outgoing
                    )
                    outgoing_market = sum(
                        asset_value(a, focus_uid)["market"] for a in outgoing
                    )
                    row["concession_value_vs_current_offer"] = round(
                        max(0.0, current_outgoing_market - outgoing_market), 2
                    )
                raw_candidates.append((row, pkg, outgoing))

    raw_candidates.sort(key=lambda x: prescreen_sort_key(x[0]), reverse=True)
    offeror_concession_candidates_enumerated = sum(
        1 for x in raw_candidates
        if x[0].get("counter_strategy") == "OFFEROR_ANCHORED_TARGET_PRESERVING_CONCESSION"
    )
    selected = diversified_select(raw_candidates, args.shortlist)

    state = team_state(focus_uid)
    simulated = []
    for row, pkg, outgoing in selected:
        row = dict(row)
        row["simulation"] = simulate_candidate(
            dl, model_inputs, baseline_lineups, baseline, focus_uid, row["buyer_user_id"], outgoing, pkg, args.quick_sims, args.seed
        )
        row["post_sim_score"] = post_sim_score(row, state)
        simulated.append(row)
    simulated.sort(key=lambda x: x["post_sim_score"], reverse=True)
    offeror_concession_candidates_simulated = sum(
        1 for x in simulated
        if x.get("counter_strategy") == "OFFEROR_ANCHORED_TARGET_PRESERVING_CONCESSION"
    )

    # Overall finalists, while preserving category leaders for negotiation output.
    finalists = simulated[: args.finalists]
    best_same = next((x for x in simulated if x["candidate_type"] == "SAME_PARTNER_COUNTER"), None)
    best_alt = next((x for x in simulated if x["candidate_type"] == "ALTERNATE_BUYER"), None)
    best_subset = next((x for x in simulated if x.get("outgoing_variant") == "SUBSET"), None)

    if args.confirm_sims >= 100 and finalists:
        top = finalists[0]
        pkg = [catalog[x] for x in top["return_assets"] if x in catalog]
        outgoing = [catalog[x] for x in top["outgoing_assets"] if x in catalog]
        confirm_baseline = dl.simulate_from_lineups(simmod, league, rosters, users, raw_schedule, baseline_lineups, args.confirm_sims, args.seed)
        top["confirmation_simulation"] = simulate_candidate(
            dl, model_inputs, baseline_lineups, confirm_baseline, focus_uid, top["buyer_user_id"], outgoing, pkg, args.confirm_sims, args.seed
        )

    viable = [x for x in simulated if x.get("championship_equity_constraint") == "PASS" and x.get("plausibility") in {"HIGH", "MEDIUM"}]
    best_viable_same = next((x for x in viable if x["candidate_type"] == "SAME_PARTNER_COUNTER"), None)
    best_viable_alt = next((x for x in viable if x["candidate_type"] == "ALTERNATE_BUYER"), None)

    if not viable:
        action = "DECLINE"
    elif best_viable_alt and (not best_viable_same or best_viable_alt["post_sim_score"] > best_viable_same["post_sim_score"] + 750):
        action = "SHOP_BEFORE_ACCEPTING"
    elif best_viable_same:
        action = "COUNTER_CURRENT_OFFEROR"
    else:
        action = "SHOP_BEFORE_ACCEPTING"

    prescreen_top = [dict(x[0]) for x in raw_candidates[:DEFAULT_REPORT_CANDIDATES]]
    report = {
        "model_version": MODEL_VERSION,
        "scenario_id": scenario.get("scenario_id") or scenario_path.stem,
        "focus_user_id": focus_uid,
        "focus_team_state": state,
        "current_offer_partner_user_id": current_partner,
        "offer_initiator_user_id": offer_initiator_uid or None,
        "offer_direction": offer_direction,
        "outgoing_assets": sent_ids,
        "incoming_offer_assets": received_ids,
        "simulation": {
            "quick_sims": args.quick_sims,
            "confirm_sims": args.confirm_sims,
            "seed": args.seed,
            "simulator_model_version": simmod.MODEL_VERSION,
            "canonical_state_mutated": False,
            "execution_path": "gm_prescreen_then_diversified_decision_lab_shortlist",
            "candidate_asset_pool": "all_owned_tradeable_assets",
            "candidate_asset_pool_legacy_caps_removed": True,
            "plausibility_band_is_candidate_eligibility_gate": False,
        },
        "candidate_counts": {
            "enumerated_plausible": len(raw_candidates),
            "outgoing_variants": len(variants),
            "simulated_shortlist": len(simulated),
            "finalists": len(finalists),
            "offeror_concession_candidates_enumerated": offeror_concession_candidates_enumerated,
            "offeror_concession_candidates_simulated": offeror_concession_candidates_simulated,
        },
        "pre_screen_top_candidates": prescreen_top,
        "same_partner_counteroffers": [x for x in simulated if x["candidate_type"] == "SAME_PARTNER_COUNTER"][:DEFAULT_REPORT_CANDIDATES],
        "alternate_buyer_candidates": [x for x in simulated if x["candidate_type"] == "ALTERNATE_BUYER"][:DEFAULT_REPORT_CANDIDATES],
        "best_subset_trade": best_subset,
        "best_same_partner": best_same,
        "best_alternate_buyer": best_alt,
        "ranked_finalists": finalists,
        "recommended_next_action": action,
        "policy": {
            "contender_title_loss_cap": contender_title_cap(state),
            "acceptance_requires_market_sweep": True,
            "conditional_state_logic": True,
            "low_plausibility_can_drive_action": False,
            "alternate_buyer_shortlist_slot_reserved": True,
            "strict_outgoing_subsets_tested": True,
            "offer_origin_aware_counter_search": True,
            "incoming_offer_target_preserving_concessions_tested": offer_direction == "INCOMING_OFFER",
            "observed_offer_willingness_is_local_counter_anchor_not_acceptance_probability": True,
        },
    }
    output = Path(args.output) if args.output else DATA / "decision_lab" / "outputs" / f"{report['scenario_id']}_market_sweep.json"
    write_json(output, report)
    print(json.dumps(report, indent=2))
    print(f"\nWrote Counter & Market Sweep report: {output}")


if __name__ == "__main__":
    main()
