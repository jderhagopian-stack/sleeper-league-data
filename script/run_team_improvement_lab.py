#!/usr/bin/env python3
"""FSFFL GM 3.0 Team Improvement Lab 1.0.

Searches for actionable ways to improve one franchise across multiple channels:
  * realistic trade targets from GM 3.0 trade-opportunity intelligence;
  * waiver/free-agent add/drop moves from the live Sleeper player pool;
  * the explicit HOLD / do-nothing benchmark.

Every candidate is resolved to a legal active roster before lineup optimization
and simulation. Forced cuts are included in strategic value. Top candidates are
reconfirmed at deeper Monte Carlo depth before final ranking.

Canonical Sleeper, GM, behavioral, and simulator state remains read-only.
"""
from __future__ import annotations

import argparse
import copy
import importlib.util
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List

DATA = Path("data")
SCRIPT = Path(__file__).resolve().parent
MODEL_VERSION = "FSFFL-GM-Team-Improvement-Lab-1.0"
DEFAULT_QUICK_SIMS = 200
DEFAULT_CONFIRM_SIMS = 1000
DEFAULT_TRADE_SCREEN = 30
DEFAULT_WAIVER_SCREEN = 30
DEFAULT_CONFIRM_TOP = 5


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to import {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_json(path: Path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def sf(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def team_index(sim):
    return {str(x.get("user_id")): x for x in sim.get("teams") or []}


def delta(before, after):
    return round(sf(after) - sf(before), 5)


def franchise_row(focus_uid: str):
    idx = load_json(DATA / "gm" / "franchise_index.json", {}) or {}
    for row in idx.get("teams") or []:
        if str(row.get("user_id")) == str(focus_uid):
            return row
    raise ValueError(f"Unknown franchise user id {focus_uid}")


def team_doc(focus_uid: str, key: str):
    row = franchise_row(focus_uid)
    path = ((row.get("paths") or {}).get(key))
    return load_json(Path(path), {}) if path else {}


def state_weights(focus_uid: str):
    cc = team_doc(focus_uid, "command_center")
    state = str(cc.get("team_state") or "unknown")
    # Ranking is intentionally dominated by football outcomes for contenders,
    # while retaining meaningful franchise/future value so short-lived upgrades
    # do not automatically win the search.
    if state == "elite_contender":
        return state, dict(wins=1450, playoff=6500, bye=5000, title=36000, base=.20, dynasty=.08, break_glass=.025)
    if state == "contender":
        return state, dict(wins=1300, playoff=6000, bye=4500, title=33000, base=.23, dynasty=.10, break_glass=.03)
    if state == "retool":
        return state, dict(wins=800, playoff=3500, bye=2200, title=22000, base=.32, dynasty=.18, break_glass=.05)
    if state == "rebuild":
        return state, dict(wins=250, playoff=800, bye=300, title=7000, base=.46, dynasty=.30, break_glass=.08)
    return state, dict(wins=900, playoff=4000, bye=2500, title=24000, base=.30, dynasty=.16, break_glass=.05)


def unified_score(focus_uid: str, sim: Dict[str, Any]) -> float:
    _, w = state_weights(focus_uid)
    d = sim.get("focus_delta") or {}
    st = sim.get("strategic") or {}
    return round(
        sf(d.get("expected_wins")) * w["wins"]
        + sf(d.get("playoff_probability")) * w["playoff"]
        + sf(d.get("bye_probability")) * w["bye"]
        + sf(d.get("championship_probability")) * w["title"]
        + sf(st.get("base_franchise_value_delta")) * w["base"]
        + sf(st.get("market_dynasty_delta")) * w["dynasty"]
        + sf(st.get("break_glass_delta")) * w["break_glass"],
        2,
    )


def contender_guardrail(focus_uid: str, sim: Dict[str, Any]) -> Dict[str, Any]:
    state, _ = state_weights(focus_uid)
    champ = sf((sim.get("focus_delta") or {}).get("championship_probability"))
    wins = sf((sim.get("focus_delta") or {}).get("expected_wins"))
    # Team-improvement recommendations should not call a move an improvement if
    # it materially damages the present title window. A future-value move may
    # still be shown separately as a strategic alternative.
    cap = -0.02 if state == "elite_contender" else -0.025 if state == "contender" else -0.06
    passed = champ >= cap or wins >= 0.35
    return {"team_state": state, "championship_loss_floor": cap, "passed": passed}


def fast_reoptimize(v13, dl, simmod, baseline_lineups, rosters, touched, league, users, players, projections):
    return v13.fast_reoptimize_touched_lineups(
        dl, simmod, baseline_lineups, rosters, touched, league, users, players, projections
    )


def simulate_actions(dl, v13, rosteraware, model_inputs, baseline_lineups, baseline,
                     focus_uid: str, actions: List[Dict[str, Any]], sims: int, seed: int):
    simmod, league, canonical_rosters, users, players, season, projections, raw_schedule = model_inputs
    hypothetical, _ = dl.apply_actions(canonical_rosters, actions)
    touched = dl.touched_users(focus_uid, actions)
    legal, resolutions, cut_actions = rosteraware.legalize_trade_rosters(
        dl, canonical_rosters, hypothetical, touched, league, players
    )
    effective_actions = list(actions) + list(cut_actions)
    lineups, reoptimized = fast_reoptimize(
        v13, dl, simmod, baseline_lineups, legal, touched, league, users, players, projections
    )
    hyp = dl.simulate_from_lineups(simmod, league, legal, users, raw_schedule, lineups, sims, seed)
    bidx, hidx = team_index(baseline), team_index(hyp)
    b, h = bidx[str(focus_uid)], hidx[str(focus_uid)]
    st = dl.strategic_summary(str(focus_uid), effective_actions)
    return {
        "focus_before": b,
        "focus_after": h,
        "focus_delta": {
            "expected_wins": delta(b.get("expected_wins"), h.get("expected_wins")),
            "expected_points_for": delta(b.get("expected_points_for"), h.get("expected_points_for")),
            "playoff_probability": delta(b.get("playoff_probability"), h.get("playoff_probability")),
            "bye_probability": delta(b.get("bye_probability"), h.get("bye_probability")),
            "championship_probability": delta(b.get("championship_probability"), h.get("championship_probability")),
        },
        "strategic": st,
        "roster_resolution": resolutions,
        "effective_actions": effective_actions,
        "teams_reoptimized": reoptimized,
        "simulation_count": sims,
    }


def owner_map(rosters):
    out = {}
    for r in rosters:
        uid = str(r.get("owner_id"))
        for pid in r.get("players") or []:
            out[str(pid)] = uid
    return out


def asset_catalog():
    doc = load_json(DATA / "fsffl_asset_values.json", {}) or {}
    players, picks = {}, {}
    for p in doc.get("players") or []:
        pid = str(p.get("player_id"))
        players[f"player:{pid}"] = {
            "asset_id": f"player:{pid}", "asset_type": "player", "player_id": pid,
            "name": p.get("name") or f"player:{pid}", "position": p.get("position"),
            "market_dynasty": sf(p.get("market_dynasty")), "market_redraft": sf(p.get("market_redraft")),
            "fsffl_value": sf(p.get("fsffl_value"), sf(p.get("market_dynasty"))),
            "owner_user_id": str(p.get("current_owner_user_id")) if p.get("current_owner_user_id") is not None else None,
        }
    for p in doc.get("picks") or []:
        aid = str(p.get("asset_id") or "")
        if aid:
            picks[aid] = {"asset_id": aid, "asset_type": "pick", "name": p.get("name") or aid,
                          "market_dynasty": sf(p.get("market_dynasty"), sf(p.get("fsffl_value"))),
                          "market_redraft": 0.0, "fsffl_value": sf(p.get("fsffl_value"), sf(p.get("market_dynasty"))),
                          "owner_user_id": str(p.get("current_owner_user_id")) if p.get("current_owner_user_id") is not None else None}
    return players, picks


def trade_candidates(focus_uid: str, catalog: Dict[str, Dict[str, Any]], limit: int):
    doc = team_doc(focus_uid, "trade_opportunities")
    rows = []
    for opp in doc.get("opportunities") or []:
        target_id = str(opp.get("target_asset_id") or "")
        seller = str(opp.get("seller_user_id") or "")
        target = catalog.get(target_id)
        if not target or not seller or seller == str(focus_uid):
            continue
        for pkg in (opp.get("best_candidate_packages") or [])[:3]:
            aids = [str(x) for x in (pkg.get("focal_outgoing_asset_ids") or [])]
            outgoing = [catalog.get(x) for x in aids]
            if not aids or any(x is None for x in outgoing):
                continue
            acceptance = sf(pkg.get("acceptance_fit_score"))
            seller_utility = sf(pkg.get("seller_strategic_utility"))
            # Keep plausible negotiations. Lower-fit options can still surface if
            # their seller-side utility is non-negative.
            if acceptance < .38 and seller_utility < 0:
                continue
            rows.append({
                "channel": "TRADE",
                "seller_user_id": seller,
                "seller_team": opp.get("seller_team"),
                "target": target,
                "outgoing": outgoing,
                "pre_screen_score": sf(pkg.get("gm30_decision_score"), sf(pkg.get("decision_score"))),
                "acceptance_fit_score": acceptance,
                "seller_strategic_utility_precomputed": seller_utility,
                "source_recommendation_band": pkg.get("recommendation_band"),
            })
    rows.sort(key=lambda x: (x["pre_screen_score"], x["acceptance_fit_score"]), reverse=True)
    # Deduplicate exact transaction structures.
    seen, out = set(), []
    for row in rows:
        key = (row["seller_user_id"], row["target"]["asset_id"], tuple(sorted(x["asset_id"] for x in row["outgoing"])))
        if key in seen:
            continue
        seen.add(key); out.append(row)
        if len(out) >= limit:
            break
    return out


def trade_actions(focus_uid: str, row: Dict[str, Any]):
    seller = row["seller_user_id"]
    outgoing = row["outgoing"]
    target = row["target"]
    return [
        {"type": "trade", "from_user_id": str(focus_uid), "to_user_id": seller,
         "players": [x["player_id"] for x in outgoing if x.get("asset_type") == "player"],
         "picks": [x["asset_id"] for x in outgoing if x.get("asset_type") == "pick"]},
        {"type": "trade", "from_user_id": seller, "to_user_id": str(focus_uid),
         "players": [target["player_id"]], "picks": []},
    ]


def waiver_candidates(focus_uid: str, players_catalog, model_inputs, limit: int):
    _, _, rosters, _, players, _, projections, _ = model_inputs
    owned = owner_map(rosters)
    projection_players = (projections or {}).get("players") or {}
    rows = []
    for aid, a in players_catalog.items():
        pid = str(a.get("player_id"))
        if pid in owned:
            continue
        if a.get("position") not in {"QB", "RB", "WR", "TE"}:
            continue
        proj = projection_players.get(pid) or {}
        weeks = proj.get("weeks") or {}
        if not weeks:
            continue
        future_means = [sf(v.get("mean", v.get("median"))) * sf(v.get("active_probability"), 1.0) for v in weeks.values()]
        projected = sum(future_means) / max(1, len(future_means))
        market = sf(a.get("market_dynasty")); redraft = sf(a.get("market_redraft"))
        screen = projected * 150 + market * .45 + redraft * .20
        rows.append({"channel": "WAIVER", "target": a, "projected_weekly_mean": round(projected, 3), "pre_screen_score": round(screen, 2)})
    rows.sort(key=lambda x: x["pre_screen_score"], reverse=True)
    return rows[:limit]


def waiver_actions(focus_uid: str, row: Dict[str, Any]):
    # Add first; roster-aware legalization identifies the least-damaging drop if
    # the active roster is full. This makes the drop endogenous to the optimizer.
    return [{"type": "add", "user_id": str(focus_uid), "players": [row["target"]["player_id"]]}]


def describe(row):
    if row["channel"] == "TRADE":
        sent = " + ".join(x["name"] for x in row["outgoing"])
        return f"Trade {sent} for {row['target']['name']}"
    if row["channel"] == "WAIVER":
        sim = row.get("simulation") or {}; res = (sim.get("roster_resolution") or {}).get(str(row.get("focus_user_id"))) or {}
        cuts = [x.get("name") for x in res.get("selected_cuts") or []]
        return f"Add {row['target']['name']}" + (f"; drop {', '.join(cuts)}" if cuts else "")
    return "Hold current roster"


def evaluate_row(row, focus_uid, dl, v13, rosteraware, model_inputs, baseline_lineups, baseline, sims, seed):
    actions = trade_actions(focus_uid, row) if row["channel"] == "TRADE" else waiver_actions(focus_uid, row)
    sim = simulate_actions(dl, v13, rosteraware, model_inputs, baseline_lineups, baseline, focus_uid, actions, sims, seed)
    row = copy.deepcopy(row)
    row["focus_user_id"] = str(focus_uid)
    row["simulation"] = sim
    row["team_improvement_score"] = unified_score(focus_uid, sim)
    row["guardrail"] = contender_guardrail(focus_uid, sim)
    row["actionable"] = bool(row["guardrail"]["passed"] and row["team_improvement_score"] > 0)
    if row["channel"] == "TRADE":
        # Acceptance is explicitly a fit heuristic, never a calibrated yes-probability.
        fit = sf(row.get("acceptance_fit_score"))
        row["acceptance_fit"] = "HIGH" if fit >= .68 else "MEDIUM" if fit >= .48 else "LOW" if fit >= .28 else "VERY_LOW"
    row["description"] = describe(row)
    return row


def rerun_candidate(row, focus_uid, dl, v13, rosteraware, model_inputs, baseline_lineups, baseline, sims, seed):
    fresh = {k: v for k, v in row.items() if k not in {"simulation", "team_improvement_score", "guardrail", "actionable", "description"}}
    return evaluate_row(fresh, focus_uid, dl, v13, rosteraware, model_inputs, baseline_lineups, baseline, sims, seed)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--focus-user-id", required=True)
    ap.add_argument("--quick-sims", type=int, default=DEFAULT_QUICK_SIMS)
    ap.add_argument("--confirm-sims", type=int, default=DEFAULT_CONFIRM_SIMS)
    ap.add_argument("--trade-screen", type=int, default=DEFAULT_TRADE_SCREEN)
    ap.add_argument("--waiver-screen", type=int, default=DEFAULT_WAIVER_SCREEN)
    ap.add_argument("--confirm-top", type=int, default=DEFAULT_CONFIRM_TOP)
    ap.add_argument("--seed", type=int, default=20260821)
    ap.add_argument("--output", required=True)
    a = ap.parse_args()
    focus_uid = str(a.focus_user_id)

    dl = load_module(SCRIPT / "run_roster_decision_lab.py", "team_improvement_dl")
    v13 = load_module(SCRIPT / "run_trade_market_sweep_v13.py", "team_improvement_v13")
    rosteraware = load_module(SCRIPT / "roster_aware_trade.py", "team_improvement_roster")
    model_inputs = dl.load_model_inputs()
    simmod, league, rosters, users, players, season, projections, raw_schedule = model_inputs
    baseline_lineups = dl.load_cached_lineups(season)
    baseline_quick = dl.simulate_from_lineups(simmod, league, rosters, users, raw_schedule, baseline_lineups, a.quick_sims, a.seed)

    players_catalog, picks_catalog = asset_catalog(); catalog = {**players_catalog, **picks_catalog}
    raw = trade_candidates(focus_uid, catalog, max(1, a.trade_screen)) + waiver_candidates(focus_uid, players_catalog, model_inputs, max(1, a.waiver_screen))
    screened = [evaluate_row(x, focus_uid, dl, v13, rosteraware, model_inputs, baseline_lineups, baseline_quick, a.quick_sims, a.seed) for x in raw]
    screened.sort(key=lambda x: (bool(x.get("actionable")), sf(x.get("team_improvement_score"))), reverse=True)

    confirm_n = min(max(0, a.confirm_top), len(screened))
    confirmed_ids = set(id(x) for x in screened[:confirm_n])
    if confirm_n and a.confirm_sims > a.quick_sims:
        baseline_deep = dl.simulate_from_lineups(simmod, league, rosters, users, raw_schedule, baseline_lineups, a.confirm_sims, a.seed)
        confirmed = [rerun_candidate(x, focus_uid, dl, v13, rosteraware, model_inputs, baseline_lineups, baseline_deep, a.confirm_sims, a.seed) for x in screened[:confirm_n]]
        screened = confirmed + screened[confirm_n:]
        screened.sort(key=lambda x: (bool(x.get("actionable")), sf(x.get("team_improvement_score"))), reverse=True)

    actionable = [x for x in screened if x.get("actionable")]
    trade_recs = [x for x in actionable if x.get("channel") == "TRADE"][:5]
    waiver_recs = [x for x in actionable if x.get("channel") == "WAIVER"][:5]
    best = actionable[0] if actionable else None
    hold = {
        "channel": "HOLD", "description": "Hold current roster", "team_improvement_score": 0.0,
        "actionable": True, "simulation": {"focus_delta": {"expected_wins": 0.0, "expected_points_for": 0.0, "playoff_probability": 0.0, "bye_probability": 0.0, "championship_probability": 0.0},
                                               "strategic": {"market_dynasty_delta": 0.0, "base_franchise_value_delta": 0.0, "break_glass_delta": 0.0}}
    }
    recommendation = best if best and sf(best.get("team_improvement_score")) > 0 else hold
    cc = team_doc(focus_uid, "command_center")
    output = {
        "model_version": MODEL_VERSION,
        "generated_for_user_id": focus_uid,
        "team_name": cc.get("focal_team") or franchise_row(focus_uid).get("team_name"),
        "team_state": state_weights(focus_uid)[0],
        "recommended_action": recommendation,
        "hold_benchmark": hold,
        "best_trade_options": trade_recs,
        "best_waiver_options": waiver_recs,
        "top_cross_channel_options": actionable[:10],
        "search_summary": {
            "trade_candidates_screened": sum(1 for x in screened if x.get("channel") == "TRADE"),
            "waiver_candidates_screened": sum(1 for x in screened if x.get("channel") == "WAIVER"),
            "actionable_candidates": len(actionable),
            "quick_sims": a.quick_sims,
            "deep_confirm_sims": a.confirm_sims if confirm_n and a.confirm_sims > a.quick_sims else 0,
            "deep_confirmed_candidates": confirm_n if a.confirm_sims > a.quick_sims else 0,
        },
        "policy": {
            "channels_compared_on_common_objective": True,
            "hold_is_explicit_benchmark": True,
            "roster_aware_trade_resolution": True,
            "waiver_adds_include_endogenous_optimal_drop": True,
            "forced_cuts_included_in_simulation_and_value": True,
            "trade_acceptance_is_heuristic_not_probability": True,
            "top_candidates_deep_confirmed": True,
            "canonical_state_mutated": False,
            "automatic_multi_step_transactions": False,
        },
    }
    Path(a.output).parent.mkdir(parents=True, exist_ok=True)
    Path(a.output).write_text(json.dumps(output, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({
        "model_version": MODEL_VERSION,
        "team": output["team_name"],
        "recommended_action": recommendation.get("description"),
        "score": recommendation.get("team_improvement_score"),
        "actionable_candidates": len(actionable),
        "output": a.output,
    }, indent=2))


if __name__ == "__main__":
    main()
