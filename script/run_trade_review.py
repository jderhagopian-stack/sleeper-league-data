#!/usr/bin/env python3
"""FSFFL GM 3.0 Trade Review 1.0.

Retrospective bilateral evaluation of a completed trade. This is a thin
orchestration layer over the canonical Decision Lab, Simulator 1.0, GM 3.0
strategic valuations, and roster-aware resolution. It does not introduce a
separate intelligence model.

The review answers a different question from the Trade Decision Report:
not "should one team accept?", but "what did this completed transaction do for
both franchises, who won each lens, and was it state-rational for both sides?"
Canonical league state is read-only; the transaction is applied ephemerally to
the supplied pre-trade baseline.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
from typing import Any, Dict, List

DATA = Path("data")
SCRIPT = Path(__file__).resolve().parent
MODEL_VERSION = "FSFFL-GM-Trade-Review-1.0"
DEFAULT_SIMS = 1000
DEFAULT_SEED = 20260821


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
    a, b = uids
    ua = sf(rows[a].get("state_aware_utility_delta")); ub = sf(rows[b].get("state_aware_utility_delta"))
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


def main():
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
    participants = [str(x) for x in (scenario.get("participant_user_ids") or transaction_participants(actions))]
    if len(set(participants)) != 2:
        raise ValueError("Trade Review 1.0 requires exactly two participant user ids")

    dl = load_module(SCRIPT / "run_roster_decision_lab.py", "trade_review_dl")
    v13 = load_module(SCRIPT / "run_trade_market_sweep_v13.py", "trade_review_v13")
    rosteraware = load_module(SCRIPT / "roster_aware_trade.py", "trade_review_roster")
    teamlab = load_module(SCRIPT / "run_team_improvement_lab.py", "trade_review_teamlab")

    model_inputs = dl.load_model_inputs()
    simmod, league, canonical_rosters, users, players, season, projections, raw_schedule = model_inputs
    hypothetical, pick_transfers = dl.apply_actions(canonical_rosters, actions)
    touched = sorted(set(participants))
    legal, resolutions, cut_actions = rosteraware.legalize_trade_rosters(
        dl, canonical_rosters, hypothetical, touched, league, players
    )
    effective_actions = list(actions) + list(cut_actions)

    baseline_lineups = dl.load_cached_lineups(season)
    hypothetical_lineups, reoptimized = v13.fast_reoptimize_touched_lineups(
        dl, simmod, baseline_lineups, legal, touched, league, users, players, projections
    )
    baseline = dl.simulate_from_lineups(
        simmod, league, canonical_rosters, users, raw_schedule, baseline_lineups, args.sims, args.seed
    )
    post = dl.simulate_from_lineups(
        simmod, league, legal, users, raw_schedule, hypothetical_lineups, args.sims, args.seed
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
        },
        "policy": {
            "same_core_intelligence_as_trade_decision_and_team_improvement": True,
            "separate_retrospective_orchestration_layer": True,
            "both_sides_evaluated_symmetrically": True,
            "roster_aware_resolution": True,
            "forced_cuts_included_in_simulation_and_value": True,
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
        "output": str(out),
    }, indent=2))


if __name__ == "__main__":
    main()
