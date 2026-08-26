#!/usr/bin/env python3
"""FSFFL GM 3.0 Trade Review 1.1.

Retrospective bilateral evaluation of a completed trade. This is a thin
orchestration layer over the canonical Decision Lab, Simulator 1.0, GM 3.0
strategic valuations, and roster-aware resolution. It does not introduce a
separate intelligence model.

Version 1.1 adds conditional second-stage forced-cut optimization. When a trade
creates an incremental active-roster cut, the retention-cost model prescreens
the three least-costly incumbent cuts. Those candidate legal rosters are then
simulated and the cut that leaves the affected franchise with the strongest
state-aware outcome is selected. A close top-two screen is confirmed at the
full requested simulation depth. Trades that require no cut incur no extra
candidate simulations.
"""
from __future__ import annotations

import argparse
import copy
import importlib.util
import json
from pathlib import Path
from typing import Any, Dict, List

DATA = Path("data")
SCRIPT = Path(__file__).resolve().parent
MODEL_VERSION = "FSFFL-GM-Trade-Review-1.1"
DEFAULT_SIMS = 1000
DEFAULT_SEED = 20260821
CUT_SCREEN_SIMS = 250
CUT_CONFIRM_MARGIN = 100.0


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {path}")
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


def dlt(before, after):
    return round(sf(after) - sf(before), 5)


def transaction_participants(actions: List[Dict[str, Any]]) -> List[str]:
    out = set()
    for a in actions:
        if str(a.get("type") or "").lower() == "trade":
            if a.get("from_user_id") is not None:
                out.add(str(a["from_user_id"]))
            if a.get("to_user_id") is not None:
                out.add(str(a["to_user_id"]))
    return sorted(out)


def team_state(teamlab, uid: str) -> str:
    try:
        return str(teamlab.state_weights(str(uid))[0])
    except Exception:
        return "unknown"


def side_sim(dl, teamlab, uid, before, after, effective_actions, resolution):
    sim = {
        "focus_before": before,
        "focus_after": after,
        "focus_delta": {
            "expected_wins": dlt(before.get("expected_wins"), after.get("expected_wins")),
            "expected_points_for": dlt(before.get("expected_points_for"), after.get("expected_points_for")),
            "playoff_probability": dlt(before.get("playoff_probability"), after.get("playoff_probability")),
            "bye_probability": dlt(before.get("bye_probability"), after.get("bye_probability")),
            "championship_probability": dlt(before.get("championship_probability"), after.get("championship_probability")),
        },
        "strategic": dl.strategic_summary(str(uid), effective_actions),
        "roster_resolution": resolution,
    }
    sim["state_aware_utility_delta"] = teamlab.unified_score(str(uid), sim)
    sim["contender_guardrail"] = teamlab.contender_guardrail(str(uid), sim)
    return sim


def cut_action(uid: str, profile: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "type": "cut",
        "user_id": str(uid),
        "players": [str(profile["player_id"])],
        "automatic_roster_legalization": True,
        "baseline_aware_incremental_cut": True,
        "simulation_selected_cut": True,
    }


def roster_with_selected_cuts(dl, hypothetical, touched, resolutions, chosen_profiles):
    out = copy.deepcopy(hypothetical)
    by_uid, _ = dl.roster_maps(out)
    actions = []
    for uid in touched:
        rr = resolutions.get(uid) or {}
        n = int(rr.get("required_cuts") or 0)
        if n <= 0:
            continue
        # Trade Review 1.1 simulates alternatives only for the common one-cut
        # case. Multi-cut cases retain the resolver's already legal prescreen.
        if n == 1 and uid in chosen_profiles:
            profiles = [chosen_profiles[uid]]
        else:
            profiles = list(rr.get("selected_cuts") or [])
        roster = by_uid.get(str(uid))
        if roster is None:
            raise RuntimeError(f"Missing hypothetical roster for cut selection: {uid}")
        for profile in profiles:
            dl.remove_player(roster, str(profile["player_id"]))
            actions.append(cut_action(uid, profile))
    return out, actions


def candidate_result(dl, v13, teamlab, simmod, league, hypothetical, touched, resolutions,
                     candidate_uid, candidate_profile, fixed_profiles, users, players, projections,
                     raw_schedule, baseline_lineups, baseline_idx, sims, seed):
    chosen = dict(fixed_profiles)
    chosen[str(candidate_uid)] = candidate_profile
    candidate_rosters, candidate_cut_actions = roster_with_selected_cuts(
        dl, hypothetical, touched, resolutions, chosen
    )
    lineups, _ = v13.fast_reoptimize_touched_lineups(
        dl, simmod, baseline_lineups, candidate_rosters, touched,
        league, users, players, projections
    )
    post = dl.simulate_from_lineups(
        simmod, league, candidate_rosters, users, raw_schedule, lineups, sims, seed
    )
    pidx = dl.team_index(post)
    uid = str(candidate_uid)
    if uid not in baseline_idx or uid not in pidx:
        raise RuntimeError(f"Cut candidate simulation missing participant {uid}")
    effective_actions = list(CURRENT_TRADE_ACTIONS) + list(candidate_cut_actions)
    rr = copy.deepcopy(resolutions.get(uid) or {})
    rr["selected_cuts"] = [candidate_profile]
    rr["cut_base_franchise_value"] = round(sf(candidate_profile.get("base_franchise_value")), 2)
    rr["cut_market_dynasty_value"] = round(sf(candidate_profile.get("market_dynasty")), 2)
    row = side_sim(dl, teamlab, uid, baseline_idx[uid], pidx[uid], effective_actions, rr)
    return {
        "player_id": str(candidate_profile["player_id"]),
        "name": candidate_profile.get("name"),
        "retention_cost": sf(candidate_profile.get("retention_cost")),
        "state_aware_utility_delta": sf(row.get("state_aware_utility_delta")),
        "expected_wins_delta": sf((row.get("focus_delta") or {}).get("expected_wins")),
        "championship_probability_delta": sf((row.get("focus_delta") or {}).get("championship_probability")),
        "expected_points_delta": sf((row.get("focus_delta") or {}).get("expected_points_for")),
        "market_dynasty_delta": sf((row.get("strategic") or {}).get("market_dynasty_delta")),
        "base_franchise_value_delta": sf((row.get("strategic") or {}).get("base_franchise_value_delta")),
        "profile": candidate_profile,
    }


def optimize_forced_cuts(dl, v13, teamlab, simmod, league, hypothetical, touched, resolutions,
                         users, players, projections, raw_schedule, baseline_lineups,
                         args, baseline_full):
    """Simulate up to three prescreened cuts only when a trade requires one."""
    analysis = {}
    chosen_profiles = {}
    screen_sims = min(int(args.sims), CUT_SCREEN_SIMS)
    if screen_sims == int(args.sims):
        screen_baseline = baseline_full
    else:
        screen_baseline = dl.simulate_from_lineups(
            simmod, league, CANONICAL_ROSTERS, users, raw_schedule,
            baseline_lineups, screen_sims, args.seed
        )
    screen_bidx = dl.team_index(screen_baseline)

    for uid in touched:
        rr = resolutions.get(uid) or {}
        n = int(rr.get("required_cuts") or 0)
        shortlist = list(rr.get("cut_candidate_shortlist") or [])[:3]
        if n != 1 or not shortlist:
            continue

        screened = []
        fixed = dict(chosen_profiles)
        for profile in shortlist:
            screened.append(candidate_result(
                dl, v13, teamlab, simmod, league, hypothetical, touched, resolutions,
                uid, profile, fixed, users, players, projections, raw_schedule,
                baseline_lineups, screen_bidx, screen_sims, args.seed
            ))
        screened.sort(key=lambda x: (sf(x.get("state_aware_utility_delta")), -sf(x.get("retention_cost"))), reverse=True)
        selected = screened[0]
        confirmation_triggered = False
        confirmed = []
        if len(screened) > 1 and int(args.sims) > screen_sims:
            margin = sf(screened[0].get("state_aware_utility_delta")) - sf(screened[1].get("state_aware_utility_delta"))
            if margin < CUT_CONFIRM_MARGIN:
                confirmation_triggered = True
                full_bidx = dl.team_index(baseline_full)
                for row in screened[:2]:
                    confirmed.append(candidate_result(
                        dl, v13, teamlab, simmod, league, hypothetical, touched, resolutions,
                        uid, row["profile"], fixed, users, players, projections, raw_schedule,
                        baseline_lineups, full_bidx, int(args.sims), args.seed
                    ))
                confirmed.sort(key=lambda x: (sf(x.get("state_aware_utility_delta")), -sf(x.get("retention_cost"))), reverse=True)
                selected = confirmed[0]

        chosen_profiles[str(uid)] = selected["profile"]
        analysis[str(uid)] = {
            "method": "retention_cost_top3_then_simulate_legal_rosters",
            "screening_sims": screen_sims,
            "candidate_count": len(screened),
            "candidates": [{k:v for k,v in x.items() if k != "profile"} for x in screened],
            "confirmation_triggered": confirmation_triggered,
            "confirmation_sims": int(args.sims) if confirmation_triggered else 0,
            "confirmed_candidates": [{k:v for k,v in x.items() if k != "profile"} for x in confirmed],
            "selected_cut": {k:v for k,v in selected.items() if k != "profile"},
        }
    return chosen_profiles, analysis


def winner(rows: Dict[str, Dict[str, Any]], path: str, higher=True):
    vals = []
    for uid, row in rows.items():
        cur = row
        for key in path.split("."):
            cur = (cur or {}).get(key)
        vals.append((sf(cur), uid))
    if not vals:
        return None
    vals.sort(reverse=higher)
    if len(vals) > 1 and abs(vals[0][0] - vals[1][0]) < 1e-9:
        return "TIE"
    return vals[0][1]


def label(uid, rows):
    row = rows.get(str(uid)) or {}
    return row.get("team_name") or row.get("manager") or str(uid)


def assessment(rows):
    uids = list(rows)
    if len(uids) != 2:
        return {"classification": "MULTI_PARTY", "summary": "Trade review currently expects two franchises."}
    dynasty_winner = winner(rows, "strategic.market_dynasty_delta")
    title_winner = winner(rows, "focus_delta.championship_probability")
    utility_winner = winner(rows, "state_aware_utility_delta")
    rational = [uid for uid in uids if sf(rows[uid].get("state_aware_utility_delta")) > 0]
    if len(rational) == 2:
        cls = "WIN_WIN_STATE_RATIONAL"
        summary = "Both teams improve on their own state-aware objective, even though they may win different value lenses."
    elif len(rational) == 1:
        cls = "ONE_SIDED_STATE_AWARE"
        summary = f"Only {label(rational[0], rows)} improves on its own state-aware objective in the modeled baseline."
    else:
        cls = "MUTUALLY_COSTLY"
        summary = "Neither side improves on its own state-aware objective in the modeled baseline."
    return {
        "classification": cls,
        "summary": summary,
        "pure_dynasty_value_winner_user_id": dynasty_winner,
        "current_title_equity_winner_user_id": title_winner,
        "state_aware_utility_winner_user_id": utility_winner,
        "pure_dynasty_value_winner": "TIE" if dynasty_winner == "TIE" else label(dynasty_winner, rows),
        "current_title_equity_winner": "TIE" if title_winner == "TIE" else label(title_winner, rows),
        "state_aware_utility_winner": "TIE" if utility_winner == "TIE" else label(utility_winner, rows),
        "both_sides_state_rational": len(rational) == 2,
    }


CURRENT_TRADE_ACTIONS = []
CANONICAL_ROSTERS = []


def main():
    global CURRENT_TRADE_ACTIONS, CANONICAL_ROSTERS
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", required=True)
    ap.add_argument("--sims", type=int, default=DEFAULT_SIMS)
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()
    if args.sims < 100:
        raise ValueError("--sims must be at least 100")

    scenario_path = Path(args.scenario)
    scenario = load_json(scenario_path, {}) or {}
    actions = scenario.get("actions") or []
    CURRENT_TRADE_ACTIONS = list(actions)
    participants = [str(x) for x in (scenario.get("participant_user_ids") or transaction_participants(actions))]
    if len(set(participants)) != 2:
        raise ValueError("Trade Review 1.1 requires exactly two participant user ids")

    dl = load_module(SCRIPT / "run_roster_decision_lab.py", "trade_review_dl")
    v13 = load_module(SCRIPT / "run_trade_market_sweep_v13.py", "trade_review_v13")
    rosteraware = load_module(SCRIPT / "roster_aware_trade.py", "trade_review_roster")
    teamlab = load_module(SCRIPT / "run_team_improvement_lab.py", "trade_review_teamlab")

    model_inputs = dl.load_model_inputs()
    simmod, league, canonical_rosters, users, players, season, projections, raw_schedule = model_inputs
    CANONICAL_ROSTERS = canonical_rosters
    hypothetical, pick_transfers = dl.apply_actions(canonical_rosters, actions)
    touched = sorted(set(participants))

    # Fast prescreen legalization produces the candidate shortlist and a legal
    # fallback. It is not yet the final cut choice when exactly one cut is due.
    _, resolutions, _ = rosteraware.legalize_trade_rosters(
        dl, canonical_rosters, hypothetical, touched, league, players
    )

    baseline_lineups = dl.load_cached_lineups(season)
    baseline = dl.simulate_from_lineups(
        simmod, league, canonical_rosters, users, raw_schedule,
        baseline_lineups, args.sims, args.seed
    )

    chosen_profiles, cut_selection_analysis = optimize_forced_cuts(
        dl, v13, teamlab, simmod, league, hypothetical, touched, resolutions,
        users, players, projections, raw_schedule, baseline_lineups, args, baseline
    )

    legal, cut_actions = roster_with_selected_cuts(
        dl, hypothetical, touched, resolutions, chosen_profiles
    )

    # Rewrite resolution metadata to the simulation-selected cut so the JSON,
    # report, strategic valuation, and final simulated roster all agree.
    for uid, profile in chosen_profiles.items():
        rr = resolutions.get(uid) or {}
        rr["selected_cuts"] = [profile]
        rr["cut_base_franchise_value"] = round(sf(profile.get("base_franchise_value")), 2)
        rr["cut_market_dynasty_value"] = round(sf(profile.get("market_dynasty")), 2)
        rr["cut_selection_method"] = "retention_cost_top3_then_simulate_legal_rosters"
        rr["simulation_selected_cut"] = True
        resolutions[uid] = rr

    effective_actions = list(actions) + list(cut_actions)
    hypothetical_lineups, reoptimized = v13.fast_reoptimize_touched_lineups(
        dl, simmod, baseline_lineups, legal, touched, league, users, players, projections
    )
    post = dl.simulate_from_lineups(
        simmod, league, legal, users, raw_schedule,
        hypothetical_lineups, args.sims, args.seed
    )
    bidx, pidx = dl.team_index(baseline), dl.team_index(post)

    rows = {}
    for uid in touched:
        if uid not in bidx or uid not in pidx:
            raise ValueError(f"Participant {uid} missing from simulation output")
        row = side_sim(dl, teamlab, uid, bidx[uid], pidx[uid], effective_actions, resolutions.get(uid) or {})
        row.update({
            "user_id": uid,
            "manager": bidx[uid].get("manager"),
            "team_name": bidx[uid].get("team_name"),
            "team_state": team_state(teamlab, uid),
        })
        rows[uid] = row

    report = {
        "model_version": MODEL_VERSION,
        "scenario_id": scenario.get("scenario_id") or scenario_path.stem,
        "description": scenario.get("description"),
        "transaction_status": scenario.get("transaction_status") or "completed",
        "participant_user_ids": touched,
        "actions": actions,
        "pick_transfers": pick_transfers,
        "automatic_roster_cut_actions": cut_actions,
        "cut_selection_analysis": cut_selection_analysis,
        "team_reviews": rows,
        "bilateral_assessment": assessment(rows),
        "simulation": {
            "n_sims": args.sims,
            "seed": args.seed,
            "common_random_numbers": True,
            "simulator_model_version": baseline.get("model_version"),
            "decision_lab_model_version": dl.MODEL_VERSION,
            "roster_resolution_model_version": rosteraware.MODEL_VERSION,
            "teams_reoptimized": reoptimized,
            "baseline_semantics": "pre_trade_canonical_snapshot_plus_ephemeral_completed_transaction",
            "forced_cut_screen_sims": min(int(args.sims), CUT_SCREEN_SIMS),
        },
        "policy": {
            "same_core_intelligence_as_trade_decision_and_team_improvement": True,
            "separate_retrospective_orchestration_layer": True,
            "both_sides_evaluated_symmetrically": True,
            "roster_aware_resolution": True,
            "forced_cuts_included_in_simulation_and_value": True,
            "forced_cut_candidates_simulated_when_required": True,
            "forced_cut_candidate_shortlist_size": 3,
            "forced_cut_deep_confirmation_when_close": True,
            "no_extra_cut_simulations_when_no_cut_required": True,
            "hold_baseline_is_pre_trade_state": True,
            "counteroffer_search_intentionally_omitted": True,
            "acceptance_probability_intentionally_omitted": True,
            "canonical_state_mutated": False,
        },
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({
        "model_version": MODEL_VERSION,
        "scenario_id": report["scenario_id"],
        "classification": report["bilateral_assessment"]["classification"],
        "state_aware_winner": report["bilateral_assessment"]["state_aware_utility_winner"],
        "cut_optimizations": len(cut_selection_analysis),
        "output": str(out),
    }, indent=2))


if __name__ == "__main__":
    main()
